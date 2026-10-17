"""Desktop history reports over a disposable archive."""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from saga import analytics, application, db, export, writes


class HistoryServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = db.init_db(self.root / 'history.db')
        self.service = application.ArchiveService(self.database, self.root / 'exports')
        self.con = db.connect(self.database)
        self.addCleanup(self.con.close)
        writes.add_measure(self.con, 'items')
        writes.add_duty(self.con, 'Trainer', 'work')
        writes.add_duty(self.con, 'Trainer', 'home')

    def completion(self, category='work', **changes):
        values = {'outcome': 'Historical work', 'completed_at': '2020-02-29 12:00:00'}
        values.update(changes)
        return writes.add_completion(self.con, category, **values)

    def test_inclusive_boundaries_and_multi_year_dates(self):
        first = self.completion(completed_at='2019-12-31 23:59:59')
        midnight = self.completion(completed_at='2020-02-29 00:00:00',
                                   measure='items', quantity=2)
        last = self.completion(completed_at='2020-02-29 23:59:59',
                               measure='items', quantity=3)
        outside = self.completion(completed_at='2021-01-01 00:00:00')
        leap_day = self.service.history_report('2020-02-29', '2020-02-29')
        self.assertEqual(leap_day['summary']['completions'], 2)
        self.assertEqual([row['id'] for row in leap_day['accomplishments']], [last, midnight])
        self.assertEqual(leap_day['measures'],
                         [{'measure': 'items', 'total': 5, 'occasions': 2}])
        self.assertEqual(leap_day['categories'][0]['completions'], 2)
        years = self.service.history_report('2019-01-01', '2020-12-31')
        self.assertEqual(years['summary']['completions'], 3)
        self.assertIn(first, [row['id'] for row in years['accomplishments']])
        self.assertNotIn(outside, [row['id'] for row in years['accomplishments']])

    def test_correction_moves_date_and_category_without_double_counting(self):
        original = self.completion(duty='Trainer', measure='items', quantity=2)
        current = writes.correct_completion(
            self.con, original, 'Correct period and category', category='home',
            completed_at='2021-03-01 23:59:59', quantity=7)
        old = self.service.history_report('2020-01-01', '2020-12-31')
        self.assertEqual(old['summary']['completions'], 0)
        self.assertEqual(old['accomplishments'], [])
        report = self.service.history_report('2021-03-01', '2021-03-01', 'home', 'Trainer')
        self.assertEqual((report['since'], report['until'], report['category'], report['duty']),
                         ('2021-03-01', '2021-03-01', 'home', 'Trainer'))
        self.assertEqual(report['summary']['completions'], 1)
        self.assertEqual(report['measures'][0]['total'], 7)
        self.assertEqual([row['id'] for row in report['accomplishments']], [current])
        self.assertEqual(report['accomplishments'][0]['supersedes_id'], original)
        self.assertEqual(self.service.history_report(category='work')['summary']['completions'], 0)

    def test_historical_filters_include_archived_category_and_retired_role(self):
        self.completion(duty='Trainer')
        writes.set_category_status(self.con, 'work', True)
        writes.set_role_status(self.con, 'work', 'Trainer', False)
        options = self.service.history_filters()
        self.assertIn('work', options['categories'])
        self.assertIn('Trainer', options['roles'])
        self.assertEqual(self.service.history_report(category='work', duty='Trainer')
                         ['summary']['completions'], 1)
        self.assertEqual(self.service.history_report(category='home', duty='Trainer')
                         ['summary']['completions'], 0)

    def test_empty_archive_and_malformed_scope_validate_before_connection(self):
        empty = self.service.history_report()
        self.assertEqual(empty['summary']['completions'], 0)
        self.assertEqual(empty['accomplishments'], [])
        self.assertEqual(empty['measures'], [])
        invalid = [('2020-2-29', None), ('2020-02-30', None),
                   (None, '2020-01-01T00:00:00'), (123, None),
                   ('2021-01-01', '2020-12-31')]
        with patch.object(application.db, 'connect', side_effect=AssertionError('opened')):
            for since, until in invalid:
                with self.subTest(since=since, until=until), self.assertRaises(ValueError):
                    self.service.history_report(since, until)

    def test_sections_share_snapshot_and_inspection_does_not_export(self):
        first = self.completion(measure='items', quantity=2)
        self.con.execute('PRAGMA journal_mode=WAL')
        original_volume = analytics.volume

        def volume_then_write(con, **scope):
            self.assertTrue(con.in_transaction)
            result = original_volume(con, **scope)
            self.completion(measure='items', quantity=9)
            return result

        with patch.object(analytics, 'volume', side_effect=volume_then_write):
            snapshot = self.service.history_report()
        self.assertEqual(snapshot['summary']['completions'], 1)
        self.assertEqual(snapshot['measures'][0]['total'], 2)
        self.assertEqual([row['id'] for row in snapshot['accomplishments']], [first])

        before = self.database.read_bytes()
        with closing(db.connect(self.database)) as con:
            version = db.schema_version(con)
            rows = [tuple(row) for row in con.execute('SELECT * FROM completions ORDER BY id')]
        with patch.object(export, 'write_all', side_effect=AssertionError('exported')):
            self.service.history_filters()
            self.service.history_report()
        with closing(db.connect(self.database)) as con:
            self.assertEqual(db.schema_version(con), version)
            self.assertEqual([tuple(row) for row in con.execute(
                'SELECT * FROM completions ORDER BY id')], rows)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse((self.root / 'exports').exists())


if __name__ == '__main__':
    unittest.main()
