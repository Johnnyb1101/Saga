import contextlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import db, writes


class MigrationTests(unittest.TestCase):
    def test_failure_rolls_back_schema_data_and_version_then_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "legacy.db"
            scripts = root / "migrations"
            scripts.mkdir()
            migration = scripts / "001_test.sql"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript("CREATE TABLE records(value TEXT); INSERT INTO records VALUES ('original');")
                before = list(con.iterdump())
            migration.write_text(
                "CREATE TABLE added(id INTEGER); UPDATE records SET value='changed'; "
                "PRAGMA user_version=1; INSERT INTO missing VALUES (1);", encoding="utf-8")
            with patch.object(db, "MIGRATIONS_PATH", scripts):
                with self.assertRaises(sqlite3.OperationalError):
                    db.migrate(path)
                with contextlib.closing(sqlite3.connect(path)) as con:
                    self.assertEqual(list(con.iterdump()), before)
                    self.assertEqual(db.schema_version(con), 0)
                original_backup = next(root.glob("*.bak"))
                original_bytes = original_backup.read_bytes()
                migration.write_text("CREATE TABLE added(id INTEGER); PRAGMA user_version=1;", encoding="utf-8")
                self.assertIn("applied 001_test.sql", db.migrate(path))
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(db.schema_version(con), 1)
                self.assertEqual(con.execute("SELECT value FROM records").fetchone()[0], "original")
                self.assertEqual(con.execute("SELECT * FROM added").fetchall(), [])
            self.assertEqual(original_backup.read_bytes(), original_bytes)
            self.assertEqual(len(list(root.glob("*.bak"))), 2)

    def test_wrong_version_rolls_back_and_preserves_prior_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "legacy.db"
            with contextlib.closing(sqlite3.connect(path)):
                pass
            scripts = root / "migrations"
            scripts.mkdir()
            (scripts / "001_first.sql").write_text(
                "CREATE TABLE first(id INTEGER); PRAGMA user_version=1;", encoding="utf-8")
            (scripts / "002_second.sql").write_text(
                "CREATE TABLE second(id INTEGER); PRAGMA user_version=3;", encoding="utf-8")
            with patch.object(db, "MIGRATIONS_PATH", scripts), \
                    patch.object(db, "SCHEMA_VERSION", 2), \
                    self.assertRaisesRegex(ValueError, "expected 2"):
                db.migrate(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(db.schema_version(con), 1)
                self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                                 [("first",)])

    def test_newer_database_is_rejected_without_backup_or_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "newer.db"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
            with self.assertRaisesRegex(ValueError, "newer than"):
                db.migrate(path)
            self.assertEqual(list(Path(folder).glob("*.bak")), [])
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(db.schema_version(con), db.SCHEMA_VERSION + 1)

    def test_upgrade_preserves_records_backup_and_supports_duties(self):
        # Frozen original schema from 7ff0f99^, independent of current schema.sql.
        fixture = Path(__file__).parent / "fixtures" / "schema_v0.sql"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.db"
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript(fixture.read_text(encoding="utf-8-sig"))
                con.execute("INSERT INTO tasks (title, category) VALUES ('Invented legacy task', 'work')")
                con.execute("INSERT INTO completions (category, outcome) VALUES ('work', 'Invented legacy outcome')")
                con.commit()
                before = list(con.iterdump())
            # A CLI subprocess matches real usage and releases migration connections on exit.
            command = [sys.executable, "-B", str(db.ROOT / "main.py"), "--db", str(path), "migrate"]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("applied 001_duty.sql", result.stdout)
            backups = list(Path(folder).glob("*.bak"))
            self.assertEqual(len(backups), 1)
            with contextlib.closing(sqlite3.connect(backups[0])) as con:
                self.assertEqual(list(con.iterdump()), before)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 0)
            with contextlib.closing(db.connect(path)) as con:
                self.assertEqual(db.schema_version(con), db.SCHEMA_VERSION)
                task = con.execute("SELECT title, duty FROM tasks").fetchone()
                self.assertEqual(tuple(task), ("Invented legacy task", None))
                completion = con.execute("SELECT outcome, duty FROM completions").fetchone()
                self.assertEqual(tuple(completion), ("Invented legacy outcome", None))
                writes.add_duty(con, "Operations", "work")
                task_id = writes.add_task(con, "New task", "work", duty="Operations")
                completion_id = writes.complete_task(con, task_id)
                self.assertEqual(con.execute("SELECT duty FROM completions WHERE id=?", (completion_id,)).fetchone()[0],
                                 "Operations")
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
                after = list(con.iterdump())
            repeated = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertIn("Already up to date.", repeated.stdout)
            self.assertEqual(list(Path(folder).glob("*.bak")), backups)
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(list(con.iterdump()), after)
