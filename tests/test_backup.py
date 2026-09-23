import contextlib
import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import cli, db, writes


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.source = self.root / "source.db"
        self.output = self.root / "backups"
        db.init_db(self.source)

    def test_recovery_preserves_records_schema_and_wal_data(self):
        with contextlib.closing(db.connect(self.source)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            writes.add_duty(con, "Operations")
            writes.add_measure(con, "packages")
            task = writes.add_task(con, "Invented work", "work", duty="Operations")
            writes.complete_task(con, task, outcome="Invented result",
                                 measure="packages", quantity=3, flagged=True)
            snapshot = db.backup(self.source, self.output)
            expected = list(con.iterdump())
        recovered = self.root / "recovered.db"
        shutil.copyfile(snapshot, recovered)
        with contextlib.closing(db.connect(recovered)) as con:
            self.assertEqual(list(con.iterdump()), expected)
            self.assertEqual(db.schema_version(con), db.SCHEMA_VERSION)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_repeated_backups_do_not_overwrite(self):
        first = db.backup(self.source, self.output)
        content = first.read_bytes()
        second = db.backup(self.source, self.output)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), content)

    def test_missing_source_is_not_created(self):
        missing = self.root / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            db.backup(missing, self.output)
        self.assertFalse(missing.exists())
        self.assertFalse(self.output.exists())

    def test_invalid_source_leaves_no_snapshot(self):
        self.source.write_bytes(b"not a SQLite database")
        with self.assertRaises(sqlite3.DatabaseError):
            db.backup(self.source, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cli_bypasses_export_refresh(self):
        with patch.object(cli, "refresh_if_stale", side_effect=AssertionError), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            status = cli.main(["--db", str(self.source), "backup",
                               "--out", str(self.output)])
        self.assertEqual(status, 0)
        self.assertIn("Verified backup:", output.getvalue())

    def test_cli_reports_destination_failure(self):
        self.output.write_text("occupied", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            status = cli.main(["--db", str(self.source), "backup",
                               "--out", str(self.output)])
        self.assertEqual(status, 1)
        self.assertIn("Backup failed:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
