"""EPB/CV package and picker preserve Saga's verified numerical source."""
import json
import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from report_bundle import publish, verify_package
from saga import db, writes

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QDate, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

    from saga.desktop.history import HistoryWidget
    from saga.desktop.report_selection import AccomplishmentSelectionDialog


class FormatPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = db.init_db(self.root / 'source.db')
        with closing(db.connect(self.database)) as con:
            writes.add_measure(con, 'hours')
            self.first = writes.add_completion(con, 'work', 'Invented result', measure='hours', quantity=2.5,
                                               completed_at='2024-02-01 00:00:00')
            self.second = writes.add_completion(con, 'home', 'Another result', measure='hours', quantity=7.5,
                                                completed_at='2024-02-29 23:59:59')

    def test_formats_have_selected_sources_and_full_range_totals(self):
        before = self.database.read_bytes()
        for report_format in ('epb', 'cv'):
            with self.subTest(report_format=report_format):
                output = publish(self.database, self.root / report_format, since='2024-02-01', until='2024-02-29',
                                 report_format=report_format, selected_ids=[self.first])
                manifest = verify_package(output)
                self.assertEqual(manifest['report_format'], report_format)
                preparation = json.loads((output / 'preparation.json').read_text())
                self.assertEqual(preparation['selected_ids'], [self.first])
                self.assertEqual(len(preparation['selected_records']), 1)
                evidence = json.loads((output / 'evidence.json').read_text())
                self.assertEqual(evidence['summary']['completions'], 2)
                self.assertEqual(evidence['measurements'][0]['total'], 10)
                writer = json.loads((output / 'agent-input.json').read_text())
                self.assertEqual(writer['preparation'], preparation)
                self.assertIn(report_format.upper(), (output / 'agent-guide.txt').read_text())
        self.assertEqual(self.database.read_bytes(), before)

    def test_invalid_or_out_of_scope_selection_creates_no_output(self):
        for options in ({'report_format': 'unknown'}, {'report_format': 'epb'},
                        {'report_format': 'cv', 'selected_ids': [True]},
                        {'report_format': 'epb', 'selected_ids': [self.first, self.first]},
                        {'report_format': 'cv', 'selected_ids': [self.second], 'category': 'work'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                publish(self.database, self.root / 'invalid', **options)
            self.assertFalse((self.root / 'invalid').exists())

    def test_changed_selection_snapshot_is_rejected_before_output(self):
        from saga.reporting import build_evidence
        original = build_evidence(self.database)['records'][0].copy()
        original['outcome'] = 'Different source wording'
        with self.assertRaisesRegex(ValueError, 'changed'):
            publish(self.database, self.root / 'stale', report_format='cv',
                    selected_ids=[original['id']], expected_records=[original])
        self.assertFalse((self.root / 'stale').exists())

    def test_draft_can_only_use_verified_selected_measurement(self):
        first = publish(self.database, self.root / 'first', report_format='epb', selected_ids=[self.first])
        preparation = json.loads((first / 'preparation.json').read_text())
        fact_id = next(iter(preparation['facts']))
        evidence = json.loads((first / 'evidence.json').read_text())
        draft = {'schema_version': 1, 'report_id': evidence['report_id'],
                 'preparation_id': preparation['preparation_id'], 'sections': [
            {'heading': 'Recorded result', 'paragraphs': [[{'fact': fact_id}, {'text': ' hours recorded.'}]]}]}
        path = self.root / 'draft.json'
        path.write_text(json.dumps(draft))
        output = publish(self.database, self.root / 'drafted', report_format='epb',
                         selected_ids=[self.first], narrative=path)
        self.assertIn('2.5', (output / 'report.html').read_text())
        with self.assertRaises(ValueError):
            publish(self.database, self.root / 'wrong-selection', report_format='epb',
                    selected_ids=[self.second], narrative=path)
        self.assertFalse((self.root / 'wrong-selection').exists())


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class FormatPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.records = [{'id': 42, 'completed_at': '2024-02-01', 'category': 'work', 'duty': None,
                         'outcome': '<b>Saved result</b>'},
                        {'id': 43, 'completed_at': '2024-02-29', 'category': 'home', 'duty': 'Lead',
                         'outcome': 'Second result'}]

    def test_picker_requires_selection_and_preserves_record_ids(self):
        dialog = AccomplishmentSelectionDialog(self.records, 'epb')
        self.addCleanup(dialog.close)
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.assertFalse(save.isEnabled())
        dialog.records_list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.selected_ids(), [43])
        self.assertTrue(save.isEnabled())
        dialog._check_all(Qt.CheckState.Checked)
        self.assertEqual(dialog.selected_ids(), [42, 43])
        dialog._check_all(Qt.CheckState.Unchecked)
        self.assertFalse(save.isEnabled())

    def test_ui_passes_format_selection_and_visible_filters(self):
        service = SimpleNamespace(database=Path('source.db'), history_report=lambda **scope: {'accomplishments': self.records})
        widget = HistoryWidget(service)
        self.addCleanup(widget.close)
        widget.from_date.setDate(QDate(2024, 2, 1))
        widget.to_date.setDate(QDate(2024, 2, 29))
        for report_format in ('epb', 'cv'):
            widget.report_format.setCurrentIndex(widget.report_format.findData(report_format))
            with (patch('saga.desktop.history.AccomplishmentSelectionDialog') as picker,
                  patch('saga.desktop.history.QFileDialog.getExistingDirectory', return_value='C:/reports'),
                  patch('saga.desktop.history.publish', side_effect=lambda db, out, **opts: out) as save,
                  patch('saga.desktop.history.QMessageBox.question', return_value=QMessageBox.StandardButton.No)):
                picker.return_value.exec.return_value = QDialog.DialogCode.Accepted
                picker.return_value.selected_ids.return_value = [43]
                widget.save_report_button.click()
            self.assertEqual(save.call_args.kwargs['report_format'], report_format)
            self.assertEqual(save.call_args.kwargs['selected_ids'], [43])
            self.assertEqual(save.call_args.kwargs['until'], '2024-02-29')

    def test_cancelled_or_empty_selection_never_saves(self):
        widget = HistoryWidget(SimpleNamespace(database=Path('source.db'), history_report=lambda **s: {'accomplishments': self.records}))
        self.addCleanup(widget.close)
        widget.report_format.setCurrentIndex(1)
        with (patch('saga.desktop.history.AccomplishmentSelectionDialog') as picker,
              patch('saga.desktop.history.QFileDialog.getExistingDirectory') as choose,
              patch('saga.desktop.history.publish') as save):
            picker.return_value.exec.return_value = QDialog.DialogCode.Rejected
            widget.save_report_button.click()
            picker.return_value.exec.return_value = QDialog.DialogCode.Accepted
            picker.return_value.selected_ids.return_value = []
            widget.save_report_button.click()
        choose.assert_not_called()
        save.assert_not_called()

    def test_empty_range_and_load_failure_do_not_open_picker(self):
        service = SimpleNamespace(database=Path('source.db'), history_report=lambda **scope: {'accomplishments': []})
        widget = HistoryWidget(service)
        self.addCleanup(widget.close)
        widget.report_format.setCurrentIndex(1)
        with patch('saga.desktop.history.AccomplishmentSelectionDialog') as picker:
            widget.save_report_button.click()
            self.assertIn('No accomplishments', widget.status_label.text())
            service.history_report = lambda **scope: (_ for _ in ()).throw(ValueError('archive unavailable'))
            widget.save_report_button.click()
            self.assertIn('archive unavailable', widget.status_label.text())
        picker.assert_not_called()
