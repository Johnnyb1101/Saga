import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import db, reads, writes


class EndDateTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'end-date.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def create(self, **changes):
        values = {'due_date': '2020-01-01', 'repeat': 'daily', 'end_date': '2020-01-02'}
        values.update(changes)
        return writes.add_task(self.con, 'Bounded task', 'work', **values)

    def test_inclusive_cutoff_for_all_schedule_shapes(self):
        cases = (
            ('daily', 1, None, None, '2020-01-01', '2020-01-02'),
            ('weekly', 1, None, None, '2020-01-01', '2020-01-08'),
            ('weekly', 2, None, None, '2020-01-01', '2020-01-15'),
            ('monthly', 1, None, None, '2020-01-31', '2020-02-29'),
            ('monthly', 1, 1, 15, '2020-01-01', '2020-01-15'),
            ('monthly', 1, 30, 31, '2020-02-29', '2020-03-30'),
        )
        for repeat, interval, first, second, anchor, end in cases:
            with self.subTest(schedule=(repeat, interval, first, second)):
                task = self.create(repeat=repeat, interval=interval, month_day=first,
                                   second_month_day=second, due_date=anchor, end_date=end)
                series = reads.get_task(self.con, task)['recurrence_id']
                writes.complete_task(self.con, task, completed_at='2021-01-01 00:00:00')
                following = reads.open_occurrence(self.con, series)
                self.assertEqual(following['due_date'], end)
                writes.complete_task(self.con, following['id'], completed_at='2021-01-02 00:00:00')
                self.assertIsNone(reads.open_occurrence(self.con, series))
                row = self.con.execute('SELECT status,end_date FROM recurrences WHERE id=?', (series,)).fetchone()
                self.assertEqual(tuple(row), ('stopped', end))

    def test_cutoff_between_occurrences_does_not_generate_later_task(self):
        task = self.create(repeat='weekly', end_date='2020-01-07')
        writes.complete_task(self.con, task)
        self.assertEqual(reads.open_tasks(self.con), [])
        self.assertEqual(reads.recurrences(self.con)[0]['status'], 'stopped')

    def test_rescheduled_current_after_cutoff_is_retained_and_anchor_controls_next(self):
        task = self.create()
        series = reads.get_task(self.con, task)['recurrence_id']
        writes.reschedule_task(self.con, task, '2020-03-01')
        self.assertEqual(reads.get_task(self.con, task)['status'], 'open')
        completion = writes.complete_task(self.con, task, outcome='Late current')
        self.assertEqual(reads.completion_history(self.con, completion)[0]['due_date'], '2020-03-01')
        following = reads.open_occurrence(self.con, series)
        self.assertEqual(following['due_date'], '2020-01-02')
        writes.reschedule_task(self.con, following['id'], '2020-04-01')
        writes.complete_task(self.con, following['id'])
        self.assertIsNone(reads.open_occurrence(self.con, series))

    def test_strict_creation_validation_leaves_no_rows(self):
        for end in ('', '20200102', '2020-1-02', '2020-02-30', '0000-01-01', '2019-12-31', 20200102):
            with self.subTest(end=end), self.assertRaises(ValueError):
                self.create(end_date=end)
        with self.assertRaisesRegex(ValueError, 'requires recurrence'):
            self.create(repeat=None)
        self.assertEqual(reads.recurrences(self.con), [])
        self.assertEqual(reads.open_tasks(self.con), [])

    def test_bounded_overflow_finishes_for_every_schedule_and_huge_interval(self):
        for repeat, first, second in (('daily', None, None), ('weekly', None, None),
                                      ('monthly', None, None), ('monthly', 15, 31)):
            for interval in (1, 9223372036854775807):
                with self.subTest(repeat=repeat, interval=interval, first=first):
                    task = self.create(due_date='9999-12-31', end_date='9999-12-31',
                                       repeat=repeat, interval=interval, month_day=first,
                                       second_month_day=second)
                    writes.complete_task(self.con, task)
                    self.assertEqual(reads.get_task(self.con, task)['status'], 'done')
        self.assertEqual(reads.open_tasks(self.con), [])
        task = self.create(interval=9223372036854775807)
        writes.complete_task(self.con, task)
        self.assertEqual(reads.open_tasks(self.con), [])

    def test_unbounded_overflow_and_validation_errors_still_rollback(self):
        task = self.create(due_date='9999-12-31', end_date=None)
        with self.assertRaisesRegex(ValueError, 'date range'):
            writes.complete_task(self.con, task)
        self.assertEqual(reads.get_task(self.con, task)['status'], 'open')
        bounded = self.create()
        with (patch('saga.writes.occurrence_date', side_effect=ValueError('invalid schedule')),
              self.assertRaisesRegex(ValueError, 'invalid schedule')):
            writes.complete_task(self.con, bounded)
        self.assertEqual(reads.get_task(self.con, bounded)['status'], 'open')
        self.assertEqual(self.con.execute('SELECT count(*) FROM completions').fetchone()[0], 0)

    def test_final_stop_failure_rolls_back_archive_and_current_task(self):
        task = self.create(end_date='2020-01-01')
        self.con.executescript("""CREATE TRIGGER reject_stop BEFORE UPDATE ON recurrences
            BEGIN SELECT RAISE(ABORT, 'injected stop failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected stop failure'):
            writes.complete_task(self.con, task)
        self.assertEqual(reads.get_task(self.con, task)['status'], 'open')
        self.assertEqual(reads.recurrences(self.con)[0]['status'], 'active')
        self.assertEqual(self.con.execute('SELECT count(*) FROM completions').fetchone()[0], 0)

    def test_sql_constraints(self):
        task = self.create()
        series = reads.get_task(self.con, task)['recurrence_id']
        for end in ('', '20200102', '2020-1-02', '2020-02-30', '0000-01-01', '2019-12-31',
                    '2020-01-02 00:00:00', '2020-01-02\x00', b'2020-01-02'):
            with self.subTest(end=end), self.assertRaises(sqlite3.IntegrityError), self.con:
                self.con.execute('UPDATE recurrences SET end_date=? WHERE id=?', (end, series))
        for end in ('2020-01-01', '2020-02-29', '9999-12-31', None):
            with self.con:
                self.con.execute('UPDATE recurrences SET end_date=? WHERE id=?', (end, series))


class EndDateMigrationTests(unittest.TestCase):
    def test_v7_migration_preserves_rows_backup_and_rolls_back_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'legacy.db'
            fixture = Path(__file__).parent / 'fixtures' / 'schema_v2.sql'
            with contextlib.closing(db._open(path)) as con:
                con.executescript(fixture.read_text(encoding='utf-8-sig'))
                for script in sorted(db.MIGRATIONS_PATH.glob('*.sql')):
                    if 2 < int(script.name.split('_')[0]) <= 7:
                        con.executescript(script.read_text(encoding='utf-8'))
                con.executescript("""INSERT INTO recurrences(title,category,frequency,interval,anchor_date)
                    VALUES ('Legacy series','work','daily',1,'2020-01-01');
                    INSERT INTO tasks(title,category,recurrence_id,occurrence_index,due_date)
                    VALUES ('Legacy current','work',1,0,'2020-01-01');
                    INSERT INTO completions(category,outcome) VALUES ('work','Preserved archive');""")
                before = list(con.iterdump())
                old_rows = {table: con.execute(f'SELECT * FROM {table}').fetchall()
                            for table in ('recurrences', 'tasks', 'completions')}
                old_rows = {table: [tuple(row) for row in rows] for table, rows in old_rows.items()}
            original = (db.MIGRATIONS_PATH / '008_recurrence_end_date.sql').read_text(encoding='utf-8')
            scripts = root / 'scripts'
            scripts.mkdir()
            failed = scripts / '008_recurrence_end_date.sql'
            failed.write_text(original + '\nINSERT INTO missing VALUES(1);', encoding='utf-8')
            with patch.object(db, 'MIGRATIONS_PATH', scripts), self.assertRaises(sqlite3.OperationalError):
                db.migrate(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(db.schema_version(con), 7)
                self.assertEqual(list(con.iterdump()), before)
            snapshot = next(root.glob('*.bak'))
            with contextlib.closing(sqlite3.connect(snapshot)) as con:
                self.assertEqual(list(con.iterdump()), before)
                self.assertEqual(db.schema_version(con), 7)
            db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                self.assertEqual(db.schema_version(con), 8)
                for table in ('tasks', 'completions'):
                    self.assertEqual([tuple(row) for row in con.execute(f'SELECT * FROM {table}')], old_rows[table])
                rows = con.execute('SELECT * FROM recurrences').fetchall()
                self.assertEqual([tuple(row)[:-1] for row in rows], old_rows['recurrences'])
                self.assertIsNone(rows[0]['end_date'])
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
                with self.assertRaises(sqlite3.IntegrityError), con:
                    con.execute("UPDATE recurrences SET end_date='2020-02-30'")
                writes.complete_task(con, 1)
                self.assertEqual(reads.open_tasks(con)[0]['due_date'], '2020-01-02')
            self.assertEqual(db.migrate(path), [])
