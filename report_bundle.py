"""Publish local readable reports and supporting agent facts without AI calls."""

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from saga.preparation import build_preparation, preparation_guide
from saga.report_html import render_html, validate_narrative
from saga.reporting import agent_input, build_evidence

AGENT_GUIDE = '''Write a draft explanation using agent-input.json.
Do not calculate, round, or invent numerical claims. Reference an existing fact.
Keep exact measurement labels separate. "hours" means recorded hours; do not
call it time spent, time saved, or time volunteered unless the label says so.
Treat missing measurements and unassigned roles as missing information, not zero.
Do not assert causation, improvement, efficiency, or comparisons unsupported by
the supplied facts. State that invented demonstration data is fictional.

Return JSON with exactly these fields:
{"schema_version": 1, "report_id": "COPY_REPORT_ID", "sections": [
  {"heading": "Overview", "paragraphs": [
    [{"text": "The archive records "}, {"fact": "COPY_THE_COMPLETIONS_FACT_ID"},
     {"text": " accomplishments in the selected scope."}]
  ]}
]}

Each paragraph is a list of segments. Each segment contains either text or fact,
never both. Text and headings must not contain numeric literals; numerical values
are substituted by the renderer from the referenced fact's trusted display.
Use the supplied report_id. Do not include HTML, Markdown links or extra keys.
Copy a real fact ID from agent-input.json; for the accomplishment count, find
the fact with dimension "summary" and group "completions".
Fact references are validated; qualitative prose and spelled-out numbers are not
automatically proven. The rendered narrative remains a draft for human review.
The fixed report and source records remain the numerical reference.

Build a new report folder with --narrative PATH_TO_JSON to include your draft.
No network calls are made by the report tools.
'''


@dataclass(frozen=True)
class ReportSnapshot:
    """A private serialized copy: callers receive fresh values, never shared state."""
    _payload: str = field(repr=False)

    def unpack(self):
        contents = json.loads(self._payload)
        return contents['evidence'], contents['preparation']

    def for_format(self, report_format, selected_ids=None):
        evidence, _ = self.unpack()
        preparation = build_preparation(evidence, report_format, selected_ids)
        return ReportSnapshot(json.dumps({'evidence': evidence, 'preparation': preparation},
                                         ensure_ascii=False, allow_nan=False))


def create_snapshot(database, *, since=None, until=None, category=None, duty=None,
                    report_format='general', selected_ids=None, expected_records=None):
    """Freeze one verified scope before preview, sending, or saving."""
    evidence = build_evidence(database, since=since, until=until, category=category, duty=duty)
    preparation = build_preparation(evidence, report_format, selected_ids)
    if expected_records is not None:
        current = {row['id']: row for row in evidence['records']}
        expected = {row['id']: row for row in expected_records}
        if (set(expected) != set(selected_ids or [])
                or any(current.get(record_id) != row for record_id, row in expected.items())):
            raise ValueError('Selected accomplishments changed. Choose them again before saving.')
    return ReportSnapshot(json.dumps({'evidence': evidence, 'preparation': preparation},
                                     ensure_ascii=False, allow_nan=False))


def publish(database, destination, *, since=None, until=None, category=None, duty=None, narrative=None,
            report_format='general', selected_ids=None, expected_records=None):
    """Offline wrapper: freeze the archive, then publish only that snapshot."""
    if os.path.lexists(destination):
        raise FileExistsError(f'Report folder already exists: {destination}')
    snapshot = create_snapshot(database, since=since, until=until, category=category, duty=duty,
                               report_format=report_format, selected_ids=selected_ids,
                               expected_records=expected_records)
    draft = json.loads(Path(narrative).read_text(encoding='utf-8')) if narrative is not None else None
    return publish_snapshot(snapshot, destination, narrative=draft)


def publish_snapshot(snapshot, destination, *, narrative=None):
    """Publish the original approved snapshot without rereading any archive."""
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise FileExistsError(f'Report folder already exists: {destination}')
    evidence, preparation = snapshot.unpack()
    report_format = preparation['report_format']
    writer_input = agent_input(evidence)
    if report_format != 'general':
        writer_input['preparation'] = preparation
    draft = narrative
    if draft is not None:
        validate_narrative(draft, evidence, preparation)
    guide = AGENT_GUIDE
    if report_format != 'general':
        guide = guide.replace('"report_id": "COPY_REPORT_ID",',
                              '"report_id": "COPY_REPORT_ID", "preparation_id": "COPY_PREPARATION_ID",')
        guide += ('\nCopy preparation_id from preparation.json exactly. The draft is bound to this '
                  'format and selected accomplishments. Per-record facts are in preparation.facts; '
                  'only those selected measurements may be used as individual numerical claims.\n')
    files = {
        'evidence.json': json.dumps(evidence, ensure_ascii=False, allow_nan=False, indent=2),
        'agent-input.json': json.dumps(writer_input, ensure_ascii=False, allow_nan=False, indent=2),
        'report.html': render_html(evidence, draft, preparation=preparation),
        'agent-guide.txt': guide + f'\nPreparation format: {report_format.upper()}\n' + preparation_guide(report_format),
    }
    if report_format != 'general':
        files['preparation.json'] = json.dumps(preparation, ensure_ascii=False, allow_nan=False, indent=2)
    if draft is not None:
        files['narrative.json'] = json.dumps(draft, ensure_ascii=False, allow_nan=False, indent=2)
    payloads = {name: value.encode('utf-8') for name, value in files.items()}
    manifest = {
        'schema_version': 1, 'report_id': evidence['report_id'],
        'generated_at': dt.datetime.now(dt.UTC).isoformat(),
        'report_format': report_format,
        'files': {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()},
    }
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name, value in payloads.items():
            with (destination / name).open('xb') as output:
                output.write(value)
        # The manifest marks a completed package and is always written last.
        with (destination / 'manifest.json').open('x', encoding='utf-8') as output:
            json.dump(manifest, output, indent=2)
    except OSError as exc:
        exc.add_note(f'Publication did not finish. Partial files may remain in {destination}; no existing folder was replaced.')
        raise
    verify_package(destination)
    return destination


def verify_package(destination):
    """Check package completeness and hashes; this is not an authenticity proof."""
    destination = Path(destination)
    manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    required = {'evidence.json', 'agent-input.json', 'report.html', 'agent-guide.txt'}
    permitted = required | {'narrative.json', 'preparation.json'}
    files = manifest.get('files')
    if (manifest.get('schema_version') != 1 or not isinstance(files, dict)
            or not required <= files.keys() or not files.keys() <= permitted):
        raise ValueError('Invalid report package manifest')
    for name, expected in files.items():
        actual = hashlib.sha256((destination / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'Report package file changed or is incomplete: {name}')
    for name in ('evidence.json', 'agent-input.json'):
        content = json.loads((destination / name).read_text(encoding='utf-8'))
        if content.get('report_id') != manifest.get('report_id'):
            raise ValueError('Report package mixes different evidence snapshots')
    report_format = manifest.get('report_format', 'general')
    if report_format not in ('general', 'epb', 'cv'):
        raise ValueError('Invalid report format in package')
    if report_format != 'general':
        if 'preparation.json' not in files:
            raise ValueError('Preparation file is missing')
        evidence = json.loads((destination / 'evidence.json').read_text(encoding='utf-8'))
        preparation = json.loads((destination / 'preparation.json').read_text(encoding='utf-8'))
        expected = build_preparation(evidence, report_format, preparation.get('selected_ids'))
        if preparation != expected:
            raise ValueError('Preparation does not match verified evidence')
        writer_input = json.loads((destination / 'agent-input.json').read_text(encoding='utf-8'))
        if writer_input.get('preparation') != preparation:
            raise ValueError('Agent preparation does not match report')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--since')
    parser.add_argument('--until')
    parser.add_argument('--category')
    parser.add_argument('--role', dest='duty')
    parser.add_argument('--narrative', type=Path)
    parser.add_argument('--format', choices=('general', 'epb', 'cv'), default='general', dest='report_format')
    parser.add_argument('--record-id', type=int, action='append', dest='selected_ids',
                        help='Accomplishment ID to include in EPB/CV; repeat to select more')
    args = parser.parse_args()
    try:
        output = publish(args.db, args.out, since=args.since, until=args.until,
                         category=args.category, duty=args.duty, narrative=args.narrative,
                         report_format=args.report_format, selected_ids=args.selected_ids)
    except (ValueError, OSError, sqlite3.Error) as exc:
        notes = '\n'.join(getattr(exc, '__notes__', []))
        parser.exit(1, f'Could not create report: {exc}\n{notes}\n')
    print(f'Report: {output / "report.html"}')
    print(f'Verified evidence: {output / "evidence.json"}')
    print(f'Agent facts and instructions: {output / "agent-input.json"}, {output / "agent-guide.txt"}')


if __name__ == '__main__':
    main()
