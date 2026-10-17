"""History integration respects archive selection and unfinished task drafts."""

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
    from saga.application import ArchiveService
    from saga.desktop.settings import SettingsStore
    from saga.desktop.window import MainWindow


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class HistoryWindowTests(unittest.TestCase):
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

    def record(self, text):
        with closing(db.connect(self.database)) as con:
            writes.add_completion(con, 'work', outcome=text)

    def test_reports_load_on_selection_and_preserve_entry_draft(self):
        self.window.entry.task.title.setText('Keep unsaved task')
        with patch.object(ArchiveService, 'history_report', wraps=self.window.service.history_report) as report:
            self.assertEqual(report.call_count, 0)
            self.record('Invented first accomplishment')
            self.window.tabs.setCurrentWidget(self.window.history)
            self.assertEqual(report.call_count, 1)
            self.assertEqual(self.window.history.accomplishments_table.rowCount(), 1)
            self.window.tabs.setCurrentWidget(self.window.today_page)
            self.record('Invented second accomplishment')
            self.window.tabs.setCurrentWidget(self.window.history)
            self.assertEqual(report.call_count, 2)
            self.assertEqual(self.window.history.accomplishments_table.rowCount(), 2)
        self.assertEqual(self.window.entry.task.title.text(), 'Keep unsaved task')

    def test_custom_range_survives_tab_switch(self):
        history = self.window.history
        history.use_from.setChecked(True)
        history.use_to.setChecked(True)
        history.from_date.setDate(QDate(2020, 1, 1))
        history.to_date.setDate(QDate(2020, 12, 31))
        self.window.tabs.setCurrentWidget(history)
        self.window.tabs.setCurrentWidget(self.window.today_page)
        self.window.tabs.setCurrentWidget(history)
        self.assertEqual(history.from_date.date(), QDate(2020, 1, 1))
        self.assertEqual(history.to_date.date(), QDate(2020, 12, 31))
        self.assertEqual(history.accomplishments_table.rowCount(), 0)

    def test_changing_archives_replaces_report_without_leaking_old_rows(self):
        self.record('Invented original-archive result')
        self.window.tabs.setCurrentWidget(self.window.history)
        self.assertEqual(self.window.history.accomplishments_table.rowCount(), 1)
        previous = self.window.history
        other = self.root / 'other.db'
        self.window.create_new_archive(other)
        self.assertIsNot(self.window.history, previous)
        self.assertEqual(self.window.tabs.count(), 3)
        self.window.tabs.setCurrentWidget(self.window.history)
        self.assertEqual(self.window.history.accomplishments_table.rowCount(), 0)
        self.assertEqual(self.window.history.service.database, other.resolve())


if __name__ == '__main__':
    unittest.main()
