import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, db, reads, writes
from saga.recurrence import occurrence_date


class CalendarTests(unittest.TestCase):
    def test_daily_weekly_and_intervals_cross_calendar_boundaries(self):
        cases = (
            ("2020-02-28", "daily", 1, 1, "2020-02-29"),
            ("2020-02-28", "daily", 2, 1, "2020-03-01"),
            ("2020-12-25", "weekly", 2, 1, "2021-01-08"),
            ("2020-01-31", "monthly", 2, 1, "2020-03-31"),
            ("2020-02-29", "monthly", 12, 1, "2021-02-28"),
            ("2020-02-29", "monthly", 12, 4, "2024-02-29"),
        )
        for anchor, frequency, interval, index, expected in cases:
            with self.subTest(case=(anchor, frequency, interval, index)):
                self.assertEqual(occurrence_date(anchor, frequency, interval, index), expected)

    def test_month_end_clamps_then_returns_to_original_day(self):
        self.assertEqual([occurrence_date("2021-01-31", "monthly", 1, n) for n in range(4)],
                         ["2021-01-31", "2021-02-28", "2021-03-31", "2021-04-30"])
        self.assertEqual(occurrence_date("2020-01-31", "monthly", 1, 1), "2020-02-29")

    def test_invalid_schedules_are_rejected(self):
        for anchor in (None, "", "2020-02-30", "20200101", "2020-1-1"):
            with self.subTest(anchor=anchor), self.assertRaises(ValueError):
                occurrence_date(anchor, "daily", 1, 0)
        for interval in (0, -1, 1.5, True, 2**63):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                occurrence_date("2020-01-01", "daily", interval, 0)
        with self.assertRaises(ValueError):
            occurrence_date("2020-01-01", "yearly", 1, 0)
        with self.assertRaises(ValueError):
            occurrence_date("2020-01-01", "daily", 1, -1)


class RecurrenceTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.path = self.root / "recurring.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        writes.add_duty(self.con, "Operations", "work")

    def create(self, **kwargs):
        return writes.add_task(self.con, "Invented recurring task", "work", due_date="2020-01-31",
                               duty="Operations", repeat="monthly", **kwargs)

    def test_completion_preserves_history_and_creates_exactly_one_next_task(self):
        project = writes.add_project(self.con, "Invented project")
        task = self.create(project_id=project)
        series = reads.get_task(self.con, task)["recurrence_id"]
        completion = writes.complete_task(self.con, task, outcome="First occurrence", completed_at="2020-04-15 12:00:00")
        following = reads.open_occurrence(self.con, series)
        self.assertEqual((following["due_date"], following["occurrence_index"], following["project_id"], following["duty"]),
                         ("2020-02-29", 1, project, "Operations"))
        self.assertEqual(following["title"], "Invented recurring task")
        self.assertEqual(len(reads.open_tasks(self.con)), 1)
        archived = reads.completion_history(self.con, completion)[0]
        self.assertEqual((archived["task_id"], archived["due_date"]), (task, "2020-01-31"))
        with self.assertRaises(ValueError):
            writes.complete_task(self.con, task)
        self.assertEqual(analytics.volume(self.con)["completions"], 1)
        self.assertEqual(len(reads.open_tasks(self.con)), 1)
        writes.correct_completion(self.con, completion, "More detail", outcome="Corrected outcome")
        self.assertEqual(reads.open_occurrence(self.con, series)["id"], following["id"])

    def test_early_completion_uses_schedule_not_completion_date(self):
        task = self.create()
        writes.complete_task(self.con, task, completed_at="2020-01-01 00:00:00")
        self.assertEqual(reads.open_tasks(self.con)[0]["due_date"], "2020-02-29")

    def test_reschedule_or_clear_changes_only_current_instance(self):
        for replacement in ("2020-06-01", None):
            with self.subTest(replacement=replacement):
                task = self.create()
                series = reads.get_task(self.con, task)["recurrence_id"]
                writes.reschedule_task(self.con, task, replacement)
                completion = writes.complete_task(self.con, task, outcome="Finished")
                self.assertEqual(reads.completion_history(self.con, completion)[0]["due_date"], replacement)
                self.assertEqual(reads.open_occurrence(self.con, series)["due_date"], "2020-02-29")

    def test_cancel_stops_series_without_completion_and_stop_keeps_current_task(self):
        task = self.create()
        writes.cancel_task(self.con, task)
        self.assertEqual(reads.recurrences(self.con)[0]["status"], "stopped")
        self.assertEqual(reads.open_tasks(self.con), [])
        self.assertEqual(analytics.volume(self.con)["completions"], 0)
        task = self.create()
        series = reads.get_task(self.con, task)["recurrence_id"]
        writes.stop_recurrence(self.con, series)
        self.assertEqual(reads.get_task(self.con, task)["status"], "open")
        writes.complete_task(self.con, task, outcome="Last occurrence")
        self.assertIsNone(reads.open_occurrence(self.con, series))
        self.assertEqual(analytics.volume(self.con)["completions"], 1)
        with self.assertRaises(ValueError):
            writes.stop_recurrence(self.con, series)
        with self.assertRaises(ValueError):
            writes.stop_recurrence(self.con, 9999)

    def test_next_task_failure_rolls_back_completion_and_task_status(self):
        task = self.create()
        self.con.executescript("""CREATE TRIGGER reject_next AFTER INSERT ON tasks
            WHEN NEW.occurrence_index=1
            BEGIN SELECT RAISE(ABORT, 'injected recurrence failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected recurrence failure"):
            writes.complete_task(self.con, task, outcome="Should roll back")
        self.assertEqual(reads.get_task(self.con, task)["status"], "open")
        self.assertEqual(analytics.volume(self.con)["completions"], 0)
        self.assertEqual(len(reads.open_tasks(self.con)), 1)
        self.con.execute("DROP TRIGGER reject_next")
        writes.complete_task(self.con, task, outcome="Retry")
        self.assertEqual(reads.open_tasks(self.con)[0]["occurrence_index"], 1)

    def test_cancel_failure_rolls_back_task_and_series(self):
        task = self.create()
        self.con.executescript("""CREATE TRIGGER reject_stop BEFORE UPDATE ON recurrences
            BEGIN SELECT RAISE(ABORT, 'injected stop failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            writes.cancel_task(self.con, task)
        self.assertEqual(reads.get_task(self.con, task)["status"], "open")
        self.assertEqual(reads.recurrences(self.con)[0]["status"], "active")

    def test_database_prevents_duplicate_open_or_reused_occurrence(self):
        task = self.create()
        series = reads.get_task(self.con, task)["recurrence_id"]
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute("""INSERT INTO tasks(title, category, recurrence_id, occurrence_index)
                                VALUES ('Duplicate open', 'work', ?, 1)""", (series,))
        writes.complete_task(self.con, task)
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute("""INSERT INTO tasks(title, category, recurrence_id, occurrence_index, status)
                                VALUES ('Reused occurrence', 'work', ?, 0, 'done')""", (series,))

    def test_creation_failure_leaves_no_series(self):
        with self.assertRaises(ValueError):
            self.create(project_id=9999)
        with self.assertRaises(ValueError):
            writes.add_task(self.con, "Invalid", "work", repeat="daily")
        with self.assertRaises(ValueError):
            writes.add_task(self.con, "Invalid", "work", interval=2)
        self.con.executescript("""CREATE TRIGGER reject_first AFTER INSERT ON tasks
            BEGIN SELECT RAISE(ABORT, 'injected first failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.create()
        self.assertEqual(reads.recurrences(self.con), [])
        self.assertEqual(reads.open_tasks(self.con), [])

    def test_project_closure_requires_resolving_current_occurrence(self):
        project = writes.add_project(self.con, "Recurring project")
        task = self.create(project_id=project)
        writes.complete_task(self.con, task)
        with self.assertRaises(ValueError):
            writes.close_project(self.con, project)
        writes.cancel_task(self.con, reads.open_tasks(self.con)[0]["id"])
        writes.close_project(self.con, project)
        with self.assertRaises(ValueError):
            self.create(project_id=project)
        self.assertEqual(len(reads.recurrences(self.con)), 1)

    def test_date_overflow_rolls_back_then_stopping_allows_completion(self):
        task = writes.add_task(self.con, "Last date", "work", due_date="9999-12-31", repeat="daily")
        with self.assertRaisesRegex(ValueError, "date range"):
            writes.complete_task(self.con, task)
        self.assertEqual(reads.get_task(self.con, task)["status"], "open")
        self.assertEqual(analytics.volume(self.con)["completions"], 0)
        writes.stop_recurrence(self.con, reads.get_task(self.con, task)["recurrence_id"])
        writes.complete_task(self.con, task)
        self.assertEqual(reads.open_tasks(self.con), [])

    def test_cli_create_list_complete_export_and_stop(self):
        def run(*args):
            result = subprocess.run([sys.executable, "-B", str(db.ROOT / "main.py"), "--db", str(self.path), *args],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        output = run("add", "CLI recurring", "-c", "work", "--due", "2020-01-31", "--repeat", "monthly", "--interval", "2")
        self.assertIn("Recurring series", output)
        task = reads.open_tasks(self.con)[0]
        self.assertIn("monthly every 2", run("recurrences"))
        output = run("done", str(task["id"]), "--outcome", "Finished")
        self.assertIn("due 2020-03-31", output)
        output_dir = self.root / "exports"
        run("export", "--out", str(output_dir))
        brief = json.loads((output_dir / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(brief["overdue"][0]["due_date"], "2020-03-31")
        run("stop-recurrence", str(task["recurrence_id"]))
        following = reads.open_tasks(self.con)[0]
        run("done", str(following["id"]), "--outcome", "Last")
        self.assertEqual(reads.open_tasks(self.con), [])


class RecurrenceMigrationTests(unittest.TestCase):
    def test_migration_preserves_one_off_tasks_archive_and_schema_equivalence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "legacy.db"
            fixture = Path(__file__).parent / "fixtures" / "schema_v2.sql"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript(fixture.read_text(encoding="utf-8-sig"))
                con.executescript("""INSERT INTO tasks(title, category) VALUES ('Legacy task', 'work');
                    INSERT INTO completions(category, outcome) VALUES ('work', 'Preserved');""")
                completion_before = con.execute("SELECT * FROM completions").fetchall()
                task_before = con.execute("SELECT * FROM tasks").fetchall()
            db.migrate(path)
            fresh = root / "fresh.db"
            db.init_db(fresh)
            with contextlib.closing(db.connect(path)) as con, contextlib.closing(db.connect(fresh)) as other:
                self.assertEqual([tuple(r) for r in con.execute("SELECT * FROM completions")], completion_before)
                self.assertEqual([tuple(r)[:-2] for r in con.execute("SELECT * FROM tasks")], task_before)
                task = reads.open_tasks(con)[0]
                self.assertIsNone(task["recurrence_id"])
                self.assertIsNone(task["occurrence_index"])
                self.assertEqual(reads.recurrences(con), [])
                for table in ("tasks", "recurrences"):
                    for name in ("table_info", "foreign_key_list", "index_list"):
                        query = f"PRAGMA {name}({table})"
                        self.assertEqual([tuple(r) for r in con.execute(query)], [tuple(r) for r in other.execute(query)])
                writes.complete_task(con, task["id"], outcome="Completed once")
                self.assertEqual(reads.open_tasks(con), [])
                recurring = writes.add_task(con, "New recurring", "work", due_date="2020-01-01", repeat="daily")
                writes.complete_task(con, recurring)
                self.assertEqual(reads.open_tasks(con)[0]["due_date"], "2020-01-02")
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.migrate(path), [])

    def test_failed_recurrence_migration_rolls_back_and_can_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "legacy.db"
            fixture = Path(__file__).parent / "fixtures" / "schema_v2.sql"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript(fixture.read_text(encoding="utf-8-sig"))
                before = list(con.iterdump())
            scripts = root / "migrations"
            scripts.mkdir()
            original = (db.MIGRATIONS_PATH / "003_recurrences.sql").read_text(encoding="utf-8")
            script = scripts / "003_recurrences.sql"
            script.write_text(original + "\nINSERT INTO missing VALUES(1);", encoding="utf-8")
            with patch.object(db, "MIGRATIONS_PATH", scripts):
                with self.assertRaises(sqlite3.OperationalError):
                    db.migrate(path)
                with contextlib.closing(sqlite3.connect(path)) as con:
                    self.assertEqual(list(con.iterdump()), before)
                    self.assertEqual(db.schema_version(con), 2)
                script.write_text(original, encoding="utf-8")
                db.migrate(path)
            db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                self.assertEqual(db.schema_version(con), db.SCHEMA_VERSION)
                self.assertEqual(reads.recurrences(con), [])
