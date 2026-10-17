"""Evidence report reconciliation over current archive revisions."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import db, reporting, writes
from saga.application import ArchiveService


class ReportingTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.database = db.init_db(Path(workspace.name) / 'report.db')
        self.con = db.connect(self.database)
        self.addCleanup(self.con.close)
        for measure in ('hours', 'hours saved', 'items'):
            writes.add_measure(self.con, measure)
        writes.add_duty(self.con, 'No role', 'work')
        writes.add_duty(self.con, 'Builder', 'work')
        writes.add_duty(self.con, 'Builder', 'home')

    def capture(self, category='work', **changes):
        values = {'outcome': 'Result', 'completed_at': '2024-02-29 12:00:00'}
        values.update(changes)
        return writes.add_completion(self.con, category, **values)

    def test_breakdowns_provenance_and_distinct_units(self):
        first = self.capture(measure='hours', quantity=2, duty='Builder')
        second = self.capture(category='home', measure='hours', quantity=3,
                              duty='Builder', completed_at='2024-03-01 00:00:00')
        third = self.capture(measure='hours', quantity=-1, duty=None)
        fourth = self.capture(measure='hours', quantity=0, duty='No role')
        self.capture(measure='hours saved', quantity=4)
        self.capture(measure='items', quantity=8)
        evidence = reporting.build_evidence(self.database)
        self.assertTrue(evidence['verification']['passed'])
        self.assertEqual(evidence['summary']['completions'], 6)
        hours = next(m for m in evidence['measurements'] if m['measure'] == 'hours')
        self.assertEqual((hours['total'], hours['occasions']), (4, 4))
        categories = {g['label']: (g['total'], g['occasions']) for g in hours['by_category']}
        self.assertEqual(categories['work'], (1, 3))
        self.assertEqual(categories['home'], (3, 1))
        self.assertTrue(all(value == (0, 0) for name, value in categories.items()
                            if name not in ('work', 'home')))
        self.assertEqual({g['label']: g['total'] for g in hours['by_month']},
                         {'2024-02': 1, '2024-03': 3})
        no_roles = [g for g in hours['by_role'] if g['label'] in ('No role assigned', 'Role: No role')]
        self.assertEqual(len(no_roles), 2)
        self.assertNotEqual(no_roles[0]['label'], no_roles[1]['label'])
        self.assertNotEqual(no_roles[0]['fact_id'], no_roles[1]['fact_id'])
        self.assertEqual({evidence['facts'][g['fact_id']]['group'] for g in no_roles},
                         {None, 'No role'})
        self.assertEqual(evidence['facts'][hours['fact_id']]['source_record_ids'],
                         sorted([first, second, third, fourth]))
        self.assertEqual(next(m['total'] for m in evidence['measurements']
                              if m['measure'] == 'hours saved'), 4)
        self.assertEqual(evidence['facts'][hours['fact_id']]['display'], '4')
        bounded = reporting.agent_input(evidence)
        self.assertNotIn('records', bounded)
        self.assertNotIn('measurements', bounded)
        self.assertNotIn('source_record_ids', next(iter(bounded['facts'].values())))

    def test_current_correction_and_inclusive_scope(self):
        original = self.capture(measure='hours', quantity=2, duty='Builder',
                                completed_at='2024-02-28 23:59:59')
        revised = writes.correct_completion(self.con, original, 'Correct date and category',
                                            completed_at='2024-02-29 23:59:59',
                                            category='home', quantity=7)
        evidence = reporting.build_evidence(self.database, '2024-02-29', '2024-02-29',
                                            'home', 'Builder')
        self.assertEqual(evidence['scope'], {'since': '2024-02-29', 'until': '2024-02-29',
                                             'category': 'home', 'duty': 'Builder'})
        self.assertEqual([r['id'] for r in evidence['records']], [revised])
        self.assertEqual(evidence['measurements'][0]['total'], 7)
        self.assertEqual(reporting.build_evidence(self.database, '2024-02-28', '2024-02-28')
                         ['summary']['completions'], 0)

    def test_empty_report_and_stable_report_id(self):
        empty = reporting.build_evidence(self.database)
        self.assertEqual(empty['measurements'], [])
        self.assertEqual(empty['summary']['completions'], 0)
        self.assertEqual(empty['report_id'], reporting.build_evidence(self.database)['report_id'])
        self.capture(measure='hours', quantity=0)
        first = reporting.build_evidence(self.database)
        second = reporting.build_evidence(self.database)
        self.assertEqual(first['report_id'], second['report_id'])
        self.assertEqual(set(first['facts']), set(second['facts']))
        self.assertNotEqual(empty['report_id'], first['report_id'])
        self.assertEqual(first['measurements'][0]['total'], 0)

    def test_one_snapshot_and_rejects_corrupt_aggregates(self):
        self.capture(measure='hours', quantity=2)
        original = ArchiveService(self.database).history_report()
        with patch.object(ArchiveService, 'history_report', return_value=original) as history:
            reporting.build_evidence(self.database)
        history.assert_called_once_with(since=None, until=None, category=None, duty=None)
        for section, key, value in (
            ('summary', 'completions', 2),
            ('measures', 'total', 3),
            ('measures', 'occasions', 2),
            ('measures', 'measure', 'hours saved'),
            ('categories', 'completions', 2),
        ):
            broken = copy.deepcopy(original)
            target = broken['summary'] if section == 'summary' else broken[section][0]
            target[key] = value
            with self.subTest(section=section, key=key), patch.object(
                    ArchiveService, 'history_report', return_value=broken), self.assertRaises(ValueError):
                reporting.build_evidence(self.database)

    def test_total_tolerance_is_explicit_and_tight(self):
        self.capture(measure='hours', quantity=0.1)
        original = ArchiveService(self.database).history_report()
        rounded = copy.deepcopy(original)
        rounded['measures'][0]['total'] += 5e-10
        with patch.object(ArchiveService, 'history_report', return_value=rounded):
            self.assertTrue(reporting.build_evidence(self.database)['verification']['passed'])
        rounded['measures'][0]['total'] += 2e-9
        with patch.object(ArchiveService, 'history_report', return_value=rounded), self.assertRaises(ValueError):
            reporting.build_evidence(self.database)

    def test_display_preserves_the_recorded_numeric_precision(self):
        self.capture(measure='hours', quantity=18417.75)
        evidence = reporting.build_evidence(self.database)
        fact = evidence['facts'][evidence['measurements'][0]['fact_id']]
        self.assertEqual(fact['value'], 18417.75)
        self.assertEqual(fact['display'], '18,417.75')

    def test_rejects_duplicate_and_nonfinite_evidence(self):
        self.capture(measure='hours', quantity=2)
        original = ArchiveService(self.database).history_report()
        duplicate = copy.deepcopy(original)
        duplicate['accomplishments'].append(copy.deepcopy(duplicate['accomplishments'][0]))
        with patch.object(ArchiveService, 'history_report', return_value=duplicate), self.assertRaisesRegex(
                ValueError, 'Duplicate source record'):
            reporting.build_evidence(self.database)
        for section, field in (('accomplishments', 'quantity'), ('measures', 'total')):
            for bad in (float('nan'), float('inf'), float('-inf')):
                broken = copy.deepcopy(original)
                broken[section][0][field] = bad
                with self.subTest(section=section, bad=bad), patch.object(
                        ArchiveService, 'history_report', return_value=broken), self.assertRaisesRegex(
                            ValueError, 'finite'):
                    reporting.build_evidence(self.database)


if __name__ == '__main__':
    unittest.main()
