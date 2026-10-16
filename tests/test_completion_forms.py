"""Offscreen checks for the two completion entry paths."""

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
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga import db
    from saga.application import ArchiveService
    from saga.desktop.completion import CompletionDialog
    from saga.desktop.task_entry import AddTaskWidget


class FakeService:
    def __init__(self):
        self.saves = 0
        self.retries = 0
        self.export_error = None
        self.task = {'id': 7, 'title': 'Submit applications', 'category': 'work',
                     'duty': 'Volunteer', 'due_date': '2026-11-01', 'status': 'open'}

    def references(self):
        return {"categories": ["personal", "work"],
                "roles": {"personal": [], "work": ["Volunteer"]},
                "measures": ["applications"]}

    def task_details(self, task_id):
        assert task_id == 7
        return self.task.copy()

    def preview_completion(self, expected_task, draft):
        assert expected_task == self.task
        if not draft["outcome"].strip() or draft["measurement_status"] is None:
            raise ValueError("Finish the outcome and measurement choice")
        return draft

    def save_completion(self, expected_task, draft):
        self.preview_completion(expected_task, draft)
        self.saves += 1
        return SimpleNamespace(completion_id=self.saves, export_error=self.export_error)

    def retry_export(self):
        self.retries += 1


@unittest.skipUnless(PYSIDE6_AVAILABLE, "Optional PySide6 desktop dependency is not installed")
class CompletionFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = FakeService()

    def test_checkbox_preserves_drafts_and_copies_blank_context(self):
        widget = AddTaskWidget(self.service)
        self.addCleanup(widget.close)
        widget.task.title.setText("Submit applications")
        widget.task.category.setCurrentIndex(widget.task.category.findData("work"))
        widget.task.role.setCurrentIndex(widget.task.role.findData("Volunteer"))
        widget.already_finished.setChecked(True)
        self.assertEqual(widget.stack.currentIndex(), 1)
        self.assertEqual(widget.completed.outcome.toPlainText(), "Submit applications")
        self.assertEqual(widget.completed.category.currentData(), "work")
        self.assertEqual(widget.completed.role.currentData(), "Volunteer")
        widget.completed.outcome.setPlainText("Submitted three applications")
        widget.already_finished.setChecked(False)
        self.assertEqual(widget.task.title.text(), "Submit applications")
        widget.already_finished.setChecked(True)
        self.assertEqual(widget.completed.outcome.toPlainText(), "Submitted three applications")

    def test_measurement_fields_follow_choice(self):
        widget = AddTaskWidget(self.service)
        self.addCleanup(widget.close)
        form = widget.completed
        self.assertFalse(form.form_layout.isRowVisible(form.measure))
        self.assertFalse(form.form_layout.isRowVisible(form.quantity))
        self.assertFalse(form.form_layout.isRowVisible(form.remind_on))
        form.measurement_status.setCurrentIndex(form.measurement_status.findData("measured"))
        self.assertTrue(form.form_layout.isRowVisible(form.measure))
        self.assertTrue(form.form_layout.isRowVisible(form.quantity))
        form.measure.setCurrentIndex(form.measure.count() - 1)
        self.assertTrue(form.form_layout.isRowVisible(form.new_measure))
        form.measurement_status.setCurrentIndex(form.measurement_status.findData("unknown"))
        self.assertFalse(form.form_layout.isRowVisible(form.measure))
        self.assertFalse(form.form_layout.isRowVisible(form.new_measure))
        self.assertTrue(form.form_layout.isRowVisible(form.remind_on))

    def test_tracked_context_cancel_double_save_and_export_retry(self):
        dialog = CompletionDialog(self.service, 7)
        self.addCleanup(dialog.close)
        form = dialog.form
        snapshot = dialog.task.copy()
        self.assertIn("Submit applications", form.task_context.text())
        self.assertIn("Volunteer", form.task_context.text())
        self.assertNotIn(form.category, [form.form_layout.itemAt(i).widget()
                         for i in range(form.form_layout.count()) if form.form_layout.itemAt(i)])
        form.outcome.setPlainText("Submitted three applications")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(dialog.close())
        self.assertTrue(form.has_unsaved_draft)
        form.measurement_status.setCurrentIndex(form.measurement_status.findData("not_applicable"))
        form.show_preview()
        form.edit_draft()
        form.outcome.setPlainText("Submitted three applications and shared results")
        form.show_preview()
        self.assertEqual(dialog.task, snapshot)
        self.assertIn("No number needed", form.preview_text.toPlainText())
        self.service.export_error = "offline"
        form.save()
        form.save()
        self.assertEqual(self.service.saves, 1)
        self.assertTrue(form.retry_button.isVisibleTo(form.preview_group) or not form.retry_button.isHidden())
        form.retry_export()
        self.assertEqual(self.service.retries, 1)
        self.assertEqual(self.service.saves, 1)
        self.assertTrue(dialog.close())

    def test_escape_preserves_draft_when_discard_is_cancelled(self):
        dialog = CompletionDialog(self.service, 7)
        dialog.show()
        self.addCleanup(dialog.close)
        dialog.form.outcome.setPlainText("A draft to keep")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.form.outcome.toPlainText(), "A draft to keep")
            question.assert_called_once()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.assertFalse(dialog.isVisible())

    def test_dialog_fits_available_screen_and_scrolls(self):
        dialog = CompletionDialog(self.service, 7)
        self.addCleanup(dialog.close)
        dialog.show()
        QApplication.processEvents()
        available = QApplication.primaryScreen().availableGeometry()
        self.assertLessEqual(dialog.width(), available.width())
        self.assertLessEqual(dialog.height(), available.height())
        self.assertIs(dialog.scroll.widget(), dialog.form)
        dialog.resize(480, 400)
        QApplication.processEvents()
        self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(), 0)

    def test_real_service_completes_selected_task(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root:
            database = Path(root) / "saga.db"
            db.init_db(database)
            service = ArchiveService(database)
            saved = service.save_task({"title": "Submit applications", "category": "work",
                                       "duty": None, "new_duty": None, "due_date": None})
            dialog = CompletionDialog(service, saved.task_id)
            try:
                dialog.form.outcome.setPlainText("Submitted three applications")
                dialog.form.measurement_status.setCurrentIndex(
                    dialog.form.measurement_status.findData("measured"))
                dialog.form.measure.setCurrentIndex(dialog.form.measure.findData("applications"))
                if dialog.form.measure.currentIndex() < 0:
                    dialog.form.measure.setCurrentIndex(dialog.form.measure.count() - 1)
                    dialog.form.new_measure.setText("applications")
                dialog.form.quantity.setText("3")
                dialog.form.show_preview()
                self.assertTrue(dialog.form._preview_draft)
                dialog.form.save()
                self.assertTrue(dialog.form._committed)
                with closing(db.connect(database)) as con:
                    row = con.execute("SELECT task_id, category, outcome, quantity FROM completions").fetchone()
                    self.assertEqual(tuple(row), (saved.task_id, "work", "Submitted three applications", 3))
                    self.assertEqual(con.execute("SELECT status FROM tasks WHERE id=?", (saved.task_id,)).fetchone()[0], "done")
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
