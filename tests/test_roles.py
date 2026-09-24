import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, guided, reads, writes


class RoleTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.path = self.root / "roles.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def test_category_scoping_normalization_and_retired_duplicates(self):
        writes.add_duty(self.con, "Training Support", "work")
        for name in ("training support", "  Training   Support ", "TRAINING\tSUPPORT"):
            with self.assertRaisesRegex(ValueError, "already exists"):
                writes.add_duty(self.con, name, "work")
        writes.add_duty(self.con, "Training Support", "school")
        writes.set_role_status(self.con, "work", "Training Support", False)
        self.assertEqual(reads.duties(self.con, "work"), [])
        self.assertEqual(reads.duties(self.con, "school")[0]['name'], "Training Support")
        with self.assertRaisesRegex(ValueError, "reactivate"):
            writes.add_duty(self.con, "training support", "work")
        writes.set_role_status(self.con, "work", "Training Support", True)
        self.assertEqual(reads.duties(self.con, "work")[0]['status'], "active")

    def test_unicode_casefold_and_blank_name(self):
        writes.add_duty(self.con, "Straße", "work")
        with self.assertRaisesRegex(ValueError, "already exists"):
            writes.add_duty(self.con, "STRASSE", "work")
        with self.assertRaisesRegex(ValueError, "blank"):
            writes.add_duty(self.con, " \t ", "work")

    def test_catalog_counts_current_revisions_in_correct_category(self):
        writes.add_duty(self.con, "Training", "work")
        writes.add_duty(self.con, "Training", "home")
        original = writes.add_completion(self.con, "work", outcome="Invented", duty="Training")
        writes.correct_completion(self.con, original, "Correct category", category="home")
        writes.set_role_status(self.con, "home", "Training", False)
        self.assertEqual([(r['category'], r['completions']) for r in reads.role_catalog(self.con)],
                         [('home', 1), ('work', 0)])
        self.assertEqual(reads.role_catalog(self.con, "home")[0]['status'], 'retired')

    def test_mismatched_assignments_are_rejected_atomically(self):
        writes.add_duty(self.con, "Training", "work")
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, "not assigned"):
            writes.add_task(self.con, "Invented", "home", duty="Training", repeat="daily", due_date="2020-01-01")
        with self.assertRaisesRegex(ValueError, "not assigned"):
            writes.add_completion(self.con, "home", duty="Training")
        self.assertEqual(list(self.con.iterdump()), before)

    def test_retirement_preserves_old_task_completion_and_history(self):
        writes.add_duty(self.con, "Training", "work")
        task = writes.add_task(self.con, "Invented", "work", duty="Training")
        writes.set_role_status(self.con, "work", "Training", False)
        with self.assertRaisesRegex(ValueError, "retired"):
            writes.add_completion(self.con, "work", outcome="New", duty="Training")
        original = writes.complete_task(self.con, task, outcome="Delivered")
        before = dict(self.con.execute("SELECT * FROM completions WHERE id=?", (original,)).fetchone())
        revised = writes.correct_completion(self.con, original, "Clarification", outcome="Delivered safely")
        self.assertEqual(dict(reads.completion_history(self.con, revised)[0]), before)
        self.assertEqual(analytics.volume(self.con, duty="Training")['completions'], 1)
        with self.assertRaisesRegex(ValueError, "not assigned"):
            writes.correct_completion(self.con, revised, "Category change", category="home")
        writes.correct_completion(self.con, revised, "Clear role for new category", category="home", duty=None)

    def test_retiring_active_recurrence_is_blocked_until_stopped(self):
        writes.add_duty(self.con, "Training", "work")
        task = writes.add_task(self.con, "Invented", "work", duty="Training", repeat="daily", due_date="2020-01-01")
        with self.assertRaisesRegex(ValueError, "Stop active recurring"):
            writes.set_role_status(self.con, "work", "Training", False)
        writes.complete_task(self.con, task, outcome="Delivered")
        next_task = reads.open_tasks(self.con)[0]
        writes.stop_recurrence(self.con, next_task['recurrence_id'])
        writes.set_role_status(self.con, "work", "Training", False)
        writes.complete_task(self.con, next_task['id'], outcome="Last occurrence")
        self.assertEqual(reads.open_tasks(self.con), [])

    def test_role_registration_rollback_in_guided_capture(self):
        with self.assertRaises(sqlite3.IntegrityError):
            writes.save_guided(self.con, "log", {"category": "work", "quantity": 1}, new_duty="Training")
        self.assertEqual(reads.duties(self.con), [])
        self.assertEqual(reads.duties(self.con, "work"), [])

    def test_cli_registration_and_lifecycle_require_category(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--db", str(self.path), "duty", "Training"]), 1)
            self.assertEqual(cli.main(["--db", str(self.path), "duty", "Training", "-c", "work"]), 0)
            self.assertEqual(cli.main(["--db", str(self.path), "retire-role", "Training", "-c", "work"]), 0)
            self.assertEqual(cli.main(["--db", str(self.path), "activate-role", "Training", "-c", "work"]), 0)
        self.assertEqual(reads.duties(self.con, "work")[0]['name'], "Training")

    def test_guided_role_picker_filters_category_and_searches(self):
        writes.add_duty(self.con, "Home only", "home")
        writes.add_duty(self.con, "Training", "work")
        with (patch('builtins.input', side_effect=['s', 'train', '1']),
              contextlib.redirect_stdout(io.StringIO()) as output):
            role = guided.reference(self.con, 'duty', 'work')
        self.assertEqual(role.value, 'Training')
        self.assertNotIn('Home only', output.getvalue())

    def test_guided_category_edit_requires_role_reselection(self):
        writes.add_duty(self.con, "Training", "work")
        answers = ['2', 'Invented', '4', '3', '1', '3', '1', '2', '2', '5', '1', '1', '0']
        with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(cli.main(['--db', str(self.path)]), 0)
        task = reads.open_tasks(self.con)[0]
        self.assertEqual((task['category'], task['duty']), ('home', None))
        self.assertIn('Category changed', output.getvalue())

    def test_guided_role_management_adds_and_retires(self):
        answers = ['7', '4', '1', 'Training', '1', '2', '1', '1', '0', '0']
        with (patch.object(cli.sys.stdin, 'isatty', return_value=True),
              patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(cli.main(['--db', str(self.path)]), 0)
        self.assertEqual(reads.duties(self.con, 'work', True)[0]['status'], 'retired')


class RoleMigrationTests(unittest.TestCase):
    def legacy(self, path):
        with contextlib.closing(sqlite3.connect(path)) as con:
            con.executescript((Path(__file__).parent / 'fixtures/schema_v3.sql').read_text(encoding='utf-8'))
            con.executescript("""
                INSERT INTO duties(name) VALUES ('Training'), (' training '), ('Unused'), ('Other');
                INSERT INTO tasks(title,category,duty) VALUES ('Invented', 'work', 'Training');
                INSERT INTO completions(category,duty,outcome) VALUES ('work','Training','Original');
                INSERT INTO completions(category,duty,outcome,supersedes_id,correction_reason)
                    VALUES ('home','Other','Corrected',1,'Changed context');
                INSERT INTO recurrences(title,category,duty,frequency,interval,anchor_date)
                    VALUES ('Invented series','work',' training ','daily',1,'2020-01-01');
                INSERT INTO tasks(title,category,duty) VALUES ('Invented school', 'school', 'Training');
            """)
            return {t: con.execute(f'SELECT * FROM {t}').fetchall() for t in ('tasks','completions','recurrences','duties')}

    def test_migration_preserves_records_and_marks_conflicts_and_unused_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.db'
            before = self.legacy(path)
            db.migrate(path)
            with contextlib.closing(db.connect(path)) as con:
                for table, rows in before.items():
                    self.assertEqual([tuple(r)[:-1] if table == 'completions' else tuple(r)
                                      for r in con.execute(f'SELECT * FROM {table}')], rows)
                work = reads.duties(con, 'work', True)
                self.assertEqual([r['status'] for r in work], ['needs_review', 'needs_review'])
                self.assertEqual(reads.duties(con, 'school')[0]['name'], 'Training')
                self.assertEqual(reads.unassigned_roles(con)[0]['name'], 'Unused')
                writes.set_role_status(con, 'work', 'Training', True)
                with self.assertRaisesRegex(ValueError, 'Conflicting active'):
                    writes.set_role_status(con, 'work', ' training ', True)
                writes.add_duty(con, 'Unused', 'home')
                self.assertEqual(reads.unassigned_roles(con), [])
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_fresh_and_migrated_role_structure_match(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.db'
            fresh = Path(folder) / 'fresh.db'
            self.legacy(path)
            db.migrate(path)
            db.init_db(fresh)
            with contextlib.closing(db.connect(path)) as old, contextlib.closing(db.connect(fresh)) as new:
                for pragma in ('table_info', 'foreign_key_list', 'index_list'):
                    sql = f'PRAGMA {pragma}(category_roles)'
                    self.assertEqual([tuple(r) for r in old.execute(sql)], [tuple(r) for r in new.execute(sql)])

    def test_migration_failure_rolls_back_all_links_then_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'legacy.db'
            self.legacy(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                before = list(con.iterdump())
            scripts = root / 'migrations'
            scripts.mkdir()
            original = (db.MIGRATIONS_PATH / '004_category_roles.sql').read_text(encoding='utf-8')
            script = scripts / '004_category_roles.sql'
            script.write_text(original + '\nINSERT INTO missing VALUES (1);', encoding='utf-8')
            with patch.object(db, 'MIGRATIONS_PATH', scripts):
                with self.assertRaises(sqlite3.OperationalError):
                    db.migrate(path)
                with contextlib.closing(sqlite3.connect(path)) as con:
                    self.assertEqual(list(con.iterdump()), before)
                script.write_text(original, encoding='utf-8')
                db.migrate(path)
            with contextlib.closing(sqlite3.connect(path)) as con:
                self.assertEqual(db.schema_version(con), 4)
            self.assertEqual(len(list(root.glob('*.bak'))), 2)


if __name__ == '__main__':
    unittest.main()
