"""End dates on recurring tasks through the desktop service."""

import contextlib
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, reads, writes


class RecurrenceEndServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = application.create_archive(self.root / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def draft(self, **changes):
        return {'title': 'Review inventory', 'category': 'work',
                'due_date': '2024-01-10', **changes}

    def counts(self):
        with contextlib.closing(db.connect(self.database)) as con:
            return tuple(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                         for table in ('tasks', 'recurrences'))

    def test_invalid_end_dates_do_not_write(self):
        invalid = ('', '2024-1-10', '2024-02-30', '20240110',
                   True, 20240110, dt.date(2024, 1, 10))
        for end_date in invalid:
            with self.subTest(end_date=end_date), \
                    self.assertRaisesRegex(ValueError, 'End date must use YYYY-MM-DD'):
                self.service.save_task(self.draft(repeat='daily', end_date=end_date))
        with self.assertRaisesRegex(ValueError, 'End date requires a repeat schedule'):
            self.service.save_task(self.draft(end_date='2024-01-10'))
        self.assertEqual(self.counts(), (0, 0))

    def test_end_date_must_follow_aligned_first_occurrence(self):
        for selectors, first in (
                ({'repeat': 'weekly', 'weekday': 0}, '2024-01-15'),
                ({'repeat': 'monthly', 'month_day': 5}, '2024-02-05')):
            with self.subTest(selectors=selectors):
                with self.assertRaisesRegex(ValueError, 'first due date'):
                    self.service.preview_task(self.draft(
                        end_date='2024-01-10', **selectors))
                prepared = self.service.preview_task(self.draft(
                    end_date=first, **selectors))['values']
                self.assertEqual((prepared['due_date'], prepared['end_date']),
                                 (first, first))
        self.assertEqual(self.counts(), (0, 0))

    def test_equal_end_date_is_inclusive_and_appears_in_label(self):
        task_id = self.service.save_task(self.draft(
            repeat='weekly', weekday=0, end_date='2024-01-15')).task_id
        with contextlib.closing(db.connect(self.database)) as con:
            task = reads.get_task(con, task_id)
            series = reads.recurrence_details(con, task['recurrence_id'])
            self.assertEqual(series['end_date'], '2024-01-15')
            self.assertEqual(task['due_date'], '2024-01-15')
        self.assertEqual(self.service.today(on='2024-01-15')['due_today'][0]['repeat_label'],
                         'Repeats weekly on Monday, ends 2024-01-15')
        with contextlib.closing(db.connect(self.database)) as con:
            writes.complete_task(con, task_id, outcome='Done')
            self.assertIsNone(reads.open_occurrence(con, series['id']))

    def test_completion_after_last_allowed_occurrence_stops_series(self):
        task_id = self.service.save_task(self.draft(
            repeat='daily', end_date='2024-01-11')).task_id
        with contextlib.closing(db.connect(self.database)) as con:
            series_id = reads.get_task(con, task_id)['recurrence_id']
            writes.complete_task(con, task_id, outcome='First')
            final = reads.open_occurrence(con, series_id)
            self.assertEqual(final['due_date'], '2024-01-11')
            writes.complete_task(con, final['id'], outcome='Final')
            self.assertIsNone(reads.open_occurrence(con, series_id))

    def test_omitted_end_date_keeps_legacy_prepared_shape_and_label(self):
        values = self.service.preview_task(self.draft(repeat='daily'))['values']
        self.assertNotIn('end_date', values)
        task_id = self.service.save_task(self.draft(repeat='daily')).task_id
        self.assertEqual(self.service.today(on='2024-01-10')['due_today'][0]['repeat_label'],
                         'Repeats daily')
        with contextlib.closing(db.connect(self.database)) as con:
            series_id = reads.get_task(con, task_id)['recurrence_id']
            self.assertIsNone(reads.recurrence_details(con, series_id)['end_date'])
            writes.complete_task(con, task_id, outcome='Done')
            self.assertEqual(reads.open_occurrence(con, series_id)['due_date'],
                             '2024-01-11')

    def test_export_failure_keeps_saved_end_date(self):
        service = application.ArchiveService(self.database, self.root / 'published')
        with patch.object(export, 'write_all',
                          side_effect=export.ExportError('injected failure')) as writer:
            result = service.save_task(self.draft(
                repeat='daily', end_date='2024-01-11'))
        self.assertEqual(writer.call_count, 1)
        self.assertIn('injected failure', result.export_error)
        with contextlib.closing(db.connect(self.database)) as con:
            series_id = reads.get_task(con, result.task_id)['recurrence_id']
            self.assertEqual(reads.recurrence_details(con, series_id)['end_date'],
                             '2024-01-11')
        service.retry_export()
        self.assertTrue((self.root / 'published' / 'manifest.json').exists())
        self.assertEqual(self.counts(), (1, 1))


if __name__ == '__main__':
    unittest.main()
