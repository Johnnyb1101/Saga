import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import cli, db, export, reads, writes


class ConnectionCleanupTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'cleanup.db'
        db.init_db(self.path)

    @contextlib.contextmanager
    def tracked(self, name):
        original = getattr(db, name)
        opened = []

        def connect(*args, **kwargs):
            con = original(*args, **kwargs)
            opened.append(con)
            return con

        try:
            with patch.object(db, name, side_effect=connect):
                yield opened
            self.assertTrue(opened)
            for con in opened:
                with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                    con.execute('SELECT 1')
        finally:
            for con in opened:
                con.close()

    def command(self, *args):
        with (self.tracked('connect'), patch.object(cli, 'refresh_if_stale'),
              contextlib.redirect_stdout(io.StringIO()) as output,
              contextlib.redirect_stderr(io.StringIO()) as errors):
            status = cli.main(['--db', str(self.path), *args])
        return status, output.getvalue(), errors.getvalue()

    def test_empty_read_handlers_close_on_early_return(self):
        for command in ('today', 'list', 'upcoming', 'measure'):
            with self.subTest(command=command):
                self.assertEqual(self.command(command)[0], 0)

    def test_populated_read_handlers_and_measure_registration_close(self):
        self.assertEqual(self.command('measure', 'items')[0], 0)
        with contextlib.closing(db.connect(self.path)) as con:
            project = writes.add_project(con, 'Project', deadline='2000-01-01')
            writes.add_task(con, 'Task', 'work', project_id=project, due_date='2000-01-01')
        for command in ('today', 'list', 'upcoming', 'measure'):
            with self.subTest(command=command):
                self.assertEqual(self.command(command)[0], 0)

    def test_write_handlers_close_without_rolling_back_committed_changes(self):
        self.assertEqual(self.command('project', 'Project')[0], 0)
        self.assertEqual(self.command('add', 'Task', '-c', 'work')[0], 0)
        with contextlib.closing(db.connect(self.path)) as con:
            task = reads.open_tasks(con)[0]['id']
        self.assertEqual(self.command('done', str(task), '--outcome', 'Delivered')[0], 0)
        with contextlib.closing(db.connect(self.path)) as con:
            self.assertEqual(len(reads.projects(con)), 1)
            self.assertEqual(reads.get_task(con, task)['status'], 'done')
            self.assertEqual(reads.completion_list(con)[0]['outcome'], 'Delivered')

    def test_validation_and_integrity_errors_close_connections(self):
        for args in (('done', '999', '--outcome', 'Missing'),
                     ('add', 'Invalid', '-c', 'missing'),
                     ('project', 'Invalid', '--deadline', 'bad-date')):
            with self.subTest(args=args):
                self.assertEqual(self.command(*args)[0], 1)
        self.assertEqual(self.command('measure', 'items')[0], 0)
        self.assertEqual(self.command('measure', 'items')[0], 1)
        with contextlib.closing(db.connect(self.path)) as con:
            self.assertEqual(reads.projects(con), [])
            self.assertEqual(reads.open_tasks(con), [])

    def test_interrupted_prompts_close_without_writing(self):
        with contextlib.closing(db.connect(self.path)) as con:
            task = writes.add_task(con, 'Task', 'work')
        for args in (('add',), ('project',), ('done', str(task))):
            for error in (EOFError, KeyboardInterrupt):
                with (self.subTest(args=args, error=error),
                      patch.object(cli.sys.stdin, 'isatty', return_value=True),
                      patch('builtins.input', side_effect=error)):
                    self.assertEqual(self.command(*args)[0], 130)
        with contextlib.closing(db.connect(self.path)) as con:
            self.assertEqual(len(reads.open_tasks(con)), 1)
            self.assertEqual(reads.projects(con), [])
            self.assertEqual(reads.completion_list(con), [])

    def test_read_failure_propagates_and_closes(self):
        for command, function in (('today', 'overdue'), ('list', 'open_tasks'),
                                  ('upcoming', 'upcoming_deadlines'), ('measure', 'measure_usage')):
            with (self.subTest(command=command), self.tracked('connect'),
                  patch.object(cli, 'refresh_if_stale'),
                  patch.object(reads, function, side_effect=sqlite3.OperationalError('injected read failure')),
                  self.assertRaisesRegex(sqlite3.OperationalError, 'injected read failure')):
                cli.main(['--db', str(self.path), command])

    def test_export_failure_closes_after_preserving_saved_write(self):
        with (patch.object(db, 'DB_PATH', self.path),
              patch.object(export, 'write_all', side_effect=export.ExportError('injected export failure'))):
            for args in (('project', 'Saved project'), ('add', 'Saved task', '-c', 'work')):
                status, _, errors = self.command(*args)
                self.assertEqual(status, 1)
                self.assertIn('Database change was saved', errors)
        with contextlib.closing(db.connect(self.path)) as con:
            self.assertEqual(len(reads.projects(con)), 1)
            self.assertEqual(len(reads.open_tasks(con)), 1)

    def test_schema_rejections_close_connection(self):
        for version in (db.SCHEMA_VERSION - 1, db.SCHEMA_VERSION + 1):
            with sqlite3.connect(self.path) as con:
                con.execute(f'PRAGMA user_version={version}')
            con.close()
            with self.tracked('_open'), self.assertRaisesRegex(ValueError, 'schema version'):
                db.connect(self.path)

    def test_schema_query_failure_and_interruption_close_connection(self):
        for error in (sqlite3.DatabaseError('injected schema failure'), KeyboardInterrupt()):
            with (self.tracked('_open'), patch.object(db, 'schema_version', side_effect=error),
                  self.assertRaises(type(error))):
                db.connect(self.path)

    def test_validated_connection_remains_open_and_caller_controls_transaction(self):
        con = db.connect(self.path)
        try:
            con.execute("INSERT INTO measures(name) VALUES ('Not committed')")
            self.assertTrue(con.in_transaction)
            con.rollback()
            self.assertEqual(con.execute('SELECT count(*) FROM measures').fetchone()[0], 0)
        finally:
            con.close()
