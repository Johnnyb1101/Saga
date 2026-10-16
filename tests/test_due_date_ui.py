"""Date changes from Today preserve drafts, preview values, and refresh cards."""

import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga import db, writes
    from saga.desktop.completion import CompletionDialog
    from saga.desktop.due_date import DueDateDialog
    from saga.desktop.settings import SettingsStore
    from saga.desktop.window import MainWindow


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class DueDateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / 'archive.db'
        self.window = MainWindow(SettingsStore(self.root / 'settings.json'))
        self.window.create_new_archive(self.database)
        self.addCleanup(self.window.close)
        with closing(db.connect(self.database)) as con:
            self.task_id = writes.add_task(con, 'Invented task', 'work', due_date='2026-01-01')
        self.window.refresh_today()

    def test_today_change_and_remove_date_preserves_add_task_draft(self):
        self.window.entry.task.title.setText('Keep draft')

        def change(dialog):
            dialog.date.setDate(QDate.currentDate())
            dialog.show_preview()
            self.assertIn('Before: 2026-01-01', dialog.preview.text())
            dialog.save()
            self.assertTrue(dialog.result_saved, dialog.status.text())

        def open_details(dialog):
            with patch.object(DueDateDialog, 'exec', change):
                dialog.change_due_button.click()

        with patch.object(CompletionDialog, 'exec', open_details):
            self.window.sections['overdue'].cards[0].click()
        self.assertEqual(len(self.window.sections['due_today'].cards), 1)
        task = self.window.service.task_details(self.task_id)
        dialog = DueDateDialog(self.window.service, task)
        self.addCleanup(dialog.close)
        dialog.has_date.setChecked(False)
        dialog.show_preview()
        self.assertIn('After: No due date', dialog.preview.text())
        dialog.save()
        dialog.save()
        self.window.refresh_today()
        self.assertEqual(len(self.window.sections['undated'].cards), 1)
        self.assertEqual(self.window.entry.task.title.text(), 'Keep draft')
        # Avoid prompting while cleaning up this deliberately retained draft.
        self.window.entry.task.title.clear()
        self.window.entry.task._dirty = False

    def test_edit_invalidates_preview_cancel_does_not_save(self):
        task = self.window.service.task_details(self.task_id)
        dialog = DueDateDialog(self.window.service, task)
        self.addCleanup(dialog.close)
        dialog.show_preview()
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.date.setDate(QDate.currentDate())
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.save()
        dialog.reject()
        self.assertEqual(self.window.service.task_details(self.task_id), task)

    def test_completion_draft_cancel_keeps_dialog_and_text(self):
        dialog = CompletionDialog(self.window.service, self.task_id)
        dialog.form.outcome.setPlainText('Keep completion draft')
        with (patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel),
              patch.object(DueDateDialog, 'exec') as opened):
            dialog.change_due_button.click()
            opened.assert_not_called()
        self.assertEqual(dialog.form.outcome.toPlainText(), 'Keep completion draft')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
            dialog.close()


if __name__ == '__main__':
    unittest.main()
