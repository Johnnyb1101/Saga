"""Runtime export destination configuration and its CLI/save integration."""

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import cli, db, export, guided, writes


class ExportDestinationTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.default = self.root / "exports"
        self.database = self.root / "data" / "saga.db"
        db.init_db(self.database)
        for target, value in ((db, "ROOT"), (db, "DB_PATH"), (export, "EXPORT_DIR")):
            replacement = {"ROOT": self.root, "DB_PATH": self.database,
                           "EXPORT_DIR": self.default}[value]
            self.enterContext(patch.object(target, value, replacement))
        self.enterContext(patch.dict(os.environ, {"SAGA_EXPORT_DIR": ""}))

    def run_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = cli.main(["--db", str(self.database), *args])
        return status, output.getvalue(), errors.getvalue()

    def test_destination_precedence_and_blank_environment(self):
        explicit = self.root / "explicit"
        with patch.dict(os.environ):
            os.environ.pop('SAGA_EXPORT_DIR', None)
            self.assertEqual(export.resolve_destination(), self.default)
            with contextlib.closing(db.connect(self.database)) as con:
                export.write_all(con)
            self.assertFalse(export.is_stale())
        for configured in ("", "   \t  "):
            with self.subTest(configured=configured), patch.dict(os.environ, SAGA_EXPORT_DIR=configured):
                self.assertEqual(export.resolve_destination(), self.default)
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(self.root / "configured")):
            self.assertEqual(export.resolve_destination(), self.root / "configured")
            self.assertEqual(export.resolve_destination(explicit), explicit)
            with contextlib.closing(db.connect(self.database)) as con:
                export.write_all(con, explicit)
            self.assertFalse(export.is_stale(explicit))
            self.assertTrue(export.is_stale())
        self.assertTrue((explicit / "manifest.json").exists())

    def test_environment_changes_are_read_at_each_export_and_staleness_check(self):
        first, second = self.root / "first", self.root / "second"
        with contextlib.closing(db.connect(self.database)) as con:
            with patch.dict(os.environ, SAGA_EXPORT_DIR=str(first)):
                export.write_all(con)
                self.assertFalse(export.is_stale())
            with patch.dict(os.environ, SAGA_EXPORT_DIR=str(second)):
                self.assertTrue(export.is_stale())
                export.write_all(con)
                self.assertFalse(export.is_stale())
        self.assertTrue((first / "manifest.json").exists())
        self.assertTrue((second / "manifest.json").exists())
        self.assertFalse(self.default.exists())

    def test_relative_environment_is_repo_anchored_and_keeps_spaces(self):
        relative = Path("folder with spaces") / "export files"
        current = self.root / 'other-working-directory'
        current.mkdir()
        with contextlib.chdir(current), patch.dict(os.environ, SAGA_EXPORT_DIR=str(relative)):
            self.assertEqual(export.resolve_destination(), self.root / relative)
            with contextlib.closing(db.connect(self.database)) as con:
                export.write_all(con)
            self.assertFalse(export.is_stale())
        self.assertTrue((self.root / relative / "manifest.json").exists())
        self.assertFalse((current / relative).exists())
        for literal in (' folder ', '~', '$HOME', '%USERPROFILE%'):
            with patch.dict(os.environ, SAGA_EXPORT_DIR=literal):
                self.assertEqual(export.resolve_destination(), self.root / literal)

    def test_explicit_relative_destination_uses_current_directory(self):
        current = self.root / "current"
        current.mkdir()
        original = Path.cwd()
        try:
            os.chdir(current)
            with patch.dict(os.environ, SAGA_EXPORT_DIR="environment"):
                self.assertEqual(export.resolve_destination("explicit"), Path("explicit"))
                with contextlib.closing(db.connect(self.database)) as con:
                    export.write_all(con, "explicit")
                self.assertFalse(export.is_stale("explicit"))
        finally:
            os.chdir(original)
        self.assertTrue((current / "explicit" / "manifest.json").exists())
        self.assertFalse((self.root / "environment").exists())

    @unittest.skipUnless(os.name == "nt", "Windows path syntax")
    def test_ambiguous_windows_environment_paths_are_rejected(self):
        for configured in ("C:exports", "\\exports"):
            with (self.subTest(configured=configured), patch.dict(os.environ, SAGA_EXPORT_DIR=configured),
                  self.assertRaisesRegex(ValueError, "SAGA_EXPORT_DIR")):
                export.resolve_destination()
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(self.root / "absolute")):
            self.assertEqual(export.resolve_destination(), self.root / "absolute")

    def test_cli_refresh_and_post_write_use_configured_destination(self):
        configured = self.root / "auto exports"
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(configured)):
            status, _, errors = self.run_cli("today")
            self.assertEqual((status, errors), (0, ""))
            self.assertFalse(export.is_stale())
            first_generation = json.loads((configured / "manifest.json").read_text())["generation_id"]
            status, _, errors = self.run_cli("add", "New task", "--category", "work", '--due', dt.date.today().isoformat())
            self.assertEqual((status, errors), (0, ""))
            self.assertFalse(export.is_stale())
        self.assertNotEqual(json.loads((configured / "manifest.json").read_text())["generation_id"],
                            first_generation)
        brief = json.loads((configured / "brief.json").read_text())
        self.assertFalse(self.default.exists())
        self.assertEqual(brief["date"], dt.date.today().isoformat())
        self.assertEqual([task['title'] for task in brief['due_today']], ['New task'])

    def test_alternate_database_does_not_automatically_export(self):
        alternate = self.root / "alternate.db"
        db.init_db(alternate)
        configured = self.root / "alternate exports"
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(configured)):
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = cli.main(["--db", str(alternate), "add", "Other task",
                                   "--category", "work"])
            self.assertEqual((status, errors.getvalue()), (0, ""))
        self.assertFalse(configured.exists())
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(configured)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--db', str(alternate), 'export']), 0)
        self.assertTrue((configured / 'manifest.json').exists())

    def test_cli_export_uses_environment_unless_out_is_explicit(self):
        configured, explicit = self.root / "configured", self.root / "explicit"
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(configured)):
            status, _, errors = self.run_cli("export")
            self.assertEqual((status, errors), (0, ""))
            status, _, errors = self.run_cli("export", "--out", str(explicit))
            self.assertEqual((status, errors), (0, ""))
        self.assertTrue((configured / "manifest.json").exists())
        self.assertTrue((explicit / "manifest.json").exists())
        self.assertFalse(self.default.exists())

    def test_invalid_destination_reports_saved_cli_change(self):
        blocked = self.root / "file"
        blocked.write_text("not a directory")
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(blocked / "exports")), \
                patch.object(export, "is_stale", return_value=False):
            status, _, errors = self.run_cli("add", "Preserved task", "--category", "work")
        self.assertEqual(status, 1)
        self.assertIn("Database change was saved", errors)
        self.assertIn("Export failed", errors)
        self.assertFalse(self.default.exists())
        with contextlib.closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT title FROM tasks").fetchone()[0], "Preserved task")

    def test_guided_saved_uses_environment_and_reports_export_failure(self):
        configured = self.root / "guided exports"
        with contextlib.closing(db.connect(self.database)) as con:
            writes.add_task(con, "Guided task", "work")
            with patch.dict(os.environ, SAGA_EXPORT_DIR=str(configured)), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                guided.saved(con, self.database, "Task added")
            self.assertIn("Saved. Task added", output.getvalue())
            self.assertFalse(export.is_stale(configured))
        blocked = self.root / "file"
        blocked.write_text("not a directory")
        with contextlib.closing(db.connect(self.database)) as con:
            writes.add_task(con, "Another guided task", "work")
            with patch.dict(os.environ, SAGA_EXPORT_DIR=str(blocked / "exports")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                guided.saved(con, self.database, "Another task added")
            self.assertIn("database change was saved, but export failed", output.getvalue())
        with contextlib.closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM tasks").fetchone()[0], 2)

    def test_invalid_destination_does_not_fall_back_and_explicit_override_works(self):
        blocked = self.root / 'not-a-directory'
        blocked.write_text('sentinel', encoding='utf-8')
        explicit = self.root / 'explicit'
        with patch.dict(os.environ, SAGA_EXPORT_DIR=str(blocked)):
            self.assertTrue(export.is_stale())
            status, _, errors = self.run_cli('export')
            self.assertEqual(status, 1)
            self.assertIn('Export failed', errors)
            self.assertFalse(self.default.exists())
            self.assertEqual(self.run_cli('export', '--out', str(explicit))[0], 0)
            self.assertFalse(export.is_stale(explicit))
        self.assertEqual(blocked.read_text(encoding='utf-8'), 'sentinel')


if __name__ == "__main__":
    unittest.main()
