"""Category lifecycle, history protection, and active write validation."""

import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import application, db, export, reads, writes


class CategoryTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = application.create_archive(self.root / 'archive.db')
        self.service = application.ArchiveService(self.database)

    def test_create_normalizes_name_and_rejects_normalized_duplicates(self):
        self.assertIsNone(self.service.create_category('  Deep Work  ', False))
        self.assertEqual(next(x for x in self.service.category_options()
                              if x['name'] == 'Deep Work'),
                         {'name': 'Deep Work', 'archived': False, 'in_use': False,
                          'counts_toward_review': False})
        for name in ('deep work', ' DEEP   WORK ', '', '   '):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.create_category(name)

    def test_archived_category_keeps_history_and_existing_task_completion(self):
        self.service.create_category('Research')
        with contextlib.closing(db.connect(self.database)) as con:
            task = writes.add_task(con, 'Read source', 'Research', repeat='daily', due_date='2026-01-01')
            completion = writes.add_completion(con, 'Research', 'First note')
            revised = writes.correct_completion(con, completion, 'Clarified', outcome='First note clarified')
        self.service.archive_category('Research')
        self.assertNotIn('Research', self.service.references()['categories'])
        option = next(x for x in self.service.category_options() if x['name'] == 'Research')
        self.assertTrue(option['archived'])
        self.assertTrue(option['in_use'])
        with contextlib.closing(db.connect(self.database)) as con:
            self.assertIn('Research', [r['name'] for r in reads.categories(con)])
            self.assertNotIn('Research', [r['name'] for r in reads.categories(con, active_only=True)])
            with self.assertRaises(ValueError):
                writes.add_task(con, 'New task', 'Research')
            with self.assertRaises(ValueError):
                writes.add_completion(con, 'Research', 'New standalone')
            done = writes.complete_task(con, task, 'Read it')
            self.assertEqual(con.execute('SELECT category FROM completions WHERE id=?',
                                         (done,)).fetchone()[0], 'Research')
            self.assertEqual(con.execute("SELECT count(*) FROM tasks WHERE category='Research'").fetchone()[0], 2)
            self.assertEqual([r['id'] for r in reads.completion_history(con, completion)],
                             [completion, revised])
        with self.assertRaises(ValueError):
            self.service.delete_category('Research')
        self.service.restore_category('Research')
        self.assertIn('Research', self.service.references()['categories'])

    def test_deletion_checks_each_reference_table_including_old_revision(self):
        for source in ('category_roles', 'tasks', 'recurrences', 'completions'):
            with self.subTest(source=source):
                name = f'Used {source}'
                self.service.create_category(name)
                with contextlib.closing(db.connect(self.database)) as con:
                    if source == 'category_roles':
                        writes.add_duty(con, 'Role', name)
                    elif source == 'tasks':
                        writes.add_task(con, 'Task', name)
                    elif source == 'recurrences':
                        task = writes.add_task(con, 'Repeat', name, repeat='daily', due_date='2026-01-01')
                        con.execute('DELETE FROM tasks WHERE id=?', (task,))
                        con.commit()
                    else:
                        first = writes.add_completion(con, name, 'Original')
                        writes.correct_completion(con, first, 'Move current', category='work')
                with self.assertRaises(ValueError):
                    self.service.delete_category(name)
        self.service.create_category('Empty')
        self.assertIsNone(self.service.delete_category('Empty'))
        self.assertNotIn('Empty', [x['name'] for x in self.service.category_options()])

    def test_stale_preview_cannot_save_after_archive(self):
        self.service.create_category('Temporary')
        task_draft = {'title': 'Future', 'category': 'Temporary', 'duty': None,
                      'new_duty': None, 'due_date': None}
        capture_draft = {'category': 'Temporary', 'outcome': 'Done',
                         'measurement_status': 'not_applicable'}
        self.service.preview_task(task_draft)
        self.service.preview_capture(capture_draft)
        self.service.archive_category('Temporary')
        with self.assertRaises(ValueError):
            self.service.save_task(task_draft)
        with self.assertRaises(ValueError):
            self.service.save_capture(capture_draft)
        with contextlib.closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT count(*) FROM completions').fetchone()[0], 0)

    def test_export_failure_does_not_undo_category_change(self):
        service = application.ArchiveService(self.database, self.root / 'exports')
        with patch.object(export, 'write_all', side_effect=export.ExportError('export failed')):
            self.assertEqual(service.create_category('Saved'), 'export failed')
        self.assertIn('Saved', [x['name'] for x in self.service.category_options()])

    def test_role_creation_serializes_with_category_archiving(self):
        with contextlib.closing(db.connect(self.database)) as writer, \
                contextlib.closing(db.connect(self.database)) as archiver:
            archiver.execute('PRAGMA busy_timeout=0')
            require_active = writes.require_active_category

            def archive_after_validation(con, category):
                require_active(con, category)
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    writes.set_category_status(archiver, category, True)

            with patch.object(writes, 'require_active_category', side_effect=archive_after_validation):
                writes.add_duty(writer, 'New role', 'work')
            self.assertEqual(writer.execute("SELECT status FROM categories WHERE name='work'").fetchone()[0],
                             'active')
            self.assertEqual(writer.execute("SELECT duty FROM category_roles WHERE category='work'").fetchone()[0],
                             'New role')
            writes.set_category_status(archiver, 'work', True)

    def test_migration_preserves_existing_categories_and_backs_up(self):
        old = self.root / 'old.db'
        with contextlib.closing(sqlite3.connect(old)) as con:
            con.executescript((Path(__file__).parent / 'fixtures' / 'schema_v4.sql').read_text())
        applied = db.migrate(old)
        self.assertTrue(any('backed up to' in item for item in applied))
        self.assertTrue(any('006_categories.sql' in item for item in applied))
        with contextlib.closing(db.connect(old)) as con:
            self.assertEqual(db.schema_version(con), 8)
            self.assertEqual(con.execute("SELECT name_key,status FROM categories WHERE name='work'").fetchone()[:],
                             ('work', 'active'))
            with contextlib.closing(db.connect(self.database)) as fresh:
                for pragma in ('table_info(categories)', 'index_list(categories)'):
                    migrated = [tuple(row) for row in con.execute(f'PRAGMA {pragma}')]
                    current = [tuple(row) for row in fresh.execute(f'PRAGMA {pragma}')]
                    self.assertEqual(migrated, current, pragma)

    def test_migration_rejects_duplicate_normalized_legacy_names_atomically(self):
        old = self.root / 'duplicate.db'
        with contextlib.closing(sqlite3.connect(old)) as con:
            con.executescript((Path(__file__).parent / 'fixtures' / 'schema_v4.sql').read_text())
            con.execute("INSERT INTO categories(name) VALUES (' WORK ')")
            con.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            db.migrate(old)
        with contextlib.closing(sqlite3.connect(old)) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertNotIn('name_key', [row[1] for row in con.execute('PRAGMA table_info(categories)')])
            self.assertEqual(con.execute('SELECT count(*) FROM categories').fetchone()[0], 6)
        self.assertEqual(len(list(self.root.glob('duplicate-*.bak'))), 1)


if __name__ == '__main__':
    unittest.main()
