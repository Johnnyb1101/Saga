"""Today task activation and completion keep each task section usable."""

import datetime as dt
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
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from saga import db, writes
    from saga.desktop.completion import CompletionDialog
    from saga.desktop.settings import SettingsStore
    from saga.desktop.window import MainWindow, TaskCard


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class TodayCompletionUiTests(unittest.TestCase):
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
        self.addCleanup(self._close)

    def _close(self):
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
            self.window.close()

    def test_every_open_task_section_can_complete_and_preserves_add_task_draft(self):
        today = dt.date.today()
        sections = [('overdue', -2), ('due_today', 0), ('due_soon', 3), ('later', 40), ('undated', None)]
        with closing(db.connect(self.database)) as con:
            for section, offset in sections:
                due = (today + dt.timedelta(days=offset)).isoformat() if offset is not None else None
                writes.add_task(con, f'Invented {section}', 'work', due_date=due)
        self.window.refresh_today()
        self.window.entry.task.title.setText('Keep unsaved task')
        self.window.entry.completed.outcome.setPlainText('Keep unsaved completed work')

        def complete(dialog):
            dialog.form.outcome.setPlainText('Finished this task')
            dialog.form.measurement_status.setCurrentIndex(
                dialog.form.measurement_status.findData('not_applicable'))
            dialog.form.show_preview()
            self.assertIsNotNone(dialog.form._preview_draft)
            dialog.form.save()
            self.assertTrue(dialog.form._committed, dialog.form.status.text())
            return QDialog.DialogCode.Accepted

        with patch.object(CompletionDialog, 'exec', complete):
            for section, _ in sections:
                with self.subTest(section=section):
                    card = self.window.sections[section].cards[0]
                    self.assertIsInstance(card, TaskCard)
                    card.click()
                    self.assertEqual(self.window.sections[section].cards, [])
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM tasks WHERE status='done'").fetchone()[0], 5)
            self.assertEqual(con.execute('SELECT count(*) FROM completions').fetchone()[0], 5)
        self.assertEqual(self.window.tabs.count(), 2)
        self.assertEqual(self.window.entry.task.title.text(), 'Keep unsaved task')
        self.assertEqual(self.window.entry.completed.outcome.toPlainText(), 'Keep unsaved completed work')

    def test_task_card_keyboard_activation_and_stale_task_error(self):
        with closing(db.connect(self.database)) as con:
            task_id = writes.add_task(con, '<b>Plain title</b>', 'work')
        self.window.refresh_today()
        card = self.window.sections['undated'].cards[0]
        self.assertEqual(card.accessibleName(), '<b>Plain title</b>')
        self.assertEqual(card.title_label.textFormat(), Qt.TextFormat.PlainText)
        with patch.object(CompletionDialog, 'exec', return_value=QDialog.DialogCode.Rejected) as opened:
            QTest.keyClick(card, Qt.Key.Key_Space)
            opened.assert_called_once()
        with closing(db.connect(self.database)) as con:
            writes.complete_task(con, task_id, 'Completed elsewhere')
        card.click()
        self.assertIn('Could not open this task', self.window.today_error.text())
        self.assertEqual(self.window.sections['undated'].cards, [])


if __name__ == '__main__':
    unittest.main()
