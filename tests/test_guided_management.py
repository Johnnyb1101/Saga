import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, export, reads, writes


class GuidedManagementTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'management.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def menu(self, answers):
        with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=answers),
              patch.object(cli, 'refresh_if_stale', side_effect=AssertionError('inspection refresh')),
              contextlib.redirect_stdout(io.StringIO()) as output):
            status = cli.main(['--db', str(self.path)])
        return status, output.getvalue()

    def series(self, project=None, title='Routine'):
        task = writes.add_task(self.con, title, 'work', project_id=project,
                               due_date='2020-01-01', repeat='monthly')
        return task, reads.get_task(self.con, task)['recurrence_id']

    def test_empty_menus_and_inspection_do_not_write_or_export(self):
        before = list(self.con.iterdump())
        with patch.object(export, 'write_all', side_effect=AssertionError('inspection export')):
            status, output = self.menu(['10', '1', '0', '11', '0'])
        self.assertEqual(status, 0)
        self.assertIn('No projects', output)
        self.assertIn('No recurring series', output)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_create_project_explicit_fields_invalid_date_and_edit_preview(self):
        status, output = self.menu(['10', '2', '', 'Draft', '1', 'Description',
                                   '3', 'bad', '2020-01-01', '3', '2020-02-01',
                                   '2', '1', 'Final', '1', '0', '0'])
        self.assertEqual(status, 0)
        row = reads.project_details(self.con, reads.projects(self.con)[0]['id'])
        self.assertEqual((row['name'], row['description'], row['start_date'], row['deadline']),
                         ('Final', 'Description', '2020-01-01', '2020-02-01'))
        self.assertIn('valid date', output)
        self.assertIn('REVIEW BEFORE SAVING', output)

    def test_discard_and_interrupt_create_no_project(self):
        for answers in (['10', '2', 'Draft', '2', '1', '1', '3', '0', '0'],
                        ['10', '2', 'Draft', ':discard', '0', '0']):
            self.assertEqual(self.menu(answers)[0], 0)
            self.assertEqual(reads.projects(self.con), [])
        for interrupt in (EOFError, KeyboardInterrupt):
            self.assertEqual(self.menu(['10', '2', 'Draft', interrupt])[0], 130)
            self.assertEqual(reads.projects(self.con), [])

    def test_project_search_pagination_duplicate_names_and_task_browsing(self):
        for index in range(10):
            writes.add_project(self.con, 'Same name', description=f'Project {index}')
        project = reads.projects(self.con)[8]['id']
        writes.add_task(self.con, 'Associated task', 'work', project_id=project)
        before = list(self.con.iterdump())
        status, output = self.menu(['10', '1', 'n', '1', '1', '0',
                                   's', f'ID {project}]', '1', '0', 'a', '0', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('page 2 of 2', output)
        self.assertIn('Description: Project 8', output)
        self.assertIn('Associated task', output)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_project_closure_blocked_cancelled_and_confirmed(self):
        project = writes.add_project(self.con, 'Project')
        task = writes.add_task(self.con, 'Open task', 'work', project_id=project)
        status, output = self.menu(['10', '1', '1', '2', '0', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('Cannot close', output)
        writes.cancel_task(self.con, task)
        before = list(self.con.iterdump())
        self.menu(['10', '1', '1', '2', '0', '0', '0', '0'])
        self.assertEqual(list(self.con.iterdump()), before)
        self.menu(['10', '1', '1', '2', '1', '0', '0', '0'])
        self.assertEqual(reads.project_details(self.con, project)['status'], 'done')
        self.assertEqual(reads.get_task(self.con, task)['status'], 'cancelled')
        self.assertEqual(analytics.volume(self.con)['completions'], 0)
        status, output = self.menu(['10', '1', '1', '2', '0', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('already closed', output)

    def test_series_stop_keeps_task_and_completion_does_not_generate_successor(self):
        project = writes.add_project(self.con, 'Project')
        task, series = self.series(project)
        before = dict(reads.get_task(self.con, task))
        self.menu(['11', '1', '0', '0', '0'])
        self.assertEqual(reads.recurrence_details(self.con, series)['status'], 'active')
        status, output = self.menu(['11', '1', '1', '1', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('already stopped', output)
        self.assertIn('Current task', output)
        self.assertIn('anchored 2020-01-01', output)
        self.assertEqual(dict(reads.get_task(self.con, task)), before)
        writes.complete_task(self.con, task, outcome='Finished', completed_at='2020-01-01 12:00:00')
        self.assertIsNone(reads.open_occurrence(self.con, series))
        self.assertEqual(self.con.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)
        self.menu(['11', '1', '0', '0'])

    def test_stale_project_and_new_open_task_are_rejected(self):
        project = writes.add_project(self.con, 'Project')
        expected = dict(reads.project_details(self.con, project))
        writes.add_task(self.con, 'New task', 'work', project_id=project)
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            writes.save_guided_management(self.con, 'close_project', expected, [])
        self.assertEqual(reads.project_details(self.con, project)['status'], 'active')
        self.assertFalse(self.con.in_transaction)

    def test_stale_series_occurrence_and_reschedule_are_rejected(self):
        task, series = self.series()
        expected = dict(reads.recurrence_details(self.con, series))
        tasks = [dict(reads.get_task(self.con, task))]
        writes.reschedule_task(self.con, task, '2020-01-02')
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            writes.save_guided_management(self.con, 'stop_recurrence', expected, tasks)
        tasks = [dict(reads.get_task(self.con, task))]
        writes.complete_task(self.con, task, outcome='Finished', completed_at='2020-01-01 12:00:00')
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            writes.save_guided_management(self.con, 'stop_recurrence', expected, tasks)
        self.assertEqual(reads.recurrence_details(self.con, series)['status'], 'active')

    def test_guarded_actions_roll_back_failures(self):
        project = writes.add_project(self.con, 'Project')
        expected = dict(reads.project_details(self.con, project))
        self.con.executescript("""CREATE TRIGGER fail_close BEFORE UPDATE ON projects
            BEGIN SELECT RAISE(ABORT, 'injected close failure'); END;""")
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected close failure'):
            writes.save_guided_management(self.con, 'close_project', expected, [])
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertFalse(self.con.in_transaction)

    def test_export_failures_report_saved_create_close_and_stop(self):
        with patch.object(db, 'DB_PATH', self.path), patch.object(export, 'write_all', side_effect=OSError('disk failure')):
            status, output = self.menu(['10', '2', 'Project', '2', '1', '1', '1', '0', '0'])
            self.assertEqual(status, 0)
            self.assertIn('database change was saved, but export failed', output)
            self.assertEqual(len(reads.projects(self.con)), 1)
            status, output = self.menu(['10', '1', '1', '2', '1', '0', '0', '0'])
            self.assertEqual(status, 0)
            self.assertIn('database change was saved, but export failed', output)
            task, series = self.series()
            status, output = self.menu(['11', '1', '1', '0', '0'])
            self.assertEqual(status, 0)
            self.assertIn('database change was saved, but export failed', output)
        self.assertEqual(reads.projects(self.con)[0]['status'], 'done')
        self.assertEqual(reads.recurrence_details(self.con, series)['status'], 'stopped')
        self.assertEqual(reads.get_task(self.con, task)['status'], 'open')

    def test_project_changed_during_confirmation_is_not_closed(self):
        project = writes.add_project(self.con, 'Project')
        answers = iter(['10', '1', '1', '2', '1', '0', '0', '0'])
        calls = 0

        def respond(prompt):
            nonlocal calls
            calls += 1
            if calls == 5:
                writes.add_task(self.con, 'Arrived during confirmation', 'work', project_id=project)
            return next(answers)

        status, output = self.menu(respond)
        self.assertEqual(status, 0)
        self.assertIn('selection changed', output)
        self.assertEqual(reads.project_details(self.con, project)['status'], 'active')

    def test_on_hold_project_closes_and_series_failure_rolls_back(self):
        project = writes.add_project(self.con, 'On hold')
        with self.con:
            self.con.execute("UPDATE projects SET status='on_hold' WHERE id=?", (project,))
        self.menu(['10', '1', '1', '2', '1', '0', '0', '0'])
        self.assertEqual(reads.project_details(self.con, project)['status'], 'done')
        task, series = self.series()
        expected = dict(reads.recurrence_details(self.con, series))
        tasks = [dict(reads.get_task(self.con, task))]
        self.con.executescript("""CREATE TRIGGER fail_stop BEFORE UPDATE ON recurrences
            BEGIN SELECT RAISE(ABORT, 'injected stop failure'); END;""")
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected stop failure'):
            writes.save_guided_management(self.con, 'stop_recurrence', expected, tasks)
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertFalse(self.con.in_transaction)
