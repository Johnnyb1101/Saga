"""Local package publication preserves sources and validates narrative references."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import report_bundle
from saga import db, writes


class ReportBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / 'invented.db'
        db.init_db(self.database)
        with closing(db.connect(self.database)) as con:
            writes.add_measure(con, 'hours')
            writes.add_completion(con, 'work', 'Invented <result> & notes', measure='hours', quantity=2.5)
        self.output = self.root / 'report'

    def test_fixed_package_is_complete_and_keeps_source_unchanged(self):
        before = self.database.read_bytes()
        report_bundle.publish(self.database, self.output)
        manifest = report_bundle.verify_package(self.output)
        self.assertEqual(set(manifest['files']), {'evidence.json', 'agent-input.json', 'agent-guide.txt', 'report.html'})
        self.assertNotIn('Invented &lt;result&gt;', (self.output / 'report.html').read_text(encoding='utf-8'))
        evidence = json.loads((self.output / 'evidence.json').read_text())
        self.assertEqual(evidence['records'][0]['outcome'], 'Invented <result> & notes')
        agent = json.loads((self.output / 'agent-input.json').read_text())
        self.assertNotIn('records', agent)
        self.assertTrue(all('source_record_ids' not in fact for fact in agent['facts'].values()))
        self.assertEqual(self.database.read_bytes(), before)

    def test_existing_folder_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / 'user.txt'
        sentinel.write_text('preserve', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            report_bundle.publish(self.database, self.output)
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_valid_agent_draft_uses_matching_verified_fact(self):
        first = report_bundle.publish(self.database, self.output)
        evidence = json.loads((first / 'evidence.json').read_text())
        fact_id = next(key for key, fact in evidence['facts'].items()
                       if fact['dimension'] == 'total' and fact['measure'] == 'hours')
        narrative = self.root / 'draft.json'
        narrative.write_text(json.dumps({
            'schema_version': 1, 'report_id': evidence['report_id'],
            'sections': [{'heading': 'Recorded hours', 'paragraphs': [[
                {'text': 'The records contain '}, {'fact': fact_id}, {'text': ' hours.'},
            ]]}],
        }), encoding='utf-8')
        published = report_bundle.publish(self.database, self.root / 'with-draft', narrative=narrative)
        self.assertIn('narrative.json', report_bundle.verify_package(published)['files'])
        self.assertIn('draft', (published / 'report.html').read_text(encoding='utf-8').lower())

    def test_bad_narrative_or_reconciliation_failure_creates_no_folder(self):
        narrative = self.root / 'draft.json'
        narrative.write_text(json.dumps({'schema_version': 1, 'report_id': 'wrong', 'sections': []}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'report ID'):
            report_bundle.publish(self.database, self.output, narrative=narrative)
        self.assertFalse(self.output.exists())
        with (patch.object(report_bundle, 'build_evidence', side_effect=ValueError('mismatched totals')),
              self.assertRaisesRegex(ValueError, 'mismatched totals')):
            report_bundle.publish(self.database, self.output)
        self.assertFalse(self.output.exists())

    def test_tampering_is_detected(self):
        report_bundle.publish(self.database, self.output)
        (self.output / 'report.html').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed or is incomplete'):
            report_bundle.verify_package(self.output)


if __name__ == '__main__':
    unittest.main()
