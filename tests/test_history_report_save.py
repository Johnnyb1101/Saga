"""Desktop report saving uses visible filters and handles cancellation/failure."""
import json
import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from report_bundle import verify_package
from saga import application, db, writes

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga.desktop.history import HistoryWidget


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class HistoryReportSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.widget = HistoryWidget(SimpleNamespace(database=Path(self.temp.name) / 'source.db'))
        self.addCleanup(self.widget.close)
        self.widget.from_date.setDate(QDate(2024, 2, 1))
        self.widget.to_date.setDate(QDate(2024, 2, 29))
        self.widget.category_filter.addItem('work', 'work')
        self.widget.category_filter.setCurrentIndex(1)
        self.widget.role_filter.addItem('Lead', 'Lead')
        self.widget.role_filter.setCurrentIndex(1)

    def test_visible_dates_filters_save_and_open_local_html(self):
        with (patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value=self.temp.name),
              patch('saga.desktop.history.publish', side_effect=lambda db, out, **scope: out) as save,
              patch('saga.desktop.history.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes),
              patch('saga.desktop.history.QDesktopServices.openUrl', return_value=True) as open_url):
            self.widget.save_report_button.click()
        args, scope = save.call_args
        self.assertEqual(scope, {'since': '2024-02-01', 'until': '2024-02-29', 'category': 'work', 'duty': 'Lead'})
        self.assertEqual(args[0], self.widget.service.database)
        self.assertEqual(args[1].parent, Path(self.temp.name))
        self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(args[1] / 'report.html').replace('\\', '/'))
        self.assertTrue(self.widget.save_report_button.isEnabled())

    def test_all_time_and_decline_open(self):
        self.widget.use_from.setChecked(False)
        self.widget.use_to.setChecked(False)
        with (patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value=self.temp.name),
              patch('saga.desktop.history.publish', side_effect=lambda db, out, **scope: out) as save,
              patch('saga.desktop.history.QMessageBox.question', return_value=QMessageBox.StandardButton.No),
              patch('saga.desktop.history.QDesktopServices.openUrl') as open_url):
            self.widget.save_report_button.click()
        self.assertIsNone(save.call_args.kwargs['since'])
        self.assertIsNone(save.call_args.kwargs['until'])
        open_url.assert_not_called()

    def test_invalid_range_and_cancel_never_publish(self):
        with (patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value='') as choose,
              patch('saga.desktop.history.publish') as save):
            self.widget.from_date.setDate(QDate(2025, 1, 1))
            self.widget.save_report_button.click()
            choose.assert_not_called()
            self.widget.from_date.setDate(QDate(2024, 1, 1))
            self.widget.save_report_button.click()
        save.assert_not_called()

    def test_save_failure_and_browser_failure_are_visible(self):
        with (patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value=self.temp.name),
              patch('saga.desktop.history.publish', side_effect=OSError('disk full')),
              patch('saga.desktop.history.QMessageBox.question') as question):
            self.widget.save_report_button.click()
        self.assertIn('disk full', self.widget.status_label.text())
        self.assertTrue(self.widget.save_report_button.isEnabled())
        question.assert_not_called()
        with (patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value=self.temp.name),
              patch('saga.desktop.history.publish', side_effect=lambda db, out, **scope: out),
              patch('saga.desktop.history.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes),
              patch('saga.desktop.history.QDesktopServices.openUrl', return_value=False)):
            self.widget.save_report_button.click()
        self.assertIn('could not open the browser', self.widget.status_label.text())

    def test_saved_package_has_only_selected_range_category_and_role(self):
        database = db.init_db(Path(self.temp.name) / 'actual.db')
        with closing(db.connect(database)) as con:
            writes.add_measure(con, 'hours')
            writes.add_duty(con, 'Lead', 'work')
            for date, quantity in [('2024-01-31 23:59:59', 100),
                                   ('2024-02-01 00:00:00', 2),
                                   ('2024-02-29 23:59:59', 3),
                                   ('2024-03-01 00:00:00', 100)]:
                writes.add_completion(con, 'work', 'Recorded work', measure='hours',
                                      quantity=quantity, duty='Lead', completed_at=date)
            writes.add_completion(con, 'home', 'Excluded category', measure='hours',
                                  quantity=100, completed_at='2024-02-15 00:00:00')
            writes.add_completion(con, 'work', 'Excluded role', measure='hours',
                                  quantity=100, completed_at='2024-02-15 00:00:00')
        self.widget.service = application.ArchiveService(database)
        before = database.read_bytes()
        with (patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value=self.temp.name),
              patch('saga.desktop.history.QMessageBox.question', return_value=QMessageBox.StandardButton.No)):
            self.widget.save_report_button.click()
        outputs = list(Path(self.temp.name).glob('Saga-report-*'))
        self.assertEqual(len(outputs), 1)
        verify_package(outputs[0])
        evidence = json.loads((outputs[0] / 'evidence.json').read_text())
        self.assertEqual(evidence['summary']['completions'], 2)
        self.assertEqual(evidence['measurements'][0]['total'], 5)
        self.assertEqual(database.read_bytes(), before)
