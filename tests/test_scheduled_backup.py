import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import db, writes
from saga import scheduled_backup as scheduled


class ScheduledBackupTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.source = self.root / "source.db"
        self.output = self.root / "backups"
        db.init_db(self.source)

    def test_restore_preserves_archive_and_leaves_source_unchanged(self):
        with contextlib.closing(db.connect(self.source)) as con:
            task = writes.add_task(con, "Invented task", "work")
            writes.complete_task(con, task, outcome="Invented result", flagged=True)
            expected = list(con.iterdump())
        before = self.source.read_bytes()
        snapshot, removed = scheduled.run(self.source, self.output)
        with contextlib.closing(db.connect(snapshot)) as con:
            self.assertEqual(list(con.iterdump()), expected)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(removed, [])
        self.assertFalse((snapshot.parent / "run.lock").exists())

    def test_retention_is_opt_in_and_only_prunes_registered_snapshots(self):
        first, _ = scheduled.run(self.source, self.output)
        second, _ = scheduled.run(self.source, self.output)
        manual = db.backup(self.source, self.output)
        untracked = first.parent / ("snapshot-" + "a" * 32 + ".db")
        untracked.write_bytes(b"untracked")
        migration = first.parent / "source.bak"
        migration.write_bytes(b"migration")
        newest, removed = scheduled.run(self.source, self.output, keep=1)
        self.assertEqual(removed, [first.name, second.name])
        self.assertTrue(newest.exists())
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(manual.exists())
        self.assertEqual(untracked.read_bytes(), b"untracked")
        self.assertEqual(migration.read_bytes(), b"migration")

    def test_same_named_sources_are_isolated(self):
        first, _ = scheduled.run(self.source, self.output)
        other = self.root / "other" / self.source.name
        other.parent.mkdir()
        db.init_db(other)
        second, _ = scheduled.run(other, self.output, keep=1)
        self.assertNotEqual(first.parent, second.parent)
        self.assertTrue(first.exists())

    def test_invalid_retention_fails_before_creating_output(self):
        for keep in (0, -1, True, 1.5):
            with self.subTest(keep=keep), self.assertRaises(ValueError):
                scheduled.run(self.source, self.output, keep)
        self.assertFalse(self.output.exists())

    def test_missing_source_does_not_create_database(self):
        missing = self.root / "missing.db"
        with self.assertRaises(FileNotFoundError):
            scheduled.run(missing, self.output)
        self.assertFalse(missing.exists())
        self.assertFalse(self.output.exists())

    def test_failed_backup_preserves_previous_snapshots(self):
        first, _ = scheduled.run(self.source, self.output)
        manifest = (first.parent / "manifest.json").read_bytes()
        with (patch.object(db, "backup", side_effect=sqlite3.OperationalError("unavailable")),
              self.assertRaises(sqlite3.OperationalError)):
            scheduled.run(self.source, self.output, keep=1)
        self.assertTrue(first.exists())
        self.assertEqual((first.parent / "manifest.json").read_bytes(), manifest)

    def test_failed_restore_does_not_register_or_prune(self):
        first, _ = scheduled.run(self.source, self.output)
        before = set(first.parent.iterdir())
        with (patch.object(scheduled, "verify_restore", side_effect=ValueError("bad restore")),
              self.assertRaisesRegex(ValueError, "bad restore")):
            scheduled.run(self.source, self.output, keep=1)
        self.assertEqual(set(first.parent.iterdir()), before)
        retry, removed = scheduled.run(self.source, self.output, keep=1)
        self.assertTrue(retry.exists())
        self.assertEqual(removed, [first.name])

    def test_restore_rejects_foreign_key_violation(self):
        with contextlib.closing(sqlite3.connect(self.source)) as con:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("INSERT INTO tasks (title, category) VALUES ('Invented', 'missing')")
            con.commit()
        with self.assertRaisesRegex(ValueError, "foreign_key_check"):
            scheduled.run(self.source, self.output)
        self.assertEqual(list(self.output.rglob("*.db")), [])

    def test_old_schema_reports_version_and_releases_restore_file(self):
        with contextlib.closing(sqlite3.connect(self.source)) as con:
            con.execute("PRAGMA user_version=1")
        with self.assertRaisesRegex(ValueError, "Backup schema is 1; expected 3"):
            scheduled.run(self.source, self.output)
        self.assertEqual(list(self.output.rglob("*.db")), [])

    def test_linked_managed_directory_is_rejected(self):
        first, _ = scheduled.run(self.source, self.output)
        with (patch.object(Path, "is_junction", return_value=True),
              self.assertRaisesRegex(ValueError, "link or junction")):
            scheduled.run(self.source, self.output, keep=1)
        self.assertTrue(first.exists())

    def test_bad_manifest_and_path_traversal_prevent_backup(self):
        first, _ = scheduled.run(self.source, self.output)
        manifest = first.parent / "manifest.json"
        valid = json.loads(manifest.read_text())
        for data in ([], {**valid, "source": "wrong"},
                     {**valid, "snapshots": ["../source.db"]},
                     {**valid, "snapshots": [first.name, first.name]}):
            with self.subTest(data=data):
                manifest.write_text(json.dumps(data))
                with patch.object(db, "backup") as backup, self.assertRaises(ValueError):
                    scheduled.run(self.source, self.output, keep=1)
                backup.assert_not_called()
                self.assertTrue(first.exists())

    def test_missing_manifest_in_populated_directory_is_not_adopted(self):
        first, _ = scheduled.run(self.source, self.output)
        (first.parent / "manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "no manifest"):
            scheduled.run(self.source, self.output, keep=1)
        self.assertTrue(first.exists())

    def test_active_or_stale_lock_blocks_and_is_preserved(self):
        first, _ = scheduled.run(self.source, self.output)
        lock = first.parent / "run.lock"
        lock.write_text("123")
        with self.assertRaisesRegex(ValueError, "stale lock"):
            scheduled.run(self.source, self.output, keep=1)
        self.assertEqual(lock.read_text(), "123")
        self.assertTrue(first.exists())

    def test_failed_manifest_publication_preserves_old_backups_and_orphan(self):
        first, _ = scheduled.run(self.source, self.output)
        with (patch.object(scheduled.os, "replace", side_effect=OSError("blocked")),
              self.assertRaisesRegex(OSError, "blocked")):
            scheduled.run(self.source, self.output, keep=1)
        orphans = set(first.parent.glob("snapshot-*.db")) - {first}
        self.assertEqual(len(orphans), 1)
        self.assertEqual(list(first.parent.glob("*.tmp")), [])
        scheduled.run(self.source, self.output, keep=1)
        self.assertTrue(next(iter(orphans)).exists())

    def test_failed_pruning_keeps_new_backup_and_can_retry(self):
        first, _ = scheduled.run(self.source, self.output)
        unlink = Path.unlink

        def fail_old(path, *args, **kwargs):
            if path == first:
                raise PermissionError("old snapshot locked")
            return unlink(path, *args, **kwargs)

        with (patch.object(Path, "unlink", fail_old),
              self.assertRaisesRegex(PermissionError, "old snapshot locked")):
            scheduled.run(self.source, self.output, keep=1)
        self.assertEqual(len(list(first.parent.glob("snapshot-*.db"))), 2)
        scheduled.run(self.source, self.output, keep=1)
        self.assertEqual(len(list(first.parent.glob("snapshot-*.db"))), 1)

    def test_missing_registered_file_is_tolerated_after_partial_pruning(self):
        first, _ = scheduled.run(self.source, self.output)
        first.unlink()
        newest, _ = scheduled.run(self.source, self.output, keep=1)
        data = json.loads((newest.parent / "manifest.json").read_text())
        self.assertEqual(data["snapshots"], [newest.name])

    def test_cli_logs_success_and_failure_and_returns_status(self):
        log = self.root / "backup.log"
        args = ["--db", str(self.source), "--out", str(self.output), "--log", str(log)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduled.main(args), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(scheduled.main([*args, "--keep", "0"]), 1)
        text = log.read_text()
        self.assertIn("Verified backup and restore:", text)
        self.assertIn("ERROR Scheduled backup failed", text)


if __name__ == "__main__":
    unittest.main()
