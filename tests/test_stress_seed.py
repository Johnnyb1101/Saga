import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from saga.db import connect
from seed_stress import seed


class StressSeedTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)

    def test_ledger_matches_current_archive_and_keeps_corrections(self):
        result = seed(self.root / 'stress.db', records=120, seed=7, end_date='2026-10-17')
        expected = json.loads(Path(result['expected']).read_text(encoding='utf-8'))
        self.assertEqual((expected['records'], len(expected['accomplishments'])), (120, 120))
        with closing(connect(result['database'])) as con:
            actual = [dict(row) for row in con.execute('SELECT * FROM current_completions ORDER BY id')]
            self.assertEqual(len(actual), 120)
            self.assertGreater(con.execute('SELECT count(*) FROM completions').fetchone()[0], 120)
            by_id = {row['id']: row for row in actual}
            for planned in expected['accomplishments']:
                row = by_id[planned['id']]
                for field in ('completed_at', 'category', 'duty', 'measure', 'quantity',
                              'flagged', 'review_counting', 'outcome', 'measurement_status',
                              'task_id', 'due_date'):
                    self.assertEqual(row[field], planned[field], (planned['id'], field))
            self.assertGreater(con.execute("SELECT count(*) FROM recurrences").fetchone()[0], 0)
            self.assertGreater(con.execute("SELECT count(*) FROM recurrences WHERE second_month_day IS NOT NULL").fetchone()[0], 0)
            self.assertGreater(con.execute("SELECT count(*) FROM tasks WHERE status='cancelled'").fetchone()[0], 0)
            self.assertGreater(con.execute("SELECT count(*) FROM measurement_followups WHERE status='open'").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT status FROM categories WHERE name='legacy'").fetchone()[0], 'archived')
        self.assertTrue(any(item['completed_at'] == '2023-10-17 00:00:00'
                            for item in expected['accomplishments']))
        self.assertTrue(any(item['completed_at'] == '2026-10-17 23:59:59'
                            for item in expected['accomplishments']))
        self.assertTrue(any(item['completed_at'].startswith('2024-02-29')
                            for item in expected['accomplishments']))

    def test_same_seed_and_date_produce_same_evidence(self):
        first = seed(self.root / 'first.db', records=60, seed=19, end_date='2026-10-17')
        second = seed(self.root / 'second.db', records=60, seed=19, end_date='2026-10-17')
        a = json.loads(Path(first['expected']).read_text(encoding='utf-8'))
        b = json.loads(Path(second['expected']).read_text(encoding='utf-8'))
        self.assertEqual(a, b)

    def test_leap_and_boundary_dates_follow_requested_window(self):
        result = seed(self.root / 'earlier.db', records=40, seed=5, end_date='2023-02-28')
        evidence = json.loads(Path(result['expected']).read_text(encoding='utf-8'))['accomplishments']
        stamps = {item['completed_at'] for item in evidence}
        self.assertIn('2020-02-28 00:00:00', stamps)
        self.assertIn('2023-02-28 23:59:59', stamps)
        self.assertIn('2020-02-29 12:00:00', stamps)

    def test_existing_outputs_and_bad_arguments_are_rejected_without_writes(self):
        path = self.root / 'existing.db'
        path.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            seed(path, records=60, end_date='2026-10-17')
        self.assertEqual(path.read_bytes(), b'original')
        other = self.root / 'other.db'
        companion = self.root / 'other.expected.json'
        companion.write_text('original', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            seed(other, records=60, end_date='2026-10-17')
        self.assertFalse(other.exists())
        self.assertEqual(companion.read_text(encoding='utf-8'), 'original')
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar_path = self.root / f'sidecar.db{suffix}'
            sidecar_path.write_bytes(b'keep this sidecar')
            with self.assertRaises(FileExistsError):
                seed(self.root / 'sidecar.db', records=60, end_date='2026-10-17')
            self.assertFalse((self.root / 'sidecar.db').exists())
            self.assertEqual(sidecar_path.read_bytes(), b'keep this sidecar')
            sidecar_path.unlink()
        with self.assertRaises(ValueError):
            seed(self.root / 'bad.db', records=0, end_date='2026-10-17')
        self.assertFalse((self.root / 'bad.db').exists())


if __name__ == '__main__':
    unittest.main()
