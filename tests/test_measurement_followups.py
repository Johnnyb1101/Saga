import contextlib
import datetime as dt
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import morning
from saga import analytics, cli, db, export, guided, reads, writes


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'saga.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        self.today = dt.date.today().isoformat()
        writes.add_duty(self.con, 'Operations', 'work')
        writes.add_measure(self.con, 'items')

    def pending(self, **kwargs):
        return writes.add_completion(self.con, 'work', outcome='Invented result', duty='Operations',
                                     measurement_status='unknown', remind_on=self.today, **kwargs)

    def test_states_zero_and_narrative_evidence(self):
        for status, extra in [('measured', {'measure': 'items', 'quantity': 0}),
                              ('not_applicable', {}), ('unknown', {'remind_on': self.today}),
                              ('unspecified', {})]:
            writes.add_completion(self.con, 'work', outcome='Invented result', duty='Operations',
                                  measurement_status=status, **extra)
        self.assertEqual(analytics.measurement_states(self.con), dict.fromkeys(
            ['measured', 'not_applicable', 'unknown', 'unspecified'], 1))
        self.assertEqual(analytics.volume(self.con)['completions'], 4)
        self.assertEqual(analytics.measure_totals(self.con)[0]['total'], 0)
        gaps = {r['measurement_status']: r['reasons'] for r in analytics.evidence_gaps(self.con)}
        self.assertEqual(gaps, {'unknown': ['measurement follow-up pending'],
                                'unspecified': ['measurement unspecified']})

    def test_invalid_states_and_reminders_leave_no_completion(self):
        cases = [{'measurement_status': 'measured'},
                 {'measurement_status': 'not_applicable', 'measure': 'items', 'quantity': 0},
                 {'measurement_status': 'unknown'},
                 {'measurement_status': 'unknown', 'remind_on': '2020-01-01'},
                 {'measurement_status': 'unknown', 'remind_on': '2099-02-30'},
                 {'measurement_status': 'unknown', 'remind_on': '20990101'},
                 {'measurement_status': 'not_applicable', 'remind_on': self.today},
                 {'measurement_status': 'incorrect'}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                writes.add_completion(self.con, 'work', outcome='Invalid', **values)
        self.assertEqual(analytics.volume(self.con)['completions'], 0)
        self.assertEqual(reads.measurement_followups(self.con), [])

    def test_reminder_failure_rolls_back_completion_task_and_next_occurrence(self):
        task = writes.add_task(self.con, 'Recurring', 'work', due_date='2020-01-01', repeat='daily')
        self.con.executescript("""CREATE TRIGGER reject_reminder BEFORE INSERT ON measurement_followups
            BEGIN SELECT RAISE(ABORT, 'injected reminder failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected reminder failure'):
            writes.complete_task(self.con, task, measurement_status='unknown', remind_on=self.today)
        self.assertEqual(reads.get_task(self.con, task)['status'], 'open')
        self.assertEqual(len(reads.open_tasks(self.con)), 1)
        self.assertEqual(analytics.volume(self.con)['completions'], 0)
        self.con.execute('DROP TRIGGER reject_reminder')
        writes.complete_task(self.con, task, measurement_status='unknown', remind_on=self.today)
        self.assertEqual(len(reads.measurement_followups(self.con)), 1)
        self.assertEqual(reads.open_tasks(self.con)[0]['occurrence_index'], 1)

    def test_delayed_measurement_preserves_actual_early_and_on_time_dates(self):
        for actual in ['2020-01-01 12:00:00', '2020-01-02 23:59:59']:
            task = writes.add_task(self.con, 'Entered much later', 'work', due_date='2020-01-02')
            completion = writes.complete_task(self.con, task, outcome='Finished', completed_at=actual,
                                              measurement_status='unknown', remind_on=self.today)
            row = reads.measurement_followups(self.con)[0]
            with self.con:
                self.con.execute("UPDATE measurement_followups SET remind_on='2020-02-01' WHERE id=?", (row['id'],))
            revision = writes.resolve_followup(self.con, row['id'], completion, 'Measurement received',
                                               measurement_status='measured', measure='items', quantity=5)
            current = reads.completion_history(self.con, revision)[-1]
            self.assertEqual(current['completed_at'], actual)
            self.assertEqual(current['due_date'], '2020-01-02')
            with self.assertRaisesRegex(ValueError, 'changed or was resolved'):
                writes.resolve_followup(self.con, row['id'], completion, 'Duplicate',
                                        measurement_status='not_applicable')
        rate = analytics.on_time_rate(self.con)[0]
        self.assertEqual((rate['evaluated'], rate['on_time'], rate['pct']), (2, 2, 100))
        self.assertEqual(analytics.volume(self.con)['completions'], 2)
        self.assertEqual(reads.measurement_followups(self.con), [])
        self.assertEqual(analytics.volume(self.con, '2020-01-01', '2020-01-02')['completions'], 2)

    def test_explicit_date_correction_changes_timing_without_extra_completion(self):
        task = writes.add_task(self.con, 'Forgot to record', 'work', due_date='2020-01-02')
        cid = writes.complete_task(self.con, task, outcome='Finished', completed_at='2020-01-03 00:00:00',
                                   measurement_status='unknown', remind_on=self.today)
        self.assertEqual(analytics.on_time_rate(self.con)[0]['on_time'], 0)
        row = reads.measurement_followups(self.con)[0]
        writes.resolve_followup(self.con, row['id'], cid, 'Correct actual completion date',
                                measurement_status='not_applicable', completed_at='2020-01-01 00:00:00')
        self.assertEqual(analytics.on_time_rate(self.con)[0]['on_time'], 1)
        self.assertEqual(analytics.volume(self.con)['completions'], 1)
        self.assertEqual(self.con.execute('SELECT completed_at FROM completions WHERE id=?', (cid,)).fetchone()[0],
                         '2020-01-03 00:00:00')

    def test_correction_moves_reminder_and_rejects_stale_resolution(self):
        cid = self.pending()
        reminder = dict(reads.measurement_followups(self.con)[0])
        new = writes.correct_completion(self.con, cid, 'Clarify', outcome='More detailed result')
        current = reads.measurement_followups(self.con)[0]
        self.assertEqual((current['id'], current['completion_id']), (reminder['id'], new))
        with self.assertRaises(ValueError):
            writes.resolve_followup(self.con, current['id'], cid, 'Stale', measurement_status='not_applicable')
        with self.assertRaises(ValueError):
            writes.correct_completion(self.con, new, 'Forget reminder', measurement_status='unspecified')
        writes.correct_completion(self.con, new, 'Narrative is sufficient', measurement_status='not_applicable')
        self.assertEqual(reads.measurement_followups(self.con), [])
        self.assertEqual(analytics.volume(self.con)['completions'], 1)

    def test_resolving_failure_rolls_back_new_unit_revision_and_reminder(self):
        cid = self.pending()
        row = reads.measurement_followups(self.con)[0]
        before = list(self.con.iterdump())
        self.con.executescript("""CREATE TRIGGER reject_resolution BEFORE UPDATE ON measurement_followups
            BEGIN SELECT RAISE(ABORT, 'injected resolution failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected resolution failure'):
            writes.resolve_followup(self.con, row['id'], cid, 'New number', new_measure='new units',
                                    quantity=4, measurement_status='measured')
        self.con.execute('DROP TRIGGER reject_resolution')
        self.assertEqual(list(self.con.iterdump()), before)

    def test_reschedule_and_due_boundary(self):
        self.pending()
        row = reads.measurement_followups(self.con)[0]
        before = [tuple(r) for r in self.con.execute('SELECT * FROM completions')]
        self.assertEqual(len(reads.measurement_followups(self.con, due_only=True, on=self.today)), 1)
        future = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        writes.reschedule_followup(self.con, row['id'], future)
        self.assertEqual(reads.measurement_followups(self.con, due_only=True, on=self.today), [])
        self.assertEqual(len(reads.measurement_followups(self.con, due_only=True, on=future)), 1)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM completions')], before)
        with self.assertRaises(ValueError):
            writes.reschedule_followup(self.con, row['id'], '2020-01-01')

    def test_reminders_appear_in_today_morning_startup_and_exports(self):
        # CLI commands normally exit their process; track their connections in this in-process test.
        original_connect = db.connect
        def tracked_connect(*args, **kwargs):
            con = original_connect(*args, **kwargs)
            self.addCleanup(con.close)
            return con
        connection_patch = patch.object(db, 'connect', side_effect=tracked_connect)
        connection_patch.start()
        self.addCleanup(connection_patch.stop)
        self.pending(completed_at='2020-01-01 00:00:00', flagged=True)
        real_main = cli.main
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['--db', str(self.path), 'today']), 0)
        self.assertIn('MEASUREMENT FOLLOW-UPS DUE', out.getvalue())
        with (patch.object(morning.cli, 'main', side_effect=lambda argv: real_main(['--db', str(self.path), *argv])),
              contextlib.redirect_stdout(io.StringIO()) as out):
            self.assertEqual(morning.brief(), 0)
        self.assertIn('MEASUREMENT FOLLOW-UPS DUE', out.getvalue())
        with (patch.object(guided.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=['0']), contextlib.redirect_stdout(io.StringIO()) as out):
            self.assertEqual(guided.run(self.path), 0)
        self.assertIn('1 measurement follow-up(s) due or overdue', out.getvalue())
        destination = self.path.parent / 'exports'
        export.write_all(self.con, destination)
        brief = json.loads((destination / 'brief.json').read_text(encoding='utf-8'))
        review = json.loads((destination / 'review.json').read_text(encoding='utf-8'))
        self.assertEqual((brief['schema_version'], review['schema_version']), (2, 3))
        self.assertEqual(brief['measurement_followups'][0]['completed_at'], '2020-01-01 00:00:00')
        self.assertEqual(review['measurement_states']['unknown'], 1)
        self.assertEqual(review['flagged'][0]['measurement_status'], 'unknown')

    def run_followup(self, answers):
        with (patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()),
              patch.object(guided, 'saved')):
            guided.manage_followups(self.con, self.path)

    def test_guided_resolve_number_na_and_reschedule(self):
        self.pending(completed_at='2020-01-01 12:00:00')
        self.run_followup(['1', '3', self.today, '1'])
        self.assertEqual(len(reads.measurement_followups(self.con)), 1)
        self.run_followup(['1', '1', '1', '3', '0', 'Number confirmed', '1'])
        self.assertEqual(reads.measurement_followups(self.con), [])
        current = self.con.execute('SELECT * FROM current_completions').fetchone()
        self.assertEqual((current['quantity'], current['completed_at']), (0, '2020-01-01 12:00:00'))
        self.pending()
        self.run_followup(['1', '2', '1', 'Narrative evidence', '1'])
        self.assertEqual(reads.measurement_followups(self.con), [])
        self.assertEqual(analytics.volume(self.con)['completions'], 2)

    def test_guided_unknown_requires_explicit_valid_date_and_can_discard(self):
        with (patch('builtins.input', side_effect=['4', '', '2020-01-01', self.today]),
              contextlib.redirect_stdout(io.StringIO()) as out):
            value = guided.measurement(self.con)
        self.assertEqual((value.status, value.remind_on), ('unknown', self.today))
        self.assertIn('Please answer explicitly', out.getvalue())
        self.assertIn('today or later', out.getvalue())
        self.assertEqual(analytics.volume(self.con)['completions'], 0)

    def test_guided_unknown_capture_and_correct_actual_date(self):
        # One registered unit: unknown is option 4. Discarding saves neither record nor reminder.
        answers = ['4', 'Invented accomplishment', '2', '2020-01-03', '1', '4', self.today, '1', '3']
        with (patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()),
              self.assertRaises(guided.Cancel)):
            guided.capture(self.con, self.path)
        self.assertEqual(reads.measurement_followups(self.con), [])
        self.assertEqual(analytics.volume(self.con)['completions'], 0)
        answers[-1] = '1'
        with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
            guided.capture(self.con, self.path)
        self.assertEqual(reads.measurement_followups(self.con)[0]['completed_at'], '2020-01-03 00:00:00')
        self.run_followup(['1', '2', '2', '2', '2020-01-01', 'Actual date corrected', '1'])
        self.assertEqual(reads.measurement_followups(self.con), [])
        current = self.con.execute('SELECT * FROM current_completions').fetchone()
        self.assertEqual((current['completed_at'], current['measurement_status']),
                         ('2020-01-01 00:00:00', 'not_applicable'))

    def test_direct_commands_create_postpone_resolve_and_report(self):
        def run(*args, code=0):
            result = subprocess.run([sys.executable, '-B', str(db.ROOT / 'main.py'), '--db', str(self.path), *args],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, code, result.stderr)
            return result.stdout
        run('log', 'Direct result', '-c', 'work', '--date', '2020-01-01',
            '--measurement-status', 'unknown', '--remind-on', self.today)
        row = reads.measurement_followups(self.con)[0]
        self.assertIn('Direct result', run('followups'))
        run('reschedule-followup', str(row['id']), '--date', self.today)
        run('correct', str(row['completion_id']), '--reason', 'Number received', '--measure', 'items', '--quantity', '0')
        self.assertEqual(reads.measurement_followups(self.con), [])
        self.assertIn('measured: 1', run('review', '--since', '2020-01-01', '--until', '2020-01-01'))
        run('log', 'Narrative', '-c', 'work', '--measurement-status', 'not_applicable')
        self.assertEqual(analytics.volume(self.con)['completions'], 2)


class MeasurementMigrationTests(unittest.TestCase):
    def legacy(self, path):
        with contextlib.closing(sqlite3.connect(path)) as con:
            fixture = Path(__file__).parent / 'fixtures' / 'schema_v4.sql'
            con.executescript(fixture.read_text(encoding='utf-8-sig'))
            con.executescript("""INSERT INTO measures(name) VALUES ('items');
                INSERT INTO completions(category,outcome,measure,quantity) VALUES ('work','Original','items',0);
                INSERT INTO completions(category,outcome,supersedes_id,correction_reason)
                  VALUES ('work','Revised',1,'Remove wrong measure');""")
            return con.execute('SELECT * FROM completions').fetchall()

    def test_preserves_all_original_columns_and_classifies_without_inventing_reminders(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.db'
            before = self.legacy(path)
            db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                rows = con.execute('SELECT * FROM completions ORDER BY id').fetchall()
                self.assertEqual([tuple(r)[:-1] for r in rows], before)
                self.assertEqual([r['measurement_status'] for r in rows], ['measured', 'unspecified'])
                self.assertEqual(reads.measurement_followups(con), [])
                self.assertEqual(analytics.volume(con)['completions'], 1)
                with self.assertRaises(sqlite3.IntegrityError), con:
                    con.execute("UPDATE completions SET outcome='tampered'")
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.migrate(path), [])

    def test_failed_migration_restores_archive_guard_data_schema_and_version_then_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'legacy.db'
            self.legacy(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                before = list(con.iterdump())
            scripts = root / 'migrations'
            scripts.mkdir()
            script = scripts / '005_measurement_followups.sql'
            original = (db.MIGRATIONS_PATH / script.name).read_text(encoding='utf-8')
            script.write_text(original + '\nINSERT INTO missing VALUES(1);', encoding='utf-8')
            with patch.object(db, 'MIGRATIONS_PATH', scripts):
                with self.assertRaises(sqlite3.OperationalError):
                    db.migrate(path)
                with contextlib.closing(sqlite3.connect(path)) as con:
                    self.assertEqual(list(con.iterdump()), before)
                    self.assertEqual(db.schema_version(con), 4)
                script.write_text(original, encoding='utf-8')
                db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                self.assertEqual(db.schema_version(con), 5)
