"""Changing one open task's due date through the desktop service."""

import contextlib
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, reads, writes


class DueDateServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = application.create_archive(self.root / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def task(self, **kwargs):
        with contextlib.closing(db.connect(self.database)) as con:
            task_id = writes.add_task(con, 'Invented task', 'work', due_date='2020-01-31', **kwargs)
        return task_id, self.service.task_details(task_id)

    def rows(self, table):
        with contextlib.closing(db.connect(self.database)) as con:
            return [dict(row) for row in con.execute(f'SELECT * FROM {table}')]

    def test_preview_validates_strict_iso_and_does_not_write(self):
        task_id, snapshot = self.task()
        before = self.rows('tasks')
        self.assertEqual(self.service.preview_due_date(snapshot, '2020-02-29'), '2020-02-29')
        self.assertIsNone(self.service.preview_due_date(snapshot, None))
        for invalid in ('', '2020-2-1', '2020-02-30', '20200229', 20200229,
                        dt.date(2020, 2, 29)):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'YYYY-MM-DD'):
                self.service.preview_due_date(snapshot, invalid)
        self.assertEqual(self.rows('tasks'), before)
        self.assertEqual(self.service.task_details(task_id), snapshot)

    def test_save_updates_only_due_date_and_can_clear_it(self):
        task_id, snapshot = self.task()
        result = self.service.save_due_date(snapshot, '2020-02-29')
        self.assertEqual(result, application.TaskSaveResult(task_id, None))
        updated = self.service.task_details(task_id)
        self.assertEqual(updated, {**snapshot, 'due_date': '2020-02-29'})
        self.assertEqual(self.rows('completions'), [])
        self.service.save_due_date(updated, None)
        self.assertEqual(self.service.task_details(task_id), {**snapshot, 'due_date': None})

    def test_stale_or_closed_task_rejects_preview_and_save(self):
        for change in ('title', 'category', 'due_date', 'closed', 'missing'):
            with self.subTest(change=change):
                task_id, snapshot = self.task()
                with contextlib.closing(db.connect(self.database)) as con, con:
                    if change == 'missing':
                        con.execute('DELETE FROM tasks WHERE id=?', (task_id,))
                    else:
                        column = 'status' if change == 'closed' else change
                        value = {'title': 'Changed', 'category': 'personal',
                                 'due_date': '2020-02-01', 'closed': 'cancelled'}[change]
                        con.execute(f'UPDATE tasks SET {column}=? WHERE id=?', (value, task_id))
                before = self.rows('tasks')
                for operation in (self.service.preview_due_date, self.service.save_due_date):
                    with self.assertRaisesRegex(ValueError, 'task changed'):
                        operation(snapshot, '2020-03-01')
                self.assertEqual(self.rows('tasks'), before)

    def test_save_rechecks_snapshot_after_preview_inside_write_transaction(self):
        task_id, snapshot = self.task()
        original_preview = self.service.preview_due_date

        def change_after_preview(expected, due):
            normalized = original_preview(expected, due)
            with contextlib.closing(db.connect(self.database)) as con, con:
                con.execute('UPDATE tasks SET title=? WHERE id=?', ('Concurrent change', task_id))
            return normalized

        with patch.object(self.service, 'preview_due_date', side_effect=change_after_preview), \
                self.assertRaisesRegex(ValueError, 'task changed'):
            self.service.save_due_date(snapshot, '2020-03-01')
        self.assertEqual(self.rows('tasks')[0]['due_date'], '2020-01-31')
        self.assertEqual(self.rows('tasks')[0]['title'], 'Concurrent change')

    def test_recurring_change_keeps_anchor_and_next_occurrence(self):
        task_id, snapshot = self.task(repeat='monthly')
        series_before = self.rows('recurrences')
        self.service.save_due_date(snapshot, '2020-06-01')
        self.assertEqual(self.rows('recurrences'), series_before)
        with contextlib.closing(db.connect(self.database)) as con:
            completion_id = writes.complete_task(con, task_id, outcome='Finished')
            next_task = reads.open_occurrence(con, snapshot['recurrence_id'])
            history = reads.completion_history(con, completion_id)[0]
        self.assertEqual(history['due_date'], '2020-06-01')
        self.assertEqual((next_task['due_date'], next_task['occurrence_index']),
                         ('2020-02-29', 1))
        self.assertEqual(self.rows('recurrences'), series_before)

    def test_existing_completion_history_is_unchanged(self):
        first, _ = self.task()
        with contextlib.closing(db.connect(self.database)) as con:
            writes.complete_task(con, first, outcome='Earlier work')
        before = self.rows('completions')
        second, snapshot = self.task()
        self.service.save_due_date(snapshot, '2020-03-01')
        self.assertEqual(self.rows('completions'), before)
        self.assertEqual(self.service.task_details(second)['due_date'], '2020-03-01')

    def test_export_failure_keeps_saved_date_and_retry_only_exports(self):
        task_id, snapshot = self.task()
        destination = self.root / 'published'
        service = application.ArchiveService(self.database, export_dir=destination)
        with patch.object(export, 'write_all', side_effect=export.ExportError('injected failure')) as writer:
            saved = service.save_due_date(snapshot, '2020-03-01')
        self.assertEqual(saved.task_id, task_id)
        self.assertIn('injected failure', saved.export_error)
        self.assertEqual(writer.call_count, 1)
        after_save = self.rows('tasks')
        self.assertEqual(after_save[0]['due_date'], '2020-03-01')
        service.retry_export()
        self.assertEqual(self.rows('tasks'), after_save)
        self.assertTrue((destination / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
