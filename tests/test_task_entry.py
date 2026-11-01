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
    from PySide6.QtCore import QCoreApplication, QDate, QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

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
        self.addCleanup(self._close_widget, self.widget)

    @staticmethod
    def _close_widget(widget):
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)

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
        self.assertEqual(task.title.text(), "")
        self.assertTrue(task.form_group.isEnabled())
        self.assertTrue(task.preview_group.isHidden())
        self.assertIn("Saved task #1", task.status.text())
        self.assertFalse(task.has_unsaved_draft)

    def test_two_tasks_can_be_saved_consecutively(self):
        task = self.widget.task
        for number, title in enumerate(("First invented task", "Second invented task"), 1):
            task.title.setText(title)
            task.show_preview()
            task.save()
            self.assertEqual(task.title.text(), "")
            self.assertIn(f"Saved task #{number}", task.status.text())
        self.assertEqual([draft["title"] for draft in self.service.saved_tasks],
                         ["First invented task", "Second invented task"])

    def test_save_failure_keeps_preview_and_draft_for_retry(self):
        task = self.widget.task
        task.title.setText("Invented task")
        task.show_preview()
        with patch.object(self.service, "save_task", side_effect=OSError("database unavailable")):
            task.save()
        self.assertEqual(task.title.text(), "Invented task")
        self.assertFalse(task.preview_group.isHidden())
        self.assertIsNotNone(task._preview_draft)
        self.assertTrue(task.save_button.isEnabled())
        self.assertIn("Your draft is still here", task.status.text())
        self.assertEqual(self.service.saved_tasks, [])
        task.save()
        self.assertEqual(len(self.service.saved_tasks), 1)
        self.assertEqual(task.title.text(), "")

    def test_reference_reload_failure_after_save_cannot_repeat_write(self):
        task = self.widget.task
        task.title.setText("Invented task")
        task.show_preview()
        with patch.object(self.service, "references", side_effect=OSError("references unavailable")):
            task.save()
            task.save()
            self.assertEqual(len(self.service.saved_tasks), 1)
            self.assertTrue(task._committed)
            self.assertFalse(task.new_button.isHidden())
            self.assertIn("Saved task #1", task.status.text())
            self.assertIn("could not start", task.status.text().lower())
        self.assertTrue(task.reset_form())
        self.assertEqual(task.title.text(), "")
        self.assertFalse(task.has_unsaved_draft)

    def test_successful_save_returns_to_top_and_focuses_title(self):
        self.widget.resize(360, 160)
        self.widget.show()
        self.app.processEvents()
        scroll = self.widget.findChild(QScrollArea)
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.assertGreater(bar.value(), 0)
        task = self.widget.task
        task.title.setText("Invented task")
        task.show_preview()
        task.save()
        self.app.processEvents()
        self.assertEqual(bar.value(), 0)
        self.assertTrue(task.title.hasFocus())
        self.assertEqual(task.title.text(), "")
        self.assertIn("Saved task #1", task.status.text())
        self.assertTrue(task.status.isVisible())
        self.assertTrue(scroll.viewport().rect().contains(
            task.status.mapTo(scroll.viewport(), task.status.rect().center())))

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

    def test_checkbox_preserves_shared_and_completion_values_on_discard_cancel(self):
        task = self.widget.task
        completed = task.completion_details
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
        self.assertEqual(completed.completed_date.date().toString('yyyy-MM-dd'), "2026-09-01")
        self.assertTrue(self.widget.has_unsaved_draft)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(self.widget.confirm_discard())
            question.assert_called_once()
        self.assertEqual(task.title.text(), "Invented future work")
        self.assertEqual(completed.outcome.toPlainText(), "Invented completed work")

    def test_service_setter_updates_form_and_shared_category(self):
        replacement = FakeService()
        self.widget.service = replacement
        self.assertIs(self.widget.task.service, replacement)
        self.assertIs(self.widget.task.category.service, replacement)

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
                self.assertEqual(task.title.text(), "")
                task.category.setCurrentIndex(task.category.findData("work"))
                self.assertGreaterEqual(task.role.findData("Invented role"), 0)
                with closing(db.connect(database)) as con:
                    row = con.execute("SELECT title, status, duty FROM tasks").fetchone()
                    self.assertEqual(tuple(row), ("Invented open task", "open", "Invented role"))
                    self.assertEqual(con.execute("SELECT count(*) FROM duties WHERE name='Invented role'").fetchone()[0], 1)
            finally:
                self._close_widget(widget)


if __name__ == "__main__":
    unittest.main()
