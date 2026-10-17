"""Independent stress expectations detect report corruption and double counts."""

import copy
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import stress_report
from saga import db, writes
from saga.application import ArchiveService


class StressReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / 'invented.db'
        db.init_db(self.database)
        with closing(db.connect(self.database)) as con:
            writes.add_measure(con, 'hours')
            first = writes.add_completion(con, 'work', 'Invented first', measure='hours',
                                          quantity=2.5, flagged=True, completed_at='2024-02-29 23:59:59')
            second = writes.add_completion(con, 'home', 'Invented second',
                                           measurement_status='not_applicable', completed_at='2024-03-01 00:00:00')
        self.rows = [
            {'id': first, 'completed_at': '2024-02-29 23:59:59', 'category': 'work', 'duty': None,
             'measure': 'hours', 'quantity': 2.5, 'flagged': True, 'review_counting': True,
             'outcome': 'Invented first', 'measurement_status': 'measured', 'task_id': None, 'due_date': None},
            {'id': second, 'completed_at': '2024-03-01 00:00:00', 'category': 'home', 'duty': None,
             'measure': None, 'quantity': None, 'flagged': False, 'review_counting': True,
             'outcome': 'Invented second', 'measurement_status': 'not_applicable', 'task_id': None, 'due_date': None},
        ]
        # Read the existing category setting as fixture setup, then keep the
        # oracle independent from the report methods being verified.
        with closing(db.connect(self.database)) as con:
            self.rows[1]['review_counting'] = bool(con.execute(
                "SELECT counts_toward_review FROM categories WHERE name='home'").fetchone()[0])
        self.ledger = {'seed': 42, 'records': 2, 'since': '2024-02-29', 'until': '2024-03-01',
                       'accomplishments': self.rows}
        self.expected = self.database.with_suffix('.expected.json')
        self.expected.write_text(json.dumps(self.ledger), encoding='utf-8')

    def test_known_totals_and_scope_boundaries(self):
        totals = stress_report.expected_totals(self.rows)
        self.assertEqual(totals['summary']['completions'], 2)
        self.assertEqual(totals['summary']['flagged'], 1)
        self.assertEqual(totals['measures'], {'hours': {'total': 2.5, 'occasions': 1}})
        report = stress_report.analyze(self.database)
        self.assertTrue(report['verified'])
        scopes = {row['name']: row for row in report['scopes']}
        self.assertEqual(scopes['First day inclusive']['completions'], 1)
        self.assertEqual(scopes['Last day inclusive']['completions'], 1)
        self.assertEqual(scopes['Before generated range']['completions'], 0)
        self.assertIn('hours: 2.50', stress_report.format_report(report))

    def test_wrong_summary_unit_total_or_duplicate_evidence_is_detected(self):
        original = ArchiveService(self.database).history_report()
        stress_report.verify_snapshot(original, self.rows)
        for change in ('summary', 'total', 'evidence'):
            report = copy.deepcopy(original)
            if change == 'summary':
                report['summary']['flagged'] += 1
            elif change == 'total':
                report['measures'][0]['total'] += 1
            else:
                report['accomplishments'].append(report['accomplishments'][0])
            with self.subTest(change=change), self.assertRaises(ValueError):
                stress_report.verify_snapshot(report, self.rows)

    def test_changed_input_ledger_fails_without_modifying_archive(self):
        before = self.database.read_bytes()
        self.ledger['accomplishments'][0]['quantity'] = 9
        self.expected.write_text(json.dumps(self.ledger), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Measurement totals'):
            stress_report.analyze(self.database)
        self.assertEqual(self.database.read_bytes(), before)

    def test_lost_measurement_state_or_task_link_is_detected(self):
        original = ArchiveService(self.database).history_report()
        for field, value in (('measurement_status', 'unknown'), ('task_id', 123), ('due_date', '2024-03-01')):
            report = copy.deepcopy(original)
            report['accomplishments'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'evidence'):
                stress_report.verify_snapshot(report, self.rows)

    def test_invalid_or_duplicate_ledger_is_rejected(self):
        self.ledger['accomplishments'][1]['id'] = self.rows[0]['id']
        self.expected.write_text(json.dumps(self.ledger), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate identities'):
            stress_report.analyze(self.database)


if __name__ == '__main__':
    unittest.main()
