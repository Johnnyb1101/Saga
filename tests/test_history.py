import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, export, reads, writes


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.path = Path(self.workspace.name) / "archive.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        writes.add_measure(self.con, "packages")
        writes.add_duty(self.con, "Operations")
        writes.add_duty(self.con, "Training")
        self.project = writes.add_project(self.con, "Original project")
        self.task = writes.add_task(self.con, "Original title", "work", self.project,
                                   due_date="2020-01-01", duty="Operations")
        self.original = writes.complete_task(
            self.con, self.task, outcome="Original outcome", measure="packages", quantity=2,
            flagged=True, completed_at="2020-01-02 12:00:00")

    def row(self, completion_id):
        return dict(self.con.execute("SELECT * FROM completions WHERE id=?", (completion_id,)).fetchone())

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            status = cli.main(["--db", str(self.path), *args])
        return status, output.getvalue(), errors.getvalue()

    def test_task_project_and_category_edits_cannot_rewrite_reports(self):
        before = self.row(self.original)
        self.assertIsNotNone(before["recorded_at"])
        self.con.execute("UPDATE tasks SET title='Changed', due_date='2020-02-01', category='home', duty='Training'")
        self.con.execute("UPDATE projects SET name='Changed project'")
        self.con.execute("UPDATE categories SET counts_toward_review=0 WHERE name='work'")
        self.con.commit()
        self.assertEqual(self.row(self.original), before)
        self.assertEqual(analytics.on_time_rate(self.con)[0]["pct"], 0)
        self.assertEqual(analytics.volume(self.con)["review_counting"], 1)
        flagged = analytics.flagged_work(self.con)[0]
        self.assertEqual((flagged["task_title"], flagged["project_name"], flagged["duty"]),
                         ("Original title", "Original project", "Operations"))
        self.assertEqual(flagged["snapshot_source"], "captured")
        corrected = writes.correct_completion(self.con, self.original, "Outcome detail", outcome="More detail")
        for field in ("task_title", "due_date", "project_name", "review_counting"):
            self.assertEqual(self.row(corrected)[field], before[field])

    def test_failed_correction_insert_leaves_original_current(self):
        before = self.row(self.original)
        self.con.executescript("""CREATE TRIGGER reject_revision AFTER INSERT ON completions
            WHEN NEW.supersedes_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'injected correction failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected correction failure"):
            writes.correct_completion(self.con, self.original, "Count", quantity=9)
        self.assertEqual([r["id"] for r in reads.completion_list(self.con)], [self.original])
        self.assertEqual(self.row(self.original), before)
        self.assertEqual(len(reads.completion_history(self.con, self.original)), 1)

    def test_corrections_preserve_originals_and_only_latest_counts(self):
        before = self.row(self.original)
        second = writes.correct_completion(self.con, self.original, "Count corrected", quantity=5)
        third = writes.correct_completion(self.con, second, "Period and role corrected",
                                          completed_at="2020-02-01 00:00:00", duty="Training")
        self.assertEqual(self.row(self.original), before)
        self.assertEqual(self.row(second)["quantity"], 5)
        self.assertEqual(self.row(third)["task_title"], "Original title")
        for identity in (self.original, second, third):
            self.assertEqual([r["id"] for r in reads.completion_history(self.con, identity)],
                             [self.original, second, third])
        self.assertEqual(analytics.volume(self.con)["completions"], 1)
        self.assertEqual(analytics.volume(self.con, until="2020-01-31")["completions"], 0)
        self.assertEqual(analytics.volume(self.con, duty="Operations")["completions"], 0)
        self.assertEqual(analytics.measure_totals(self.con)[0]["total"], 5)
        self.assertEqual(analytics.on_time_rate(self.con)[0]["evaluated"], 1)
        self.assertEqual(sum(r["completions"] for r in analytics.completions_by_category(self.con)), 1)
        self.assertEqual(reads.measure_usage(self.con)[0]["occasions"], 1)
        duties = {r["name"]: r["completions"] for r in reads.duty_usage(self.con)}
        self.assertEqual(duties, {"Operations": 0, "Training": 1})
        review = export.build_review(self.con)
        self.assertEqual(review["schema_version"], 2)
        self.assertEqual([r["id"] for r in review["flagged"]], [third])
        self.assertEqual([r["id"] for r in reads.completion_list(self.con)], [third])
        self.assertEqual(reads.get_task(self.con, self.task)["status"], "done")

    def test_invalid_and_stale_corrections_leave_no_revisions(self):
        for reason, changes in ((" ", {"quantity": 4}), ("Reason", {}),
                                ("Reason", {"outcome": " "}), ("Reason", {"quantity": 2}),
                                ("Reason", {"task_id": None})):
            with self.subTest(reason=reason, changes=changes), self.assertRaises(ValueError):
                writes.correct_completion(self.con, self.original, reason, **changes)
        with self.assertRaises(sqlite3.IntegrityError):
            writes.correct_completion(self.con, self.original, "Unknown duty", duty="missing")
        with self.assertRaises(sqlite3.IntegrityError):
            writes.correct_completion(self.con, self.original, "Incomplete measure", quantity=None)
        self.assertEqual(len(reads.completion_history(self.con, self.original)), 1)
        latest = writes.correct_completion(self.con, self.original, "Fixed count", quantity=4)
        with self.assertRaisesRegex(ValueError, "superseded"):
            writes.correct_completion(self.con, self.original, "Stale attempt", quantity=6)
        self.assertEqual([r["id"] for r in reads.completion_list(self.con)], [latest])

    def test_database_blocks_update_delete_replace_and_reference_rename(self):
        before = self.row(self.original)
        statements = (
            "UPDATE completions SET outcome='rewritten'",
            "DELETE FROM completions",
            f"INSERT OR REPLACE INTO completions(id, category) VALUES ({self.original}, 'work')",
            "UPDATE categories SET name='renamed' WHERE name='work'",
            "UPDATE measures SET name='renamed' WHERE name='packages'",
            "UPDATE duties SET name='renamed' WHERE name='Operations'",
        )
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError), self.con:
                self.con.execute(statement)
            self.assertEqual(self.row(self.original), before)

    def test_database_blocks_branching_and_relinking_corrections(self):
        latest = writes.correct_completion(self.con, self.original, "Count", quantity=3)
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute("""INSERT OR REPLACE INTO completions(category, task_id, supersedes_id, correction_reason)
                                VALUES ('work', ?, ?, 'Branch')""", (self.task, self.original))
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute("""INSERT INTO completions(category, supersedes_id, correction_reason)
                                VALUES ('work', ?, 'Relink')""", (latest,))
        self.assertEqual(len(reads.completion_history(self.con, latest)), 2)

    def test_standalone_correction_and_category_eligibility(self):
        original = writes.add_completion(self.con, "home", outcome="Invented standalone")
        revised = writes.correct_completion(self.con, original, "Wrong category", category="work")
        row = self.row(revised)
        self.assertIsNone(row["task_id"])
        self.assertIsNone(row["due_date"])
        self.assertEqual(row["review_counting"], 1)
        self.assertEqual(self.row(original)["review_counting"], 0)

    def test_cli_lookup_history_correction_and_clear_flags(self):
        status, output, errors = self.run_cli("completions", "--duty", "Operations")
        self.assertEqual(status, 0, errors)
        self.assertIn("Original outcome", output)
        status, _, errors = self.run_cli("correct", str(self.original), "--reason", "Verified details",
                                         "--quantity", "7", "--date", "2020-01-01", "--no-flag")
        self.assertEqual(status, 0, errors)
        latest = reads.completion_list(self.con)[0]
        self.assertEqual((latest["quantity"], latest["flagged"], latest["completed_at"]),
                         (7, 0, "2020-01-01 00:00:00"))
        status, output, errors = self.run_cli("history", str(self.original))
        self.assertEqual(status, 0, errors)
        self.assertIn("Verified details", output)
        self.assertIn("(superseded)", output)
        self.assertIn("(current)", output)
        status, _, errors = self.run_cli("correct", str(latest["id"]), "--reason", "Remove unsupported detail",
                                         "--clear-measure", "--clear-duty", "--clear-due-date")
        self.assertEqual(status, 0, errors)
        latest = reads.completion_list(self.con)[0]
        self.assertIsNone(latest["measure"])
        self.assertIsNone(latest["quantity"])
        self.assertIsNone(latest["duty"])
        self.assertIsNone(latest["due_date"])

    def test_cli_rejects_conflicting_measure_and_invalid_dates(self):
        for flags in (("--clear-measure", "--quantity", "3"), ("--date", "9999-01-01"),
                      ("--due-date", "2020-02-30")):
            with self.subTest(flags=flags):
                self.assertEqual(self.run_cli("correct", str(self.original), "--reason", "Wrong detail", *flags)[0], 1)
        self.assertEqual(len(reads.completion_history(self.con, self.original)), 1)


class HistoryMigrationTests(unittest.TestCase):
    def test_fresh_and_upgraded_schema_have_equivalent_columns_and_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fresh = root / "fresh.db"
            upgraded = root / "upgraded.db"
            db.init_db(fresh)
            fixture = Path(__file__).parent / "fixtures" / "schema_v1.sql"
            with contextlib.closing(sqlite3.connect(upgraded)) as con:
                con.executescript(fixture.read_text(encoding="utf-8-sig"))
            db.migrate(upgraded)
            with contextlib.closing(db.connect(fresh)) as first, contextlib.closing(db.connect(upgraded)) as second:
                for pragma in ("table_info(completions)", "foreign_key_list(completions)", "index_list(completions)"):
                    self.assertEqual([tuple(r) for r in first.execute(f"PRAGMA {pragma}")],
                                     [tuple(r) for r in second.execute(f"PRAGMA {pragma}")])
                query = "SELECT name, sql FROM sqlite_master WHERE type IN ('view', 'trigger') ORDER BY name"
                self.assertEqual([tuple(r) for r in first.execute(query)], [tuple(r) for r in second.execute(query)])

    def test_archive_migration_failure_rolls_back_backfill_and_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "legacy.db"
            fixture = Path(__file__).parent / "fixtures" / "schema_v1.sql"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript(fixture.read_text(encoding="utf-8-sig"))
                con.execute("INSERT INTO completions(category, outcome) VALUES ('work', 'Preserved')")
                con.commit()
                before = list(con.iterdump())
            scripts = root / "migrations"
            scripts.mkdir()
            script = (db.MIGRATIONS_PATH / "002_archive_history.sql").read_text(encoding="utf-8")
            target = scripts / "002_archive_history.sql"
            target.write_text(script + "\nINSERT INTO missing VALUES(1);", encoding="utf-8")
            with patch.object(db, "MIGRATIONS_PATH", scripts):
                with self.assertRaises(sqlite3.OperationalError):
                    db.migrate(path)
                with contextlib.closing(sqlite3.connect(path)) as con:
                    self.assertEqual(list(con.iterdump()), before)
                    self.assertEqual(db.schema_version(con), 1)
                target.write_text(script, encoding="utf-8")
                db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                self.assertEqual(reads.completion_list(con)[0]["outcome"], "Preserved")

    def test_version_one_migration_backfills_without_inventing_capture_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.db"
            fixture = Path(__file__).parent / "fixtures" / "schema_v1.sql"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript(fixture.read_text(encoding="utf-8-sig"))
                con.executescript("""
                    INSERT INTO projects(id, name) VALUES (1, 'Legacy project');
                    INSERT INTO tasks(id, title, category, project_id, due_date, status)
                    VALUES (1, 'Legacy task', 'work', 1, '2020-01-01', 'done');
                    INSERT INTO completions(task_id, category, completed_at, outcome, flagged)
                    VALUES (1, 'work', '2020-01-02 00:00:00', 'Legacy outcome', 1);
                    INSERT INTO completions(category, outcome) VALUES ('home', 'Standalone');
                """)
                before = [tuple(r) for r in con.execute("SELECT * FROM completions")]
                old_columns = [r[1] for r in con.execute("PRAGMA table_info(completions)")]
            db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                columns = ", ".join(old_columns)
                self.assertEqual([tuple(r) for r in con.execute(f"SELECT {columns} FROM completions")], before)
                rows = reads.completion_list(con)
                self.assertTrue(all(r["snapshot_source"] == "backfilled" and r["recorded_at"] is None for r in rows))
                flagged = analytics.flagged_work(con)[0]
                self.assertEqual((flagged["task_title"], flagged["project_name"]), ("Legacy task", "Legacy project"))
                self.assertEqual(analytics.on_time_rate(con)[0]["pct"], 0)
                self.assertEqual(analytics.volume(con)["review_counting"], 1)
                revised = writes.correct_completion(con, 1, "Verified historical deadline", due_date="2020-01-03")
                self.assertEqual(analytics.on_time_rate(con)[0]["pct"], 100)
                self.assertEqual(reads.completion_history(con, revised)[0]["due_date"], "2020-01-01")
                self.assertEqual(reads.completion_history(con, revised)[-1]["snapshot_source"], "backfilled")
                with self.assertRaises(sqlite3.IntegrityError), con:
                    con.execute("DELETE FROM completions")
            self.assertEqual(db.migrate(path), [])
