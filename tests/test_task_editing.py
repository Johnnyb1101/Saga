import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import cli, db, reads, writes


class TaskEditingTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'editing.db'
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        writes.add_duty(self.con, 'Training', 'work')
        self.task = writes.add_task(self.con, 'Original', 'work', duty='Training')

    def edit(self, **changes):
        values = dict(reads.get_task(self.con, self.task))
        values.update(changes)
        return writes.edit_task(self.con, self.task,
                                **{k: values[k] for k in ('title', 'category', 'duty', 'project_id')})

    def menu(self, answers):
        with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=answers),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(cli.main(['--db', str(self.path)]), 0)
        return output.getvalue()

    def test_edit_changes_fields_without_touching_deadline_or_archive(self):
        original = writes.add_completion(self.con, 'work', outcome='Historical', duty='Training')
        history = dict(reads.completion_history(self.con, original)[0])
        writes.reschedule_task(self.con, self.task, '2020-01-01')
        project = writes.add_project(self.con, 'New project')
        self.edit(title='Updated', category='home', duty=None, project_id=project)
        task = reads.get_task(self.con, self.task)
        self.assertEqual((task['title'], task['category'], task['duty'], task['project_id']),
                         ('Updated', 'home', None, project))
        self.assertEqual(task['due_date'], '2020-01-01')
        self.assertEqual(dict(reads.completion_history(self.con, original)[0]), history)

    def test_invalid_or_unchanged_edits_leave_task_unchanged(self):
        before = list(self.con.iterdump())
        for changes in ({'title': '  '}, {'category': 'home'}, {'duty': 'Missing'}, {}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.edit(**changes)
            self.assertEqual(list(self.con.iterdump()), before)

    def test_closed_task_and_closed_project_rejected(self):
        project = writes.add_project(self.con, 'Closed project')
        writes.close_project(self.con, project)
        with self.assertRaisesRegex(ValueError, 'cannot add tasks'):
            self.edit(project_id=project)
        writes.cancel_task(self.con, self.task)
        with self.assertRaisesRegex(ValueError, 'open task'):
            self.edit(title='Updated')

    def test_retired_role_can_be_preserved_but_not_newly_assigned(self):
        writes.set_role_status(self.con, 'work', 'Training', False)
        self.edit(title='Updated')
        self.edit(duty=None)
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.edit(duty='Training')

    def test_recurring_edit_leaves_future_template_and_prior_evidence_unchanged(self):
        recurring = writes.add_task(self.con, 'Series title', 'work', duty='Training',
                                   repeat='monthly', due_date='2020-01-31')
        original = writes.complete_task(self.con, recurring, outcome='First occurrence')
        before = dict(reads.completion_history(self.con, original)[0])
        current = reads.open_occurrence(self.con, reads.get_task(self.con, recurring)['recurrence_id'])
        writes.edit_task(self.con, current['id'], 'This occurrence', 'home', None, None)
        writes.complete_task(self.con, current['id'], outcome='Second occurrence')
        following = reads.open_occurrence(self.con, current['recurrence_id'])
        self.assertEqual((following['title'], following['category'], following['duty'], following['due_date']),
                         ('Series title', 'work', 'Training', '2020-03-31'))
        self.assertEqual(dict(reads.completion_history(self.con, original)[0]), before)

    def test_stale_selection_rejects_new_references(self):
        expected = dict(reads.get_task(self.con, self.task))
        self.edit(title='Changed elsewhere')
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, 'changed while'):
            writes.save_guided(self.con, 'edit', {'task_id': self.task, 'title': 'Updated',
                'category': 'work', 'duty': None, 'project_id': None},
                expected_task=expected, new_duty='New role', new_project='New project')
        self.assertEqual(list(self.con.iterdump()), before)

    def test_failed_edit_rolls_back_reference_registration(self):
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, 'blank'):
            writes.save_guided(self.con, 'edit', {'task_id': self.task, 'title': ' ',
                'category': 'work', 'duty': None, 'project_id': None},
                new_duty='New role', new_project='New project')
        self.assertEqual(list(self.con.iterdump()), before)

    def test_guided_title_edit_shows_before_after(self):
        output = self.menu(['1', '1', '5', '2', 'Updated', '1', '1', '1', '1', '0'])
        self.assertIn('Original -> Updated', output)
        self.assertIn('(unchanged)', output)
        self.assertEqual(reads.get_task(self.con, self.task)['title'], 'Updated')

    def test_guided_category_change_requires_explicit_new_role(self):
        output = self.menu(['1', '1', '5', '1', '2', '5', '1', '1', '1', '0'])
        task = reads.get_task(self.con, self.task)
        self.assertEqual((task['category'], task['duty']), ('home', None))
        self.assertIn('work -> home', output)

    def test_guided_discard_does_not_save_task_or_new_references(self):
        before = list(self.con.iterdump())
        self.menu(['1', '1', '5', '1', '1', '2', '2', 'Pending role',
                   '2', '2', 'Pending project', '3', '0', '0'])
        self.assertEqual(list(self.con.iterdump()), before)

    def test_guided_new_references_commit_with_edit(self):
        self.menu(['1', '1', '5', '1', '1', '2', '2', 'New role',
                   '2', '2', 'New project', '1', '0'])
        task = reads.get_task(self.con, self.task)
        self.assertEqual(task['duty'], 'New role')
        self.assertIsNotNone(task['project_id'])
        self.assertIn('New role', [r['name'] for r in reads.duties(self.con, 'work')])


if __name__ == '__main__':
    unittest.main()
