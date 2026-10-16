"""Recurring task creation and permanent stopping through the desktop."""

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
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga import db, writes
    from saga.desktop.completion import CompletionDialog
    from saga.desktop.settings import SettingsStore
    from saga.desktop.stop_repeating import StopRepeatingDialog
    from saga.desktop.window import MainWindow


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class RecurringUiTests(unittest.TestCase):
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
        self.addCleanup(self.close_window)

    def close_window(self):
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
            self.window.close()

    def test_create_recurring_task_preview_draft_and_today_label(self):
        form = self.window.entry.task
        form.title.setText('Invented repeating task')
        form.repeat_task.setChecked(True)
        self.assertTrue(form.has_due_date.isChecked())
        self.assertFalse(form.has_due_date.isEnabled())
        form.repeat_frequency.setCurrentIndex(form.repeat_frequency.findData('weekly'))
        form.repeat_interval.setValue(2)
        self.window.entry.already_finished.setChecked(True)
        self.window.entry.already_finished.setChecked(False)
        self.assertEqual(form.draft()['interval'], 2)
        form.show_preview()
        self.assertIn('Every 2 weeks', form.preview_text.toPlainText())
        form.save()
        self.assertTrue(form._committed, form.status.text())
        card = self.window.sections['due_today'].cards[0]
        self.assertIn('Every 2 weeks', card.details_label.text())
        form.reset_form()
        self.assertFalse(form.repeat_task.isChecked())
        self.assertTrue(form.has_due_date.isEnabled())
        self.assertNotIn('repeat', form.draft())

    def test_keep_then_stop_from_today_removes_task_without_completion(self):
        with closing(db.connect(self.database)) as con:
            task_id = writes.add_task(con, 'Invented monthly task', 'work',
                                      due_date='2020-01-01', repeat='monthly')
        self.window.refresh_today()
        task = self.window.service.task_details(task_id)
        series = self.window.service.recurrence_details(task_id)
        confirmation = StopRepeatingDialog(self.window.service, task, series)
        confirmation.back_button.click()
        self.assertFalse(confirmation.result_saved)
        self.assertEqual(self.window.service.task_details(task_id), task)
        confirmation.close()

        def stop(dialog):
            dialog.stop_button.click()
            dialog.stop_button.click()
            self.assertTrue(dialog.result_saved, dialog.status.text())

        def open_details(dialog):
            self.assertFalse(dialog.stop_repeat_button.isHidden())
            with patch.object(StopRepeatingDialog, 'exec', stop):
                dialog.stop_repeat_button.click()

        with patch.object(CompletionDialog, 'exec', open_details):
            self.window.sections['overdue'].cards[0].click()
        self.assertEqual(self.window.sections['overdue'].cards, [])
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()[0],
                             'cancelled')
            self.assertEqual(con.execute('SELECT status FROM recurrences').fetchone()[0], 'stopped')
            self.assertEqual(con.execute('SELECT count(*) FROM completions').fetchone()[0], 0)

    def test_standalone_task_hides_stop_action(self):
        with closing(db.connect(self.database)) as con:
            task_id = writes.add_task(con, 'Invented one-time task', 'work')
        dialog = CompletionDialog(self.window.service, task_id)
        self.assertTrue(dialog.stop_repeat_button.isHidden())
        dialog.close()

    def test_already_stopped_series_hides_stop_action(self):
        with closing(db.connect(self.database)) as con:
            task_id = writes.add_task(con, 'Previously stopped', 'work',
                                      due_date='2020-01-01', repeat='monthly')
            series = self.window.service.recurrence_details(task_id)
            writes.stop_recurrence(con, series['id'])
        dialog = CompletionDialog(self.window.service, task_id)
        self.assertTrue(dialog.stop_repeat_button.isHidden())
        self.assertTrue(dialog.form.isEnabled())
        dialog.close()


if __name__ == '__main__':
    unittest.main()
