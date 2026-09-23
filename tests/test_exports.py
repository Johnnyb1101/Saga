import datetime as dt
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import cli, db, export, writes


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.output = Path(self.workspace.name)
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_exports_keep_unicode_records_and_review_period(self):
        # Pin query date arguments as well as the Python clock used by exports.
        from saga import reads

        day = dt.date(2020, 2, 29)
        for offset, title in ((-1, "Overdue"), (0, "Due today"), (1, "Café task")):
            writes.add_task(self.con, title, "work", due_date=(day + dt.timedelta(days=offset)).isoformat())
        writes.add_completion(self.con, "work", outcome="Résumé reviewed", flagged=True,
                              completed_at="2020-02-29 23:59:59")
        writes.add_completion(self.con, "work", outcome="Outside period", flagged=True,
                              completed_at="2020-03-01 00:00:00")
        queries = {name: getattr(reads, name) for name in
                   ("overdue", "due_today", "due_soon", "upcoming_deadlines")}
        with patch.object(export.dt, "date", wraps=dt.date) as clock, \
                patch.object(reads, "overdue", side_effect=lambda con: queries["overdue"](con, on=day.isoformat())), \
                patch.object(reads, "due_today", side_effect=lambda con: queries["due_today"](con, on=day.isoformat())), \
                patch.object(reads, "due_soon", side_effect=lambda con, days: queries["due_soon"](con, days, on=day.isoformat())), \
                patch.object(reads, "upcoming_deadlines", side_effect=lambda con, days: queries["upcoming_deadlines"](con, days, on=day.isoformat())):
            clock.today.return_value = day
            files = export.write_all(self.con, self.output, since=day.isoformat(), until=day.isoformat())
        self.assertEqual({p.name for p in files}, {"brief.json", "brief.md", "review.json", "manifest.json"})
        brief = json.loads((self.output / "brief.json").read_text(encoding="utf-8"))
        review = json.loads((self.output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(brief["schema_version"], 1)
        self.assertEqual(brief["date"], "2020-02-29")
        for section, title in (("overdue", "Overdue"), ("due_today", "Due today"), ("due_soon", "Café task")):
            self.assertEqual([r["title"] for r in brief[section]], [title])
        self.assertEqual(review["schema_version"], 2)
        self.assertEqual((review["since"], review["until"]), ("2020-02-29", "2020-02-29"))
        self.assertEqual(review["volume"]["completions"], 1)
        self.assertEqual([r["outcome"] for r in review["flagged"]], ["Résumé reviewed"])
        markdown = (self.output / "brief.md").read_text(encoding="utf-8")
        for text in ("# Brief - 2020-02-29", "## Overdue", "## Due today", "## Coming up", "Café task"):
            self.assertIn(text, markdown)

    def test_empty_export_is_valid_and_has_quiet_day_message(self):
        export.write_all(self.con, self.output)
        review = json.loads((self.output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["volume"]["completions"], 0)
        self.assertEqual(review["flagged"], [])
        self.assertIn("Nothing due and no deadlines ahead.",
                      (self.output / "brief.md").read_text(encoding="utf-8"))

    def test_freshness_uses_content_date_not_modification_time(self):
        path = self.output / "brief.json"
        self.assertTrue(export.is_stale(self.output))
        for content in ("{broken", "{}", '{"date":"2020-02-28"}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with patch.object(export.dt, "date", wraps=dt.date) as clock:
                    clock.today.return_value = dt.date(2020, 2, 29)
                    self.assertTrue(export.is_stale(self.output))
        with patch.object(export.dt, "date", wraps=dt.date) as clock:
            clock.today.return_value = dt.date(2020, 2, 29)
            export.write_all(self.con, self.output)
            for path in self.output.iterdir():
                os.utime(path, (1, 1))
            self.assertFalse(export.is_stale(self.output))

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.output.iterdir()}

    def test_staging_failure_keeps_all_previous_files_and_cleans_temps(self):
        export.write_all(self.con, self.output)
        before = self.snapshot()
        with patch.object(export.os, "fsync", side_effect=OSError("injected disk failure")), \
                self.assertRaisesRegex(export.ExportError, "injected disk failure"):
            export.write_all(self.con, self.output)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(export.is_stale(self.output))

    def test_failure_between_replacements_leaves_complete_files_and_retry_recovers(self):
        export.write_all(self.con, self.output)
        before = self.snapshot()
        writes.add_task(self.con, "Changed brief", "work", due_date=dt.date.today().isoformat())
        replace = os.replace
        calls = []

        def fail_second(source, destination):
            calls.append(destination.name)
            if len(calls) == 2:
                raise PermissionError("injected sharing violation")
            replace(source, destination)

        with patch.object(export.os, "replace", side_effect=fail_second), \
                self.assertRaisesRegex(export.ExportError, "sharing violation"):
            export.write_all(self.con, self.output)
        after = self.snapshot()
        self.assertEqual(set(after), set(before))
        self.assertEqual(after["manifest.json"], before["manifest.json"])
        self.assertEqual(after["brief.md"], before["brief.md"])
        self.assertEqual(after["review.json"], before["review.json"])
        self.assertEqual(json.loads(after["brief.json"])["due_today"][0]["title"], "Changed brief")
        self.assertTrue(export.is_stale(self.output))
        export.write_all(self.con, self.output)
        self.assertFalse(export.is_stale(self.output))

    def test_each_file_exists_in_full_before_replace_and_manifest_is_last(self):
        replace = os.replace
        names = []

        def inspect(source, destination):
            content = Path(source).read_bytes()
            self.assertTrue(content)
            if destination.suffix == ".json":
                self.assertIsInstance(json.loads(content), dict)
            names.append(destination.name)
            replace(source, destination)

        with patch.object(export.os, "replace", side_effect=inspect):
            export.write_all(self.con, self.output)
        self.assertEqual(names, ["brief.json", "brief.md", "review.json", "manifest.json"])
        self.assertFalse(export.is_stale(self.output))

    def test_manifest_replacement_failure_is_detected_and_retry_recovers(self):
        export.write_all(self.con, self.output)
        old_manifest = (self.output / "manifest.json").read_bytes()
        writes.add_completion(self.con, "work", outcome="New record")
        replace = os.replace

        def fail_manifest(source, destination):
            if destination.name == "manifest.json":
                raise OSError("manifest blocked")
            replace(source, destination)

        with patch.object(export.os, "replace", side_effect=fail_manifest), \
                self.assertRaisesRegex(export.ExportError, "manifest blocked"):
            export.write_all(self.con, self.output)
        self.assertEqual((self.output / "manifest.json").read_bytes(), old_manifest)
        self.assertTrue(export.is_stale(self.output))
        self.assertFalse(list(self.output.glob("*.tmp")))
        export.write_all(self.con, self.output)
        self.assertFalse(export.is_stale(self.output))

    def test_cleanup_failure_is_reported(self):
        unlink = Path.unlink

        def fail_temp(path, *args, **kwargs):
            if path.suffix == ".tmp":
                raise PermissionError("cleanup blocked")
            return unlink(path, *args, **kwargs)

        with patch.object(export.os, "fsync", side_effect=OSError("write blocked")), \
                patch.object(Path, "unlink", fail_temp), \
                self.assertRaisesRegex(export.ExportError, "cleanup failed"):
            export.write_all(self.con, self.output)

    def test_missing_corrupt_and_mixed_files_are_stale_on_same_day(self):
        for name in (*export.EXPORT_NAMES, "manifest.json"):
            with self.subTest(name=name):
                export.write_all(self.con, self.output)
                (self.output / name).unlink()
                self.assertTrue(export.is_stale(self.output))
                export.write_all(self.con, self.output)
                (self.output / name).write_text("corrupt", encoding="utf-8")
                self.assertTrue(export.is_stale(self.output))

    def test_unexpected_json_types_are_stale_even_with_matching_hashes(self):
        for name in ("brief.json", "review.json", "manifest.json"):
            for content in (b"[]", b"null", b"42", b'"string"'):
                with self.subTest(name=name, content=content):
                    export.write_all(self.con, self.output)
                    (self.output / name).write_bytes(content)
                    if name != "manifest.json":
                        manifest_path = self.output / "manifest.json"
                        manifest = json.loads(manifest_path.read_bytes())
                        manifest["files"][name] = hashlib.sha256(content).hexdigest()
                        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    self.assertTrue(export.is_stale(self.output))

    def test_nonfinite_numbers_cannot_replace_good_exports(self):
        export.write_all(self.con, self.output)
        before = self.snapshot()
        writes.add_measure(self.con, "units")
        writes.add_completion(self.con, "work", measure="units", quantity=float("inf"))
        with self.assertRaisesRegex(export.ExportError, "JSON"):
            export.write_all(self.con, self.output)
        self.assertEqual(self.snapshot(), before)

    def test_caller_transaction_is_not_committed(self):
        self.con.execute("INSERT INTO tasks(title, category) VALUES ('Uncommitted', 'work')")
        export.write_all(self.con, self.output)
        self.assertTrue(self.con.in_transaction)
        self.con.rollback()
        self.assertEqual(self.con.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_database_snapshot_excludes_a_concurrent_commit(self):
        path = self.output / "snapshot.db"
        db.init_db(path)
        from contextlib import closing

        with closing(db.connect(path)) as reader, closing(db.connect(path)) as writer:
            reader.execute("PRAGMA journal_mode=WAL")
            build_review = export.build_review

            def change_between_reports(con, since, until):
                writes.add_completion(writer, "work", outcome="Concurrent commit")
                return build_review(con, since, until)

            with patch.object(export, "build_review", side_effect=change_between_reports):
                export.write_all(reader, self.output / "snapshot_exports")
            review = json.loads((self.output / "snapshot_exports" / "review.json").read_bytes())
            self.assertEqual(review["volume"]["completions"], 0)
            self.assertEqual(writer.execute("SELECT count(*) FROM completions").fetchone()[0], 1)

    def test_post_write_export_error_says_database_change_was_saved(self):
        from argparse import Namespace

        args = Namespace(db=db.DB_PATH)
        with patch.object(export, "write_all", side_effect=export.ExportError("injected")), \
                self.assertRaisesRegex(export.ExportError, "Database change was saved"):
            cli.maybe_export(args, self.con)
