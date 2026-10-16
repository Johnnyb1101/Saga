"""Desktop task creation and complete open-task sections."""

import contextlib
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, writes


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = application.create_archive(self.root / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def draft(self, **changes):
        draft = {'title': 'Invented task', 'category': 'work', 'duty': None,
                 'new_duty': None, 'due_date': None}
        draft.update(changes)
        return draft

    def rows(self, table):
        with contextlib.closing(db.connect(self.database)) as con:
            return [dict(row) for row in con.execute(f'SELECT * FROM {table}')]

    def test_preview_normalizes_without_writing_and_accepts_past_due_date(self):
        draft = self.draft(title='  Invented overdue task  ', new_duty='  Planner  ',
                           due_date='2020-02-29')
        before = {table: self.rows(table) for table in ('tasks', 'duties', 'category_roles')}
        preview = self.service.preview_task(draft)
        self.assertEqual(preview, {
            'values': {'title': 'Invented overdue task', 'category': 'work',
                       'duty': None, 'due_date': '2020-02-29'},
            'new_duty': 'Planner',
        })
        self.assertEqual(before, {table: self.rows(table) for table in before})

    def test_invalid_drafts_leave_no_writes(self):
        invalid = (
            {'title': '   '}, {'category': 'missing'},
            {'duty': 'Missing'}, {'duty': 'Missing', 'new_duty': 'New'},
            {'new_duty': ''}, {'new_duty': '   '},
            {'due_date': ''}, {'due_date': '2020-02-30'},
            {'due_date': '2020-2-1'}, {'due_date': 20200101},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.save_task(self.draft(**changes))
        for table in ('tasks', 'duties', 'category_roles'):
            self.assertEqual(self.rows(table), [], table)

    def test_save_registers_new_role_and_task_atomically(self):
        saved = self.service.save_task(self.draft(new_duty='Planner', due_date='2020-02-29'))
        self.assertIsInstance(saved, application.TaskSaveResult)
        self.assertIsInstance(saved.task_id, int)
        self.assertIsNone(saved.export_error)
        self.assertEqual([(row['id'], row['duty'], row['due_date']) for row in self.rows('tasks')],
                         [(saved.task_id, 'Planner', '2020-02-29')])
        self.assertEqual(len(self.rows('duties')), 1)
        self.assertEqual(len(self.rows('category_roles')), 1)

    def test_task_insert_failure_rolls_back_new_role(self):
        with contextlib.closing(sqlite3.connect(self.database)) as con, con:
            con.execute("CREATE TRIGGER reject_task BEFORE INSERT ON tasks "
                        "BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.save_task(self.draft(new_duty='Pending role'))
        for table in ('tasks', 'duties', 'category_roles'):
            self.assertEqual(self.rows(table), [], table)

    def test_export_failure_reports_saved_task_and_retry_does_not_duplicate(self):
        destination = self.root / 'published'
        service = application.ArchiveService(self.database, export_dir=destination)
        with patch.object(export, 'write_all', side_effect=export.ExportError('injected failure')) as writer:
            saved = service.save_task(self.draft())
        self.assertIn('injected failure', saved.export_error)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual([row['id'] for row in self.rows('tasks')], [saved.task_id])
        service.retry_export()
        self.assertEqual(len(self.rows('tasks')), 1)
        self.assertTrue((destination / 'manifest.json').exists())

    def test_today_partitions_open_tasks_at_fourteen_days_and_excludes_closed(self):
        on = dt.date(2026, 1, 10)
        with contextlib.closing(db.connect(self.database)) as con:
            for title, offset in (('overdue', -1), ('today', 0), ('soon', 14), ('later', 15)):
                writes.add_task(con, title, 'work', due_date=(on + dt.timedelta(days=offset)).isoformat())
            writes.add_task(con, 'undated', 'work')
            closed = writes.add_task(con, 'closed later', 'work', due_date='2026-02-01')
            writes.cancel_task(con, closed)
        view = self.service.today(on=on.isoformat())
        for section, title in (('overdue', 'overdue'), ('due_today', 'today'),
                               ('due_soon', 'soon'), ('later', 'later'), ('undated', 'undated')):
            self.assertEqual([row['title'] for row in view[section]], [title])
            self.assertIs(type(view[section][0]), dict)


if __name__ == '__main__':
    unittest.main()
