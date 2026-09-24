import contextlib
import datetime as dt
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, export, guided, reads, writes


class GuidedCorrectionTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'corrections.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        writes.add_duty(self.con, 'Operations', 'work')
        writes.add_measure(self.con, 'items')
        self.project = writes.add_project(self.con, 'Original project')
        self.task = writes.add_task(self.con, 'Original task', 'work', self.project,
                                   due_date='2020-01-02', duty='Operations')
        self.cid = writes.complete_task(self.con, self.task, outcome='Original result',
                                        completed_at='2020-01-01 12:34:56')

    def snapshot(self, cid=None):
        cid = self.cid if cid is None else cid
        row = dict(self.con.execute('SELECT * FROM current_completions WHERE id=?', (cid,)).fetchone())
        reminder = self.con.execute('SELECT * FROM measurement_followups WHERE completion_id=?', (cid,)).fetchone()
        return row, dict(reminder) if reminder is not None else None

    def current(self, cid=None):
        return dict(reads.completion_history(self.con, self.cid if cid is None else cid)[-1])

    def pending(self):
        cid = writes.add_completion(self.con, 'work', outcome='Pending result',
                                    completed_at='2020-01-01 12:34:56', measurement_status='unknown',
                                    remind_on=dt.date.today().isoformat())
        with self.con:
            self.con.execute("UPDATE measurement_followups SET remind_on='2020-01-03' WHERE completion_id=?", (cid,))
        return cid

    def run_form(self, answers, cid=None):
        selected, _ = self.snapshot(cid)
        with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()) as output:
            result = guided.correct_accomplishment(self.con, self.path, selected)
        return result, output.getvalue()

    def test_atomic_new_references_and_revision_preserve_history_and_tasks(self):
        original, reminder = self.snapshot()
        task_before = dict(reads.get_task(self.con, self.task))
        revision = writes.save_guided_correction(self.con, original, reminder, 'Verified details',
                                                new_duty='Training', new_measure='hours', quantity=0,
                                                measurement_status='measured', outcome='Verified result')
        history = reads.completion_history(self.con, revision)
        self.assertEqual(dict(history[0]), original)
        self.assertEqual((history[-1]['duty'], history[-1]['measure'], history[-1]['quantity']), ('Training', 'hours', 0))
        self.assertEqual(history[-1]['completed_at'], original['completed_at'])
        self.assertEqual(history[-1]['snapshot_source'], original['snapshot_source'])
        self.assertEqual(history[-1]['correction_reason'], 'Verified details')
        self.assertIsNotNone(history[-1]['recorded_at'])
        self.assertEqual(dict(reads.get_task(self.con, self.task)), task_before)
        self.assertEqual(analytics.volume(self.con)['completions'], 1)

    def test_stale_revision_or_reminder_rejects_before_reference_registration(self):
        original, reminder = self.snapshot()
        writes.correct_completion(self.con, self.cid, 'Other edit', outcome='Changed elsewhere')
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, 'superseded'):
            writes.save_guided_correction(self.con, original, reminder, 'Stale draft',
                                          new_duty='New role', new_measure='New unit', quantity=2)
        self.assertEqual(list(self.con.iterdump()), before)
        pending = self.pending()
        original, reminder = self.snapshot(pending)
        writes.reschedule_followup(self.con, reminder['id'], dt.date.today().isoformat())
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, 'reminder changed'):
            writes.save_guided_correction(self.con, original, reminder, 'Stale reminder',
                                          new_duty='New role', outcome='Clarified')
        self.assertEqual(list(self.con.iterdump()), before)

    def test_failure_rolls_back_references_revision_and_reminder(self):
        pending = self.pending()
        original, reminder = self.snapshot(pending)
        self.con.executescript("""CREATE TRIGGER fail_reminder BEFORE UPDATE ON measurement_followups
            BEGIN SELECT RAISE(ABORT, 'injected reminder failure'); END;""")
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected reminder failure'):
            writes.save_guided_correction(self.con, original, reminder, 'Confirmed measure',
                                          new_duty='New role', new_measure='hours', quantity=2,
                                          measurement_status='measured')
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertFalse(self.con.in_transaction)

    def test_invalid_reason_unchanged_future_date_and_nested_transaction_rejected(self):
        original, reminder = self.snapshot()
        for reason, changes in [(' ', {'outcome': 'Changed'}), ('Reason', {'outcome': original['outcome']}),
                                ('Reason', {'completed_at': '2099-01-01 00:00:00'}),
                                ('Reason', {'completed_at': '2020-1-1 00:00:00'})]:
            before = list(self.con.iterdump())
            with self.assertRaises(ValueError):
                writes.save_guided_correction(self.con, original, reminder, reason, **changes)
            self.assertEqual(list(self.con.iterdump()), before)
        self.con.execute('BEGIN')
        with self.assertRaisesRegex(ValueError, 'current transaction'):
            writes.save_guided_correction(self.con, original, reminder, 'Nested', outcome='Changed')
        self.con.rollback()

    def test_form_keeps_retired_role_timestamp_and_overdue_reminder(self):
        writes.set_role_status(self.con, 'work', 'Operations', False)
        result, output = self.run_form(['1', '2', 'Clarified result', '9', 'More detail', '1'])
        self.assertTrue(result)
        self.assertIn('Original result -> Clarified result', output)
        self.assertIn('Unspecified (no measurement decision recorded)', output)
        self.assertEqual(self.current()['duty'], 'Operations')
        self.assertEqual(self.current()['completed_at'], '2020-01-01 12:34:56')
        pending = self.pending()
        _, reminder = self.snapshot(pending)
        result, output = self.run_form(['1', '2', 'Clarified pending result', '9', 'More detail', '1'], pending)
        self.assertTrue(result)
        current = self.current(pending)
        moved = reads.measurement_followups(self.con)[0]
        self.assertEqual((moved['id'], moved['completion_id'], moved['remind_on']),
                         (reminder['id'], current['id'], '2020-01-03'))
        self.assertEqual(current['completed_at'], '2020-01-01 12:34:56')

    def test_discard_unchanged_back_and_final_confirmation_preserve_database(self):
        for answers in (['9', '0'], ['3', '2', '2', 'Draft role', '0'],
                        ['5', '2', '2', 'Draft unit', '4', '0'],
                        ['1', '2', 'Changed', '9', 'Reason', '3'],
                        ['1', ':back', '0'], ['7', ':discard']):
            with self.subTest(answers=answers):
                before = list(self.con.iterdump())
                result, _ = self.run_form(answers)
                self.assertFalse(result)
                self.assertEqual(list(self.con.iterdump()), before)

    def test_category_change_requires_role_and_previews_eligibility(self):
        result, output = self.run_form(['2', '2', '5', '1', '9', 'Wrong area', '1'])
        self.assertTrue(result)
        self.assertIn('Saved review eligibility: Yes -> No', output)
        current = self.current()
        self.assertEqual((current['category'], current['duty'], current['review_counting']), ('home', None, 0))
        self.assertEqual(reads.get_task(self.con, self.task)['category'], 'work')

    def test_historical_context_changes_only_archive_snapshot(self):
        task_before = dict(reads.get_task(self.con, self.task))
        result, output = self.run_form(['7', '1', '2', 'Corrected title',
                                       '2', '2', '2', '2020-01-05', '3', '3', '4', '3', '0',
                                       '9', 'Verified historical context', '1'])
        self.assertTrue(result)
        current = self.current()
        self.assertEqual((current['task_title'], current['due_date'], current['project_name'], current['review_counting']),
                         ('Corrected title', '2020-01-05', None, 0))
        self.assertEqual(dict(reads.get_task(self.con, self.task)), task_before)
        self.assertEqual(reads.projects(self.con)[0]['name'], 'Original project')
        self.assertIn('Saved deadline: 2020-01-02 -> 2020-01-05', output)

    def test_measurement_resolution_new_unit_zero_and_narrative(self):
        for answers, status in [(['5', '2', '2', 'hours', 'nan', '0', '9', 'Result received', '1'], 'measured'),
                                (['5', '2', '1', '9', 'Narrative is enough', '1'], 'not_applicable')]:
            pending = self.pending()
            result, output = self.run_form(answers, pending)
            self.assertTrue(result)
            self.assertEqual(self.current(pending)['measurement_status'], status)
            self.assertEqual(reads.measurement_followups(self.con), [])
            self.assertEqual(self.current(pending)['completed_at'], '2020-01-01 12:34:56')
            if status == 'measured':
                self.assertEqual(self.current(pending)['quantity'], 0)
                self.assertIn('finite number', output)

    def test_unknown_measurement_and_reminder_only_changes(self):
        today = dt.date.today().isoformat()
        result, _ = self.run_form(['5', '2', '4', today, '9', 'Awaiting result', '1'])
        self.assertTrue(result)
        current = self.current()
        self.assertEqual(current['measurement_status'], 'unknown')
        before = list(self.con.iterdump())
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        result, output = self.run_form(['5', '2', '4', tomorrow, '9', '0'], current['id'])
        self.assertFalse(result)
        self.assertIn('reminder-only rescheduling', output)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_completion_date_correction_rejects_future_and_updates_timing(self):
        result, output = self.run_form(['4', '2', '2', '2099-01-01', '2020-01-03', '9', 'Actual finish date', '1'])
        self.assertTrue(result)
        self.assertIn('cannot be in the future', output)
        self.assertEqual(self.current()['completed_at'], '2020-01-03 00:00:00')
        self.assertEqual(analytics.on_time_rate(self.con)[0]['on_time'], 0)

    def test_export_failure_after_save_does_not_repeat_correction(self):
        with patch.object(db, 'DB_PATH', self.path), patch.object(export, 'write_all', side_effect=OSError('disk failure')):
            result, output = self.run_form(['1', '2', 'Confirmed', '9', 'Reason', '1'])
        self.assertTrue(result)
        self.assertIn('database change was saved, but export failed', output)
        self.assertEqual(len(reads.completion_history(self.con, self.cid)), 2)

    def test_review_gap_correction_refreshes_filters_without_extra_count(self):
        before_count = analytics.volume(self.con)['completions']
        answers = ['9', '1', '5', '1', '3', '1', '2',  # review work gaps -> correction
                   '5', '2', '1', '9', 'Narrative is enough', '1', '0', '0']
        with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=answers),
              patch.object(cli, 'refresh_if_stale', side_effect=AssertionError('inspection refresh')),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(cli.main(['--db', str(self.path)]), 0)
        self.assertIn('No evidence gaps found', output.getvalue())
        self.assertGreaterEqual(output.getvalue().count('CATEGORY       work'), 2)
        self.assertEqual(analytics.volume(self.con)['completions'], before_count)

    def test_eof_and_keyboard_interrupt_discard_drafts(self):
        before = list(self.con.iterdump())
        for error in (EOFError, KeyboardInterrupt):
            with self.assertRaises(error):
                self.run_form(['3', '2', '2', 'Draft role', error])
            self.assertEqual(list(self.con.iterdump()), before)

    def test_edit_answers_saves_only_final_role_measure_and_flag(self):
        result, output = self.run_form(['3', '2', '2', 'Draft role',
                                       '5', '2', '2', 'hours', '2.5', '9', 'Confirmed details', '2',
                                       '3', '2', '2', 'Final role', '6', '2', '1', '9', '1'])
        self.assertTrue(result)
        current = self.current()
        self.assertEqual((current['duty'], current['measure'], current['quantity'], current['flagged']),
                         ('Final role', 'hours', 2.5, 1))
        self.assertIsNone(self.con.execute("SELECT 1 FROM duties WHERE name='Draft role'").fetchone())
        self.assertIn('Flag for review: No -> Yes', output)
        self.assertEqual(len(reads.completion_history(self.con, self.cid)), 2)

    def test_stale_form_reports_error_and_discards_unregistered_references(self):
        answers = iter(['3', '2', '2', 'Draft role', '9', 'Confirmed', '1', '0'])
        changed = False

        def respond(prompt):
            nonlocal changed
            answer = next(answers)
            if answer == 'Confirmed' and not changed:
                writes.correct_completion(self.con, self.cid, 'Other correction', outcome='Elsewhere')
                changed = True
            return answer

        result, output = self.run_form(respond)
        self.assertFalse(result)
        self.assertIn('changed or was superseded', output)
        self.assertIsNone(self.con.execute("SELECT 1 FROM duties WHERE name='Draft role'").fetchone())
        self.assertEqual(len(reads.completion_history(self.con, self.cid)), 2)

    def test_category_change_back_does_not_leave_partial_context(self):
        result, _ = self.run_form(['2', '2', '5', ':back', '1', '2', 'Clarified', '9', 'Reason', '1'])
        self.assertTrue(result)
        current = self.current()
        self.assertEqual((current['category'], current['duty'], current['review_counting']), ('work', 'Operations', 1))
