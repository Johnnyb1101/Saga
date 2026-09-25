import contextlib
import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, export, guided, reads, writes


class ReviewTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'review.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        for category in ('work', 'home'):
            writes.add_duty(self.con, 'Training', category)
        writes.add_measure(self.con, 'items')
        writes.add_measure(self.con, 'hours')

    def completion(self, category='work', **kwargs):
        return writes.add_completion(self.con, category, outcome='Invented result',
                                     completed_at='2020-02-29 23:59:59', **kwargs)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            status = cli.main(['--db', str(self.path), *args])
        return status, output.getvalue(), errors.getvalue()

    def run_menu(self, answers):
        with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=answers),
              patch.object(cli, 'refresh_if_stale', side_effect=AssertionError('inspection refresh')),
              patch.object(export, 'write_all', side_effect=AssertionError('inspection export')),
              contextlib.redirect_stdout(io.StringIO()) as output):
            status = cli.main(['--db', str(self.path)])
        return status, output.getvalue()

    def test_groups_reconcile_and_preserve_retired_and_unassigned_roles(self):
        self.completion(duty='Training', measure='items', quantity=0, flagged=True)
        self.completion('home', duty='Training', measure='items', quantity=4)
        self.completion(measurement_status='not_applicable')
        self.completion(duty='Training', measurement_status='unknown',
                        remind_on=dt.date.today().isoformat())
        self.completion(duty='Training', measure='hours', quantity=1.5)
        self.completion()
        writes.set_role_status(self.con, 'work', 'Training', False)
        groups = analytics.category_role_breakdown(self.con)
        self.assertEqual(len(groups), 3)
        total = analytics.volume(self.con)
        for field in ('completions', 'review_counting', 'flagged', 'with_a_number'):
            self.assertEqual(sum(g[field] for g in groups), total[field])
        work = next(g for g in groups if (g['category'], g['duty']) == ('work', 'Training'))
        self.assertEqual(work['measurement_states'],
                         {'measured': 2, 'unknown': 1, 'not_applicable': 0, 'unspecified': 0})
        self.assertEqual(work['measures'], [{'measure': 'hours', 'total': 1.5, 'occasions': 1},
                                          {'measure': 'items', 'total': 0, 'occasions': 1}])
        self.assertIsNone(work['pct'])
        self.assertEqual(sum(g['completions'] for g in groups if g['category'] == 'work'), 5)
        for state, count in analytics.measurement_states(self.con).items():
            self.assertEqual(sum(g['measurement_states'][state] for g in groups), count)
        report = export.build_review(self.con)
        self.assertEqual(report['schema_version'], 4)
        self.assertEqual(report['by_category_role'], groups)
        self.assertEqual(self.con.execute('PRAGMA user_version').fetchone()[0], 5)

    def test_every_query_uses_category_role_and_inclusive_dates(self):
        selected = self.completion(duty='Training', flagged=True, measure='items', quantity=2)
        self.completion('home', duty='Training', flagged=True, measure='items', quantity=99)
        self.completion(flagged=True)
        scope = ('2020-02-29', '2020-02-29', 'Training', 'work')
        self.assertEqual(analytics.volume(self.con, *scope)['completions'], 1)
        self.assertEqual(analytics.measure_totals(self.con, *scope)[0]['total'], 2)
        self.assertEqual(analytics.measurement_states(self.con, *scope)['measured'], 1)
        self.assertEqual([r['category'] for r in analytics.completions_by_category(self.con, *scope)], ['work'])
        self.assertEqual([r['id'] for r in analytics.flagged_work(self.con, *scope)], [selected])
        self.assertEqual([r['id'] for r in reads.completion_list(self.con, *scope)], [selected])
        self.assertEqual(analytics.evidence_gaps(self.con, *scope), [])
        self.assertEqual(analytics.on_time_rate(self.con, *scope), [])
        self.assertEqual(len(analytics.category_role_breakdown(self.con, *scope)), 1)
        self.assertEqual(analytics.volume(self.con, duty='Training')['completions'], 2)
        self.assertEqual(analytics.volume(self.con, unassigned=True)['completions'], 1)
        self.assertEqual(len(analytics.evidence_gaps(self.con, unassigned=True)), 1)
        self.assertEqual(analytics.category_role_breakdown(self.con, since='2020-03-01'), [])

    def test_corrections_move_groups_and_periods_without_double_counting(self):
        original = self.completion(duty='Training', flagged=True)
        current = writes.correct_completion(self.con, original, 'Correct context', category='home',
                                            completed_at='2020-03-01 00:00:00')
        self.assertEqual(analytics.category_role_breakdown(self.con, until='2020-02-29'), [])
        self.assertEqual(analytics.category_role_breakdown(self.con, category='work'), [])
        groups = analytics.category_role_breakdown(self.con, category='home', duty='Training')
        self.assertEqual(groups[0]['completions'], 1)
        self.assertEqual(groups[0]['review_counting'], 0)
        self.assertEqual([r['id'] for r in analytics.evidence_gaps(self.con, category='home')], [current])
        self.assertEqual(len(reads.completion_history(self.con, current)), 2)

    def test_timing_uses_saved_deadlines_and_excludes_undated_work(self):
        ids = []
        for day in ('2020-02-28', '2020-02-29', '2020-03-01'):
            task = writes.add_task(self.con, 'Deadline work', 'work', duty='Training', due_date='2020-02-29')
            ids.append(writes.complete_task(self.con, task, outcome='Finished', completed_at=f'{day} 12:00:00'))
        self.completion(duty='Training')
        writes.correct_completion(self.con, ids[0], 'Add narrative', outcome='Finished and verified')
        group = analytics.category_role_breakdown(self.con, category='work', duty='Training')[0]
        self.assertEqual((group['completions'], group['on_time'], group['evaluated'], group['pct']), (4, 2, 3, 66.7))
        rate = analytics.on_time_rate(self.con, category='work', duty='Training')[0]
        self.assertEqual((rate['on_time'], rate['evaluated'], rate['pct']), (2, 3, 66.7))

    def test_cli_filters_and_rejects_invalid_scope_before_refresh(self):
        self.completion(duty='Training', flagged=True)
        for command in ('review', 'completions'):
            status, output, _ = self.run_cli(command, '--category', 'work', '--duty', 'Training')
            self.assertEqual(status, 0)
            self.assertIn('CATEGORY       work', output)
            self.assertIn('ROLE           Training', output)
            for flags in (('--since', '2020-02-30'), ('--until', '20200229'),
                          ('--since', '2020-03-01', '--until', '2020-02-29'),
                          ('--category', 'missing'), ('--duty', 'missing')):
                with patch.object(cli, 'refresh_if_stale', side_effect=AssertionError('invalid scope refresh')):
                    self.assertEqual(self.run_cli(command, *flags)[0], 1)
        status, output, _ = self.run_cli('review', '--check-evidence', '--category', 'home')
        self.assertEqual(status, 0)
        self.assertIn('No evidence gaps', output)
        self.assertEqual(self.run_cli('review', '--unassigned')[0], 0)

    def test_group_measure_totals_use_the_same_numeric_aggregation_as_headlines(self):
        for quantity in (1e16, 1, -1e16):
            self.completion(duty='Training', measure='items', quantity=quantity)
        group = analytics.category_role_breakdown(self.con)[0]
        total = analytics.measure_totals(self.con)[0]
        self.assertEqual(group['measures'][0], dict(total))

    def test_unassigned_filters_apply_to_all_views(self):
        task = writes.add_task(self.con, 'Unassigned task', 'work', due_date='2020-02-29')
        selected = writes.complete_task(self.con, task, outcome='Finished', measure='items', quantity=3,
                                        flagged=True, completed_at='2020-02-29 23:59:59')
        self.completion(duty='Training', flagged=True, measure='items', quantity=99)
        scope = {'category': 'work', 'unassigned': True}
        self.assertEqual(analytics.volume(self.con, **scope)['completions'], 1)
        self.assertEqual(analytics.measure_totals(self.con, **scope)[0]['total'], 3)
        self.assertEqual(analytics.measurement_states(self.con, **scope)['measured'], 1)
        self.assertEqual(analytics.completions_by_category(self.con, **scope)[0]['completions'], 1)
        self.assertEqual(analytics.on_time_rate(self.con, **scope)[0]['pct'], 100)
        for query in (analytics.flagged_work, analytics.evidence_gaps, reads.completion_list):
            self.assertEqual([r['id'] for r in query(self.con, **scope)], [selected])
        self.assertIsNone(analytics.category_role_breakdown(self.con, **scope)[0]['duty'])

    def test_guided_summary_history_and_filters_are_read_only(self):
        original = self.completion(duty='Training')
        current = writes.correct_completion(self.con, original, 'Improve narrative', outcome='Confirmed result')
        writes.set_role_status(self.con, 'work', 'Training', False)
        before = list(self.con.iterdump())
        # Review, all dates, work, historical Training, summary, browse, history, return.
        status, output = self.run_menu(['9', '1', '5', '3', '1', '2', '1', '1', '0', '0', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('BY CATEGORY / ROLE', output)
        self.assertIn(f'COMPLETION {original} (superseded)', output)
        self.assertIn(f'COMPLETION {current} (current)', output)
        self.assertGreaterEqual(output.count('CATEGORY       work'), 3)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_guided_search_pagination_gaps_and_empty_results(self):
        for index in range(10):
            writes.add_completion(self.con, 'work', outcome=f'Result {index:02}', completed_at='2020-02-29 12:00:00')
        # All dates/categories/roles, browse second page, search, clear, return, gaps.
        status, output = self.run_menu(['9', '1', '1', '1', '2', 'n', '1', '0',
                                       's', 'Result 09', '1', '0', 'a', '0', '3', '0',
                                       '4', '2', '2021-01-01', '2021-12-31', '1', '1', '2', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('page 2 of 2', output)
        self.assertIn('Outcome: Result 09', output)
        self.assertIn('missing duty; measurement unspecified', output)
        self.assertIn('No completions in this period.', output)

    def test_guided_filter_back_invalid_dates_and_interrupts(self):
        status, output = self.run_menu(['9', '2', 'invalid', '2020-02-29', '2020-02-01', '2020-02-29',
                                       '0', '1', '1', '0', '1', '2', '3', '0', '0'])
        self.assertEqual(status, 0)
        self.assertIn('Review dates must use valid YYYY-MM-DD', output)
        self.assertIn('No evidence gaps', output)
        for interrupt in (EOFError, KeyboardInterrupt):
            with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
                  patch('builtins.input', side_effect=['9', interrupt]),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(guided.run(self.path), 130)
