"""Behavior of the desktop-facing service over a disposable Saga archive."""

import contextlib
import datetime as dt
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, writes


class ArchiveServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = self.root / "nested" / "archive.db"
        self.assertEqual(application.create_archive(self.database), self.database.resolve())
        self.service = application.ArchiveService(self.database)

    def draft(self, **changes):
        values = {
            "category": "work",
            "outcome": "Completed an invented inventory review",
            "completed_date": None,
            "duty": None,
            "new_duty": None,
            "measurement_status": "not_applicable",
            "measure": None,
            "new_measure": None,
            "quantity": None,
            "remind_on": None,
            "flagged": False,
        }
        values.update(changes)
        return values

    def rows(self, table):
        with contextlib.closing(db.connect(self.database)) as con:
            return [dict(row) for row in con.execute(f"SELECT * FROM {table}")]

    def test_create_archive_refuses_existing_file_and_validation_rejects_missing_or_old_schema(self):
        self.assertIsNone(self.service.validate_archive())
        with self.assertRaises(FileExistsError):
            application.create_archive(self.database)
        with self.assertRaises(FileNotFoundError):
            application.ArchiveService(self.root / "missing.db").validate_archive()
        with contextlib.closing(sqlite3.connect(self.database)) as con, con:
            con.execute(f"PRAGMA user_version={db.SCHEMA_VERSION - 1}")
        with self.assertRaisesRegex(ValueError, "schema version"):
            self.service.validate_archive()

    def test_today_uses_inclusive_fourteen_day_boundary_and_plain_dicts(self):
        on = dt.date.today()
        with contextlib.closing(db.connect(self.database)) as con:
            for title, offset in (("late", -1), ("today", 0), ("soon", 14), ("later", 15)):
                writes.add_task(con, title, "work", due_date=(on + dt.timedelta(days=offset)).isoformat())
            writes.add_completion(con, "work", "Invented evidence pending", measurement_status="unknown",
                                  remind_on=on.isoformat())
        view = self.service.today(on=on.isoformat())
        self.assertEqual(view["date"], on.isoformat())
        for key, expected in (("overdue", "late"), ("due_today", "today"), ("due_soon", "soon")):
            self.assertEqual([row["title"] for row in view[key]], [expected])
            self.assertIs(type(view[key][0]), dict)
        self.assertEqual([row["outcome"] for row in view["followups"]], ["Invented evidence pending"])

    def test_references_include_only_active_roles_and_registered_measures(self):
        with contextlib.closing(db.connect(self.database)) as con:
            writes.add_duty(con, "Invented active role", "work")
            writes.add_duty(con, "Invented retired role", "work")
            writes.set_role_status(con, "work", "Invented retired role", False)
            writes.add_measure(con, "invented units")
        refs = self.service.references()
        self.assertIn("work", refs["categories"])
        self.assertIn("Invented active role", refs["roles"]["work"])
        self.assertNotIn("Invented retired role", refs["roles"]["work"])
        self.assertEqual(refs["measures"], ["invented units"])

    def test_preview_normalizes_values_without_registering_references_or_completions(self):
        draft = self.draft(outcome="  Invented outcome  ", new_duty="New invented role",
                           measurement_status="measured", new_measure="new invented unit", quantity="2.5",
                           completed_date="2020-02-29", flagged=True)
        before = {name: self.rows(name) for name in ("duties", "category_roles", "measures", "completions")}
        preview = self.service.preview_capture(draft)
        self.assertEqual(preview["new_duty"], "New invented role")
        self.assertEqual(preview["new_measure"], "new invented unit")
        values = preview["values"]
        self.assertEqual(values["outcome"], "Invented outcome")
        self.assertEqual(values["completed_at"], "2020-02-29 00:00:00")
        self.assertEqual(values["quantity"], 2.5)
        self.assertEqual(values["measurement_status"], "measured")
        self.assertEqual(before, {name: self.rows(name) for name in before})

    def test_invalid_capture_drafts_leave_no_writes(self):
        future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        invalid = (
            {"outcome": "   "},
            {"category": "unregistered"},
            {"completed_date": "2020-02-30"},
            {"completed_date": "2020-2-1"},
            {"completed_date": future},
            {"measurement_status": "unknown", "remind_on": None},
            {"measurement_status": "measured", "measure": "units", "quantity": None},
            {"measurement_status": "measured", "measure": None, "quantity": "1"},
            {"measurement_status": "measured", "new_measure": "new units", "quantity": "nan"},
            {"measurement_status": "measured", "new_measure": "new units", "quantity": math.inf},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.service.save_capture(self.draft(new_duty="Uncommitted role", **changes))
        for table in ("completions", "duties", "category_roles", "measures", "measurement_followups"):
            self.assertEqual(self.rows(table), [], table)

    def test_stale_retired_role_is_rejected_without_a_completion(self):
        with contextlib.closing(db.connect(self.database)) as con:
            writes.add_duty(con, "Invented old role", "work")
        draft = self.draft(duty="Invented old role")
        self.service.preview_capture(draft)
        with contextlib.closing(db.connect(self.database)) as con:
            writes.set_role_status(con, "work", "Invented old role", False)
        with self.assertRaises(ValueError):
            self.service.save_capture(draft)
        self.assertEqual(self.rows("completions"), [])

    def test_blank_new_reference_is_not_silently_saved_as_unassigned(self):
        for changes in ({'new_duty': ''}, {'new_duty': '   '},
                        {'measurement_status': 'measured', 'new_measure': '', 'quantity': '1'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.save_capture(self.draft(**changes))
        self.assertEqual(self.rows('completions'), [])

    def test_save_registers_references_once_and_records_backdated_quantity(self):
        draft = self.draft(new_duty="Invented coordinator", measurement_status="measured",
                           new_measure="invented packages", quantity="0", completed_date="2020-02-29",
                           flagged=True)
        saved = self.service.save_capture(draft)
        self.assertIsInstance(saved.completion_id, int)
        self.assertIsNone(saved.export_error)
        row = self.rows("completions")[0]
        self.assertEqual(row["id"], saved.completion_id)
        self.assertEqual((row["duty"], row["measure"], row["quantity"]),
                         ("Invented coordinator", "invented packages", 0))
        self.assertEqual((row["completed_at"], row["flagged"]), ("2020-02-29 00:00:00", 1))
        self.assertEqual(len(self.rows("duties")), 1)
        self.assertEqual(len(self.rows("category_roles")), 1)
        self.assertEqual(len(self.rows("measures")), 1)

    def test_unknown_measurement_creates_dated_followup(self):
        reminder = (dt.date.today() + dt.timedelta(days=3)).isoformat()
        saved = self.service.save_capture(self.draft(measurement_status="unknown", remind_on=reminder))
        followup = self.rows("measurement_followups")[0]
        self.assertEqual((followup["completion_id"], followup["remind_on"], followup["status"]),
                         (saved.completion_id, reminder, "open"))

    def test_failed_completion_rolls_back_new_references(self):
        with contextlib.closing(sqlite3.connect(self.database)) as con, con:
            con.execute("CREATE TRIGGER test_reject_completion BEFORE INSERT ON completions "
                        "BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END")
        draft = self.draft(new_duty="Pending role", measurement_status="measured",
                           new_measure="pending units", quantity="1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.save_capture(draft)
        for table in ("duties", "category_roles", "measures", "completions"):
            self.assertEqual(self.rows(table), [], table)

    def test_export_is_opt_in_and_retry_requires_destination(self):
        with patch.object(export, "write_all", side_effect=AssertionError("unexpected export")):
            self.service.save_capture(self.draft())
        with self.assertRaises(ValueError):
            self.service.retry_export()
        self.assertEqual(len(self.rows("completions")), 1)

    def test_export_failure_reports_saved_row_and_retry_does_not_duplicate(self):
        destination = self.root / "published"
        service = application.ArchiveService(self.database, export_dir=destination)
        with patch.object(export, "write_all", side_effect=export.ExportError("injected failure")) as writer:
            saved = service.save_capture(self.draft())
        self.assertIn("injected failure", saved.export_error)
        self.assertEqual(len(self.rows("completions")), 1)
        self.assertEqual(writer.call_count, 1)
        service.retry_export()
        self.assertEqual(len(self.rows("completions")), 1)
        self.assertTrue((destination / "manifest.json").exists())

    def test_service_closes_connections_after_reads_writes_and_errors(self):
        original = db.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            con = original(*args, **kwargs)
            opened.append(con)
            return con

        with patch.object(db, "connect", side_effect=tracked_connect):
            self.service.validate_archive()
            self.service.today(on="2026-01-10")
            self.service.references()
            self.service.preview_capture(self.draft())
            self.service.save_capture(self.draft())
            with self.assertRaises(ValueError):
                self.service.save_capture(self.draft(outcome=" "))
        self.assertTrue(opened)
        for con in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                con.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
