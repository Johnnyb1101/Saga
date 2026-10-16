"""Desktop service completion of an existing task."""

import contextlib
import datetime as dt
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, reads, writes


class CompletionServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = application.create_archive(self.root / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def draft(self, **changes):
        draft = {'outcome': '  Invented result  ', 'completed_date': None,
                 'measurement_status': 'not_applicable', 'measure': None,
                 'new_measure': None, 'quantity': None, 'remind_on': None,
                 'flagged': False}
        draft.update(changes)
        return draft

    def task(self, title='Invented task', **kwargs):
        with contextlib.closing(db.connect(self.database)) as con:
            task_id = writes.add_task(con, title, 'work', **kwargs)
        return task_id, self.service.task_details(task_id)

    def rows(self, table):
        with contextlib.closing(db.connect(self.database)) as con:
            return [dict(row) for row in con.execute(f'SELECT * FROM {table}')]

    def test_task_details_rejects_missing_or_closed_task(self):
        with self.assertRaisesRegex(ValueError, 'open task'):
            self.service.task_details(999)
        task_id, snapshot = self.task()
        self.assertEqual(snapshot, self.rows('tasks')[0])
        self.service.save_completion(snapshot, self.draft())
        with self.assertRaisesRegex(ValueError, 'open task'):
            self.service.task_details(task_id)

    def test_preview_has_no_writes_and_task_context_is_locked(self):
        with contextlib.closing(db.connect(self.database)) as con:
            writes.add_duty(con, 'Invented role', 'work')
            project = writes.add_project(con, 'Invented project')
        task_id, snapshot = self.task(duty='Invented role', project_id=project,
                                      due_date='2099-01-01')
        before = {table: self.rows(table) for table in ('completions', 'measures', 'duties')}
        draft = self.draft(category='personal', duty='Other role', new_duty='Unwanted role',
                           measurement_status='measured', new_measure='invented items',
                           quantity='2.5')
        prepared = self.service.preview_completion(snapshot, draft)
        self.assertEqual(prepared['new_measure'], 'invented items')
        self.assertEqual(prepared['values']['task_id'], task_id)
        self.assertEqual(prepared['values']['outcome'], 'Invented result')
        self.assertEqual(prepared['values']['quantity'], 2.5)
        self.assertNotIn('category', prepared['values'])
        self.assertNotIn('duty', prepared['values'])
        self.assertEqual(before, {table: self.rows(table) for table in before})
        saved = self.service.save_completion(snapshot, draft)
        row = self.rows('completions')[0]
        self.assertEqual(row['id'], saved.completion_id)
        self.assertEqual((row['category'], row['duty'], row['task_title'], row['due_date'],
                          row['project_name'], row['measure'], row['quantity']),
                         ('work', 'Invented role', 'Invented task', '2099-01-01',
                          'Invented project', 'invented items', 2.5))
        self.assertNotIn('Unwanted role', [row['name'] for row in self.rows('duties')])

    def test_stale_title_category_and_closed_task_do_not_write(self):
        for mutation in ('title', 'category', 'closed'):
            with self.subTest(mutation=mutation):
                task_id, snapshot = self.task(title=f'Invented {mutation}')
                with contextlib.closing(db.connect(self.database)) as con, con:
                    if mutation == 'title':
                        con.execute('UPDATE tasks SET title=? WHERE id=?', ('Changed', task_id))
                    elif mutation == 'category':
                        con.execute('UPDATE tasks SET category=? WHERE id=?', ('personal', task_id))
                    else:
                        con.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
                before = (len(self.rows('completions')), len(self.rows('measures')))
                with self.assertRaisesRegex(ValueError, 'task changed'):
                    self.service.save_completion(snapshot, self.draft(
                        measurement_status='measured', new_measure=f'new {mutation}', quantity='1'))
                self.assertEqual(before, (len(self.rows('completions')), len(self.rows('measures'))))

    def test_atomic_stale_check_rolls_back_new_unit(self):
        task_id, snapshot = self.task()
        original = writes.save_guided

        def mutate_before_write(con, action, values, **kwargs):
            with contextlib.closing(db.connect(self.database)) as other, other:
                other.execute('UPDATE tasks SET title=? WHERE id=?', ('Changed elsewhere', task_id))
            return original(con, action, values, **kwargs)

        with patch.object(writes, 'save_guided', side_effect=mutate_before_write), \
                self.assertRaisesRegex(ValueError, 'task changed'):
            self.service.save_completion(snapshot, self.draft(
                measurement_status='measured', new_measure='unused unit', quantity='1'))
        self.assertEqual(self.rows('completions'), [])
        self.assertNotIn('unused unit', [row['name'] for row in self.rows('measures')])

    def test_archived_category_and_retired_role_can_complete_existing_task(self):
        with contextlib.closing(db.connect(self.database)) as con:
            writes.add_duty(con, 'Invented retired role', 'work')
        task_id, snapshot = self.task(duty='Invented retired role')
        with contextlib.closing(db.connect(self.database)) as con:
            writes.set_role_status(con, 'work', 'Invented retired role', False)
            writes.set_category_status(con, 'work', True)
        saved = self.service.save_completion(snapshot, self.draft())
        row = self.rows('completions')[0]
        self.assertEqual((row['id'], row['task_id'], row['category'], row['duty']),
                         (saved.completion_id, task_id, 'work', 'Invented retired role'))

    def test_failed_completion_rolls_back_new_unit_and_task_change(self):
        _, snapshot = self.task()
        with contextlib.closing(db.connect(self.database)) as con:
            con.executescript("""CREATE TRIGGER reject_completion BEFORE INSERT ON completions
                BEGIN SELECT RAISE(ABORT, 'invented failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'invented failure'):
            self.service.save_completion(snapshot, self.draft(
                measurement_status='measured', new_measure='rolled-back unit', quantity='3'))
        self.assertEqual(self.rows('completions'), [])
        self.assertNotIn('rolled-back unit', [row['name'] for row in self.rows('measures')])
        self.assertEqual(self.rows('tasks')[0]['status'], 'open')

    def test_early_backdated_recurring_completion_advances_once(self):
        _, snapshot = self.task(due_date='2099-01-31', repeat='monthly')
        saved = self.service.save_completion(snapshot, self.draft(completed_date='2020-02-29'))
        row = self.rows('completions')[0]
        self.assertEqual((row['id'], row['completed_at'], row['due_date']),
                         (saved.completion_id, '2020-02-29 00:00:00', '2099-01-31'))
        with contextlib.closing(db.connect(self.database)) as con:
            following = reads.open_occurrence(con, snapshot['recurrence_id'])
        self.assertEqual((following['due_date'], following['occurrence_index']), ('2099-02-28', 1))
        with self.assertRaisesRegex(ValueError, 'task changed'):
            self.service.save_completion(snapshot, self.draft())
        self.assertEqual(len(self.rows('completions')), 1)
        self.assertEqual(len(self.rows('tasks')), 2)

    def test_unknown_measurement_creates_followup_for_undated_task(self):
        _, snapshot = self.task()
        reminder = dt.date.today().isoformat()
        saved = self.service.save_completion(snapshot, self.draft(
            measurement_status='unknown', remind_on=reminder))
        self.assertIsNone(self.rows('completions')[0]['due_date'])
        followup = self.rows('measurement_followups')[0]
        self.assertEqual((followup['completion_id'], followup['remind_on']),
                         (saved.completion_id, reminder))

    def test_invalid_evidence_does_not_write(self):
        _, snapshot = self.task()
        future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        invalid = ({'outcome': ' '}, {'completed_date': '2020-02-30'},
                   {'completed_date': future}, {'measurement_status': 'measured'},
                   {'measurement_status': 'measured', 'new_measure': 'partial'},
                   {'measurement_status': 'measured', 'quantity': '1'},
                   {'measurement_status': 'measured', 'new_measure': 'bad', 'quantity': 'nan'},
                   {'measurement_status': 'measured', 'new_measure': 'bad', 'quantity': math.inf},
                   {'measurement_status': 'unknown'},
                   {'measurement_status': 'not_applicable', 'quantity': '1'})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.save_completion(snapshot, self.draft(**changes))
        self.assertEqual(self.rows('completions'), [])
        self.assertEqual(self.rows('measurement_followups'), [])
        self.assertNotIn('bad', [row['name'] for row in self.rows('measures')])
        self.assertEqual(self.rows('tasks')[0]['status'], 'open')

    def test_export_failure_reports_saved_completion_and_retry(self):
        _, snapshot = self.task()
        service = application.ArchiveService(self.database, self.root / 'exports')
        with patch.object(export, 'write_all', side_effect=export.ExportError('export unavailable')):
            saved = service.save_completion(snapshot, self.draft())
        self.assertEqual(saved.export_error, 'export unavailable')
        self.assertEqual(len(self.rows('completions')), 1)
        service.retry_export()
        self.assertEqual(len(self.rows('completions')), 1)
        with self.assertRaisesRegex(ValueError, 'task changed'):
            service.save_completion(snapshot, self.draft())


if __name__ == '__main__':
    unittest.main()
