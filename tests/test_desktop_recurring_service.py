"""Recurring task creation and stopping through the desktop service."""

import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, reads, writes


class DesktopRecurringServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = application.create_archive(self.root / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def rows(self, table):
        with contextlib.closing(db.connect(self.database)) as con:
            return [dict(row) for row in con.execute(f'SELECT * FROM {table} ORDER BY id')]

    def draft(self, **updates):
        return {'title': 'Inventory', 'category': 'work', 'due_date': '2020-01-31',
                **updates}

    def recurring(self, **updates):
        result = self.service.save_task(self.draft(repeat='monthly', **updates))
        task = self.service.task_details(result.task_id)
        return task, self.service.recurrence_details(task['id'])

    def test_create_and_preview_preserve_plain_draft_shape(self):
        plain = self.service.preview_task(self.draft())
        self.assertEqual(plain, {
            'values': {'title': 'Inventory', 'category': 'work', 'duty': None,
                       'due_date': '2020-01-31'},
            'new_duty': None,
        })
        prepared = self.service.preview_task(self.draft(repeat='monthly', interval=2))
        self.assertEqual(prepared['values']['repeat'], 'monthly')
        self.assertEqual(prepared['values']['interval'], 2)
        self.assertEqual(self.rows('tasks'), [])
        self.assertEqual(self.rows('recurrences'), [])
        task, series = self.recurring(interval=2)
        self.assertEqual((task['recurrence_id'], task['occurrence_index'], task['due_date']),
                         (series['id'], 0, '2020-01-31'))
        self.assertEqual((series['frequency'], series['interval'], series['anchor_date']),
                         ('monthly', 2, '2020-01-31'))
        self.assertEqual(self.service.task_details(task['id']), task)
        plain_id = self.service.save_task(self.draft(title='Single')).task_id
        self.assertIsNone(self.service.recurrence_details(plain_id))

    def test_recurrence_validation_writes_nothing(self):
        invalid = [
            ({'repeat': 'yearly'}, 'repeat'),
            ({'repeat': 'weekly', 'due_date': None}, 'due date'),
            ({'repeat': 'daily', 'interval': 0}, 'interval'),
            ({'repeat': 'daily', 'interval': True}, 'interval'),
            ({'repeat': 'daily', 'interval': 1.5}, 'interval'),
            ({'interval': 2}, 'Interval requires'),
        ]
        for update, message in invalid:
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, message):
                self.service.save_task(self.draft(**update))
        self.assertEqual(self.rows('tasks'), [])
        self.assertEqual(self.rows('recurrences'), [])

    def test_today_labels_use_current_series_status(self):
        monthly, series = self.recurring(interval=2)
        daily = self.service.save_task(self.draft(
            title='Daily', due_date='2020-01-31', repeat='daily')).task_id
        plain = self.service.save_task(self.draft(title='Plain')).task_id
        view = self.service.today(on='2020-01-31')
        labeled = {row['id']: row['repeat_label'] for row in view['due_today']}
        self.assertEqual(labeled[monthly['id']], 'Every 2 months')
        self.assertEqual(labeled[daily], 'Repeats daily')
        self.assertIsNone(labeled[plain])
        with contextlib.closing(db.connect(self.database)) as con:
            writes.stop_recurrence(con, series['id'])
        view = self.service.today(on='2020-01-31')
        labeled = {row['id']: row['repeat_label'] for row in view['due_today']}
        self.assertEqual(labeled[monthly['id']], 'Stopped: Every 2 months')
        self.assertEqual(self.service.task_details(monthly['id']), monthly)

    def test_stop_cancels_current_without_completion_and_keeps_history(self):
        first, series = self.recurring()
        with contextlib.closing(db.connect(self.database)) as con:
            completion_id = writes.complete_task(con, first['id'], outcome='First done')
            current = dict(reads.open_occurrence(con, series['id']))
        before = self.rows('completions')
        result = self.service.stop_repeating(current, self.service.recurrence_details(current['id']))
        self.assertEqual(result, application.TaskSaveResult(current['id']))
        self.assertEqual(self.rows('completions'), before)
        self.assertEqual(self.rows('tasks')[-1]['status'], 'cancelled')
        self.assertEqual(self.rows('recurrences')[0]['status'], 'stopped')
        with contextlib.closing(db.connect(self.database)) as con:
            self.assertIsNone(reads.open_occurrence(con, series['id']))
            self.assertEqual(reads.completion_history(con, completion_id)[0]['outcome'],
                             'First done')

    def test_stop_rejects_stale_task_or_series_atomically(self):
        for changed in ('task', 'series', 'stopped'):
            with self.subTest(changed=changed):
                task, series = self.recurring(title=f'Inventory {changed}')
                with contextlib.closing(db.connect(self.database)) as con, con:
                    if changed == 'task':
                        con.execute('UPDATE tasks SET title=? WHERE id=?',
                                    ('Updated', task['id']))
                    else:
                        con.execute('UPDATE recurrences SET status=? WHERE id=?',
                                    ('stopped' if changed == 'stopped' else 'active',
                                     series['id']))
                        if changed == 'series':
                            con.execute('UPDATE recurrences SET interval=2 WHERE id=?',
                                        (series['id'],))
                before_tasks = self.rows('tasks')
                before_series = self.rows('recurrences')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.service.stop_repeating(task, series)
                self.assertEqual(self.rows('tasks'), before_tasks)
                self.assertEqual(self.rows('recurrences'), before_series)
        self.assertEqual(self.rows('completions'), [])

    def test_failed_series_stop_rolls_back_task_cancellation(self):
        task, series = self.recurring()
        with contextlib.closing(db.connect(self.database)) as con:
            con.executescript("""CREATE TRIGGER reject_stop BEFORE UPDATE ON recurrences
                BEGIN SELECT RAISE(ABORT, 'injected stop failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected stop failure'):
            self.service.stop_repeating(task, series)
        self.assertEqual(self.rows('tasks')[0]['status'], 'open')
        self.assertEqual(self.rows('recurrences')[0]['status'], 'active')
        self.assertEqual(self.rows('completions'), [])

    def test_export_failure_after_stop_can_retry_without_second_save(self):
        task, series = self.recurring()
        destination = self.root / 'published'
        service = application.ArchiveService(self.database, export_dir=destination)
        with patch.object(export, 'write_all', side_effect=export.ExportError('offline')) as writer:
            result = service.stop_repeating(task, series)
        self.assertEqual(result.task_id, task['id'])
        self.assertIn('offline', result.export_error)
        self.assertEqual(writer.call_count, 1)
        saved_tasks = self.rows('tasks')
        saved_series = self.rows('recurrences')
        service.retry_export()
        self.assertEqual(self.rows('tasks'), saved_tasks)
        self.assertEqual(self.rows('recurrences'), saved_series)
        self.assertTrue((destination / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
