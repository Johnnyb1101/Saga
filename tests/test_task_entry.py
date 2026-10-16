"""Offscreen interaction checks for the Add task entry choices."""

import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYSIDE6_AVAILABLE = find_spec("PySide6") is not None

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga import db
    from saga.application import ArchiveService
    from saga.desktop.task_entry import AddTaskWidget


class FakeService:
    def __init__(self):
        self.saved_tasks = []
        self.retry_count = 0
        self.export_error = None

    def references(self):
        return {"categories": ["work", "personal"],
                "roles": {"work": ["Existing role"], "personal": []},
                "measures": ["items"]}

    def preview_task(self, draft):
        if not draft["title"].strip():
            raise ValueError("Task title must not be blank")
        return draft

    def save_task(self, draft):
        self.preview_task(draft)
        self.saved_tasks.append(dict(draft))
        return SimpleNamespace(task_id=len(self.saved_tasks), export_error=self.export_error)

    def retry_export(self):
        self.retry_count += 1


@unittest.skipUnless(PYSIDE6_AVAILABLE, "Optional PySide6 desktop dependency is not installed")
class TaskEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = FakeService()
        self.widget = AddTaskWidget(self.service)
        self.addCleanup(self.widget.close)

    def test_preview_edit_and_save_only_once(self):
        task = self.widget.task
        task.title.setText("Invented task")
        task.category.setCurrentIndex(task.category.findData("work"))
        task.role.setCurrentIndex(task.role.findData("Existing role"))
        task.has_due_date.setChecked(True)
        task.due_date.setDate(QDate(2026, 10, 15))
        task.show_preview()
        self.assertIn("Invented task", task.preview_text.toPlainText())
        task.edit_draft()
        task.title.setText("Revised invented task")
        task.show_preview()
        task.save()
        task.save()
        self.assertEqual(len(self.service.saved_tasks), 1)
        self.assertEqual(self.service.saved_tasks[0], {
            "title": "Revised invented task", "category": "work", "duty": "Existing role",
            "new_duty": None, "due_date": "2026-10-15",
        })
        self.assertFalse(task.save_button.isEnabled())
        self.assertFalse(task.has_unsaved_draft)

    def test_invalid_preview_keeps_draft(self):
        task = self.widget.task
        task.title.setText("   ")
        task.show_preview()
        self.assertIn("Check the draft", task.status.text())
        self.assertEqual(task.title.text(), "   ")
        self.assertTrue(task.has_unsaved_draft)
        self.assertIsNone(task._preview_draft)

    def test_export_retry_does_not_save_again(self):
        task = self.widget.task
        self.service.export_error = "unavailable"
        task.title.setText("Invented task")
        task.show_preview()
        task.save()
        self.assertIn("Export failed", task.status.text())
        task.retry_export()
        self.assertEqual(self.service.retry_count, 1)
        self.assertEqual(len(self.service.saved_tasks), 1)
        self.assertIn("Export completed", task.status.text())

    def test_nested_save_cannot_commit_twice(self):
        task = self.widget.task
        task.title.setText("Invented task")
        task.show_preview()
        original_save = self.service.save_task

        def nested_save(draft):
            task.save()
            return original_save(draft)

        with patch.object(self.service, "save_task", side_effect=nested_save) as save:
            task.save()
            save.assert_called_once()
        self.assertEqual(len(self.service.saved_tasks), 1)

    def test_modes_preserve_both_drafts_and_discard_is_all_or_nothing(self):
        task = self.widget.task
        completed = self.widget.completed
        self.assertFalse(self.widget.already_finished.isChecked())
        task.title.setText("Invented future work")
        self.widget.already_finished.setChecked(True)
        completed.outcome.setPlainText("Invented completed work")
        completed.use_today.setChecked(False)
        completed.completed_date.setDate(QDate(2026, 9, 1))
        self.widget.already_finished.setChecked(False)
        self.assertEqual(task.title.text(), "Invented future work")
        self.widget.already_finished.setChecked(True)
        self.assertEqual(completed.outcome.toPlainText(), "Invented completed work")
        self.assertEqual(completed.draft()["completed_date"], "2026-09-01")
        self.assertTrue(self.widget.has_unsaved_draft)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(self.widget.confirm_discard())
            question.assert_called_once()
        self.assertEqual(task.title.text(), "Invented future work")
        self.assertEqual(completed.outcome.toPlainText(), "Invented completed work")

    def test_service_setter_updates_both_forms(self):
        replacement = FakeService()
        self.widget.service = replacement
        self.assertIs(self.widget.task.service, replacement)
        self.assertIs(self.widget.completed.service, replacement)

    def test_real_service_saves_open_task_and_new_role(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / "saga.db"
            db.init_db(database)
            widget = AddTaskWidget(ArchiveService(database))
            try:
                task = widget.task
                task.title.setText("Invented open task")
                task.category.setCurrentIndex(task.category.findData("work"))
                task.role.setCurrentIndex(task.role.count() - 1)
                task.new_role.setText("Invented role")
                task.show_preview()
                self.assertTrue(task._preview_draft)
                task.save()
                self.assertIn("Saved task #", task.status.text())
                widget.completed.category.setCurrentIndex(widget.completed.category.findData("work"))
                self.assertGreaterEqual(widget.completed.role.findData("Invented role"), 0)
                with closing(db.connect(database)) as con:
                    row = con.execute("SELECT title, status, duty FROM tasks").fetchone()
                    self.assertEqual(tuple(row), ("Invented open task", "open", "Invented role"))
                    self.assertEqual(con.execute("SELECT count(*) FROM duties WHERE name='Invented role'").fetchone()[0], 1)
            finally:
                widget.close()


if __name__ == "__main__":
    unittest.main()
