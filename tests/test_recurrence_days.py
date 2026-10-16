import calendar
import contextlib
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import db, reads, writes
from saga.recurrence import occurrence_date


class SelectedDaysTests(unittest.TestCase):
    def dates(self, anchor, first, second=None, count=5, interval=1):
        return [occurrence_date(anchor, 'monthly', interval, n, first, second)
                for n in range(count)]

    def test_nominal_month_day_survives_initial_february_clamp(self):
        self.assertEqual(self.dates('2021-02-28', 31, count=4),
                         ['2021-02-28', '2021-03-31', '2021-04-30', '2021-05-31'])
        self.assertEqual(self.dates('2020-02-29', 30, count=3),
                         ['2020-02-29', '2020-03-30', '2020-04-30'])

    def test_twice_monthly_starting_on_second_day(self):
        self.assertEqual(self.dates('2021-01-20', 5, 20),
                         ['2021-01-20', '2021-02-05', '2021-02-20', '2021-03-05', '2021-03-20'])

    def test_clamping_deduplicates_and_restores_both_days(self):
        self.assertEqual(self.dates('2021-01-30', 30, 31, count=7),
                         ['2021-01-30', '2021-01-31', '2021-02-28', '2021-03-30',
                          '2021-03-31', '2021-04-30', '2021-05-30'])
        self.assertEqual(self.dates('2020-02-29', 29, 31, count=3),
                         ['2020-02-29', '2020-03-29', '2020-03-31'])

    def test_cycle_algorithm_matches_independent_calendar_enumeration(self):
        for first, second, interval in ((1, 15, 1), (28, 31, 1), (30, 31, 2), (29, 31, 12)):
            expected = []
            for offset in range(60):
                year, month = divmod(1999 * 12 + offset * interval, 12)
                days = sorted({min(day, calendar.monthrange(year + 1, month + 1)[1])
                               for day in (first, second)})
                expected.extend(dt.date(year + 1, month + 1, day).isoformat() for day in days)
            anchor = expected[1]
            with self.subTest(days=(first, second), interval=interval):
                self.assertEqual(self.dates(anchor, first, second, len(expected) - 1, interval), expected[1:])
        # Leap-year counts repeat after 400 years, including century exceptions.
        self.assertEqual(occurrence_date('2000-01-30', 'monthly', 1, 7600, 30, 31), '2400-01-30')

    def test_invalid_days_and_incompatible_fields(self):
        for first, second in ((0, None), (32, None), (True, None), (1.5, None),
                              (None, 15), (15, 15), (20, 5), (1, 32), (1, True)):
            with self.subTest(days=(first, second)), self.assertRaises(ValueError):
                occurrence_date('2020-01-01', 'monthly', 1, 0, first, second)
        for frequency in ('daily', 'weekly'):
            with self.assertRaises(ValueError):
                occurrence_date('2020-01-01', frequency, 1, 0, 1)
        with self.assertRaisesRegex(ValueError, 'anchor'):
            occurrence_date('2020-01-02', 'monthly', 1, 0, 1, 15)

    def test_huge_indexes_and_end_of_range(self):
        for index in (1, 10**100):
            with self.assertRaisesRegex(ValueError, 'date range'):
                occurrence_date('9999-12-31', 'monthly', 1, index, 15, 31)
        with self.assertRaisesRegex(ValueError, 'date range'):
            occurrence_date('2020-01-15', 'monthly', 2**63 - 1, 2, 15, 31)
        self.assertEqual(occurrence_date('9999-12-15', 'monthly', 1, 1, 15, 31), '9999-12-31')


class SelectedDaysWritesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'test.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def test_completion_retains_schedule_and_atomic_failure(self):
        task = writes.add_task(self.con, 'Two dates', 'home', due_date='2021-02-28',
                               repeat='monthly', month_day=30, second_month_day=31)
        series = reads.get_task(self.con, task)['recurrence_id']
        self.con.executescript("""CREATE TRIGGER reject_next AFTER INSERT ON tasks
            WHEN NEW.occurrence_index=1
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected failure'):
            writes.complete_task(self.con, task, outcome='Rolled back')
        self.assertEqual(reads.get_task(self.con, task)['status'], 'open')
        self.assertEqual(self.con.execute('SELECT count(*) FROM completions').fetchone()[0], 0)
        self.con.execute('DROP TRIGGER reject_next')
        writes.complete_task(self.con, task, outcome='Done')
        following = reads.open_occurrence(self.con, series)
        self.assertEqual(following['due_date'], '2021-03-30')
        writes.complete_task(self.con, following['id'])
        self.assertEqual(reads.open_occurrence(self.con, series)['due_date'], '2021-03-31')

    def test_no_repeat_rejects_day_fields_without_writing(self):
        with self.assertRaises(ValueError):
            writes.add_task(self.con, 'Invalid', 'home', month_day=1)
        self.assertEqual(reads.open_tasks(self.con), [])

    def test_sql_constraints_reject_incompatible_days(self):
        task = writes.add_task(self.con, 'Daily', 'home', due_date='2020-01-01', repeat='daily')
        series = reads.get_task(self.con, task)['recurrence_id']
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute('UPDATE recurrences SET month_day=1 WHERE id=?', (series,))


class SelectedDaysMigrationTests(unittest.TestCase):
    def test_v6_records_preserved_and_failed_migration_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'legacy.db'
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript((Path(__file__).parent / 'fixtures' / 'schema_v2.sql').read_text(encoding='utf-8-sig'))
            with patch.object(db, 'SCHEMA_VERSION', 6):
                db.migrate(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.executescript("""INSERT INTO recurrences(title, category, frequency, interval, anchor_date)
                    VALUES ('Legacy', 'home', 'monthly', 1, '2021-02-28');
                    INSERT INTO tasks(title, category, due_date, recurrence_id, occurrence_index)
                    VALUES ('Legacy', 'home', '2021-02-28', 1, 0);
                    INSERT INTO completions(category, outcome) VALUES ('home', 'Unchanged archive');""")
                before = list(con.iterdump())
                rows = {table: con.execute('SELECT * FROM ' + table).fetchall()
                        for table in ('recurrences', 'tasks', 'completions')}
            scripts = root / 'scripts'
            scripts.mkdir()
            original = (db.MIGRATIONS_PATH / '007_recurrence_days.sql').read_text(encoding='utf-8')
            (scripts / '007_failure.sql').write_text(original + '\nINSERT INTO missing VALUES (1);', encoding='utf-8')
            with patch.object(db, 'MIGRATIONS_PATH', scripts), self.assertRaises(sqlite3.OperationalError):
                db.migrate(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(list(con.iterdump()), before)
                self.assertEqual(db.schema_version(con), 6)
            db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                self.assertEqual([tuple(row) for row in con.execute('SELECT * FROM recurrences')],
                                 [row + (None, None, None) for row in rows['recurrences']])
                for table in ('tasks', 'completions'):
                    self.assertEqual([tuple(row) for row in con.execute('SELECT * FROM ' + table)], rows[table])
                writes.complete_task(con, 1)
                self.assertEqual(reads.open_tasks(con)[0]['due_date'], '2021-03-28')
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
