"""Selected recurrence days through the desktop service."""

import contextlib
import tempfile
import unittest
from pathlib import Path

from saga import application, db, reads, writes


class ScheduleSelectorServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.database = application.create_archive(Path(workspace.name) / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def draft(self, **changes):
        return {'title': 'Review inventory', 'category': 'work',
                'due_date': '2024-01-10', **changes}

    def test_legacy_drafts_keep_their_prepared_shape(self):
        self.assertEqual(self.service.preview_task(self.draft()), {
            'values': {'title': 'Review inventory', 'category': 'work',
                       'duty': None, 'due_date': '2024-01-10'},
            'new_duty': None,
        })
        self.assertEqual(self.service.preview_task(self.draft(repeat='monthly'))['values'], {
            'title': 'Review inventory', 'category': 'work', 'duty': None,
            'due_date': '2024-01-10', 'repeat': 'monthly', 'interval': 1,
        })

    def test_weekday_aligns_first_due_date_and_weekly_interval(self):
        draft = self.draft(repeat='weekly', interval=2, weekday=0)
        prepared = self.service.preview_task(draft)['values']
        self.assertEqual(prepared['due_date'], '2024-01-15')
        self.assertNotIn('weekday', prepared)
        task_id = self.service.save_task(draft).task_id
        with contextlib.closing(db.connect(self.database)) as con:
            first = reads.get_task(con, task_id)
            series = reads.recurrence_details(con, first['recurrence_id'])
            self.assertEqual(first['due_date'], '2024-01-15')
            self.assertEqual(series['anchor_date'], '2024-01-15')
            writes.complete_task(con, task_id, outcome='Done')
            following = reads.open_occurrence(con, series['id'])
            self.assertEqual(following['due_date'], '2024-01-29')
        label = self.service.today(on='2024-01-29')['due_today'][0]['repeat_label']
        self.assertEqual(label, 'Every 2 weeks on Monday')

    def test_monthly_day_starts_on_next_selected_day_and_clamps(self):
        draft = self.draft(due_date='2024-01-31', repeat='monthly', month_day=30)
        self.assertEqual(self.service.preview_task(draft)['values']['due_date'], '2024-02-29')
        task_id = self.service.save_task(draft).task_id
        with contextlib.closing(db.connect(self.database)) as con:
            first = reads.get_task(con, task_id)
            series = reads.recurrence_details(con, first['recurrence_id'])
            self.assertEqual((series['month_day'], series['second_month_day']), (30, None))
            writes.complete_task(con, task_id, outcome='Done')
            self.assertEqual(reads.open_occurrence(con, series['id'])['due_date'], '2024-03-30')
        self.assertEqual(self.service.today(on='2024-03-30')['due_today'][0]['repeat_label'],
                         'Repeats monthly on the 30th')

    def test_monthly_interval_uses_aligned_anchor(self):
        draft = self.draft(due_date='2024-01-20', repeat='monthly',
                           interval=2, month_day=15)
        self.assertEqual(self.service.preview_task(draft)['values']['due_date'], '2024-02-15')
        task_id = self.service.save_task(draft).task_id
        with contextlib.closing(db.connect(self.database)) as con:
            first = reads.get_task(con, task_id)
            writes.complete_task(con, task_id, outcome='Done')
            self.assertEqual(reads.open_occurrence(con, first['recurrence_id'])['due_date'],
                             '2024-04-15')

    def test_twice_monthly_sorts_days_and_skips_duplicate_clamped_date(self):
        draft = self.draft(due_date='2025-02-27', repeat='monthly',
                           month_day=31, second_month_day=30)
        values = self.service.preview_task(draft)['values']
        self.assertEqual((values['due_date'], values['month_day'], values['second_month_day']),
                         ('2025-02-28', 30, 31))
        task_id = self.service.save_task(draft).task_id
        with contextlib.closing(db.connect(self.database)) as con:
            first = reads.get_task(con, task_id)
            series = reads.recurrence_details(con, first['recurrence_id'])
            self.assertEqual((series['month_day'], series['second_month_day']), (30, 31))
            writes.complete_task(con, task_id, outcome='Done')
            second = reads.open_occurrence(con, series['id'])
            self.assertEqual(second['due_date'], '2025-03-30')
            writes.complete_task(con, second['id'], outcome='Done again')
            self.assertEqual(reads.open_occurrence(con, series['id'])['due_date'], '2025-03-31')
        self.assertEqual(self.service.today(on='2025-03-31')['due_today'][0]['repeat_label'],
                         'Twice a month on the 30th and 31st')

    def test_invalid_selectors_are_rejected_without_writes(self):
        invalid = (
            {'weekday': 0},
            {'repeat': 'weekly', 'weekday': True},
            {'repeat': 'weekly', 'weekday': 7},
            {'repeat': 'weekly', 'month_day': 3},
            {'repeat': 'monthly', 'weekday': 0},
            {'repeat': 'monthly', 'month_day': '3'},
            {'repeat': 'monthly', 'month_day': 0},
            {'repeat': 'monthly', 'second_month_day': 4},
            {'repeat': 'monthly', 'month_day': 4, 'second_month_day': 4},
            {'repeat': 'monthly', 'interval': 2, 'month_day': 4, 'second_month_day': 17},
            {'repeat': 'daily', 'month_day': 1},
            {'due_date': '9999-12-31', 'repeat': 'weekly', 'weekday': 0},
            {'due_date': '9999-12-31', 'repeat': 'monthly', 'month_day': 1},
        )
        for selectors in invalid:
            with self.subTest(selectors=selectors), self.assertRaises(ValueError):
                self.service.save_task(self.draft(**selectors))
        with contextlib.closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM recurrences').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
