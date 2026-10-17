"""Check invented stress data against its independent ledger and summarize it."""

import argparse
import calendar
import datetime as dt
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from saga import db
from saga.application import ArchiveService


def expected_totals(rows):
    """Calculate expectations from generated inputs, without database queries."""
    measures = defaultdict(lambda: {'total': 0, 'occasions': 0})
    categories = defaultdict(lambda: {'completions': 0, 'with_a_number': 0, 'flagged': 0})
    summary = {'completions': len(rows), 'review_counting': 0, 'flagged': 0, 'with_a_number': 0}
    for row in rows:
        numbered = row['quantity'] is not None
        summary['review_counting'] += bool(row['review_counting'])
        summary['flagged'] += bool(row['flagged'])
        summary['with_a_number'] += numbered
        category = categories[row['category']]
        category['completions'] += 1
        category['with_a_number'] += numbered
        category['flagged'] += bool(row['flagged'])
        if row['measure'] is not None:
            measures[row['measure']]['total'] += row['quantity']
            measures[row['measure']]['occasions'] += 1
    return {'summary': summary, 'measures': dict(measures), 'categories': dict(categories)}


def verify_snapshot(snapshot, rows):
    """Reject incorrect summaries, missing/duplicate rows, or changed evidence."""
    expected = expected_totals(rows)
    if snapshot['summary'] != expected['summary']:
        raise ValueError('Report summary does not match the independent ledger')
    actual_measures = {row['measure']: {key: row[key] for key in ('total', 'occasions')}
                       for row in snapshot['measures']}
    if len(actual_measures) != len(snapshot['measures']) or actual_measures != expected['measures']:
        raise ValueError('Measurement totals do not match the independent ledger')
    actual_categories = {row['category']: {key: row[key] for key in ('completions', 'with_a_number', 'flagged')}
                         for row in snapshot['categories']}
    if len(actual_categories) != len(snapshot['categories']):
        raise ValueError('Duplicate categories in report')
    nonzero = {key: value for key, value in actual_categories.items() if value['completions']}
    invalid_zero = any(any(value.values()) for value in actual_categories.values() if not value['completions'])
    if invalid_zero or nonzero != expected['categories']:
        raise ValueError('Category totals do not match the independent ledger')
    fields = ('id', 'completed_at', 'category', 'duty', 'measure', 'quantity',
              'flagged', 'review_counting', 'outcome', 'measurement_status', 'task_id', 'due_date')
    actual = [tuple(row[key] for key in fields) for row in snapshot['accomplishments']]
    wanted = [tuple(row[key] for key in fields) for row in rows]
    if sorted(actual, key=lambda row: row[0]) != sorted(wanted, key=lambda row: row[0]):
        raise ValueError('Accomplishment evidence does not match the independent ledger')


def _scopes(ledger):
    start = dt.date.fromisoformat(ledger['since'])
    end = dt.date.fromisoformat(ledger['until'])
    yield 'All time', {}
    yield 'Full generated range', {'since': start.isoformat(), 'until': end.isoformat()}
    for year in range(start.year, end.year + 1):
        yield str(year), {'since': max(start, dt.date(year, 1, 1)).isoformat(),
                         'until': min(end, dt.date(year, 12, 31)).isoformat()}
    month = start.replace(day=1)
    while month <= end:
        last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        yield month.strftime('%Y-%m'), {'since': max(start, month).isoformat(),
                                      'until': min(end, last).isoformat()}
        month = last + dt.timedelta(days=1)
    yield 'First day inclusive', {'since': start.isoformat(), 'until': start.isoformat()}
    yield 'Last day inclusive', {'since': end.isoformat(), 'until': end.isoformat()}
    yield 'Before generated range', {'until': (start - dt.timedelta(days=1)).isoformat()}
    rows = ledger['accomplishments']
    for category in sorted({row['category'] for row in rows}):
        yield f'Category: {category}', {'category': category}
    for role in sorted({row['duty'] for row in rows if row['duty'] is not None}):
        yield f'Role: {role}', {'duty': role}
    for category, role in sorted({(row['category'], row['duty']) for row in rows if row['duty'] is not None}):
        yield f'{category} / {role}', {'category': category, 'duty': role}


def _matching(rows, scope):
    return [row for row in rows
            if (scope.get('since') is None or row['completed_at'][:10] >= scope['since'])
            and (scope.get('until') is None or row['completed_at'][:10] <= scope['until'])
            and (scope.get('category') is None or row['category'] == scope['category'])
            and (scope.get('duty') is None or row['duty'] == scope['duty'])]


def _load_ledger(path):
    ledger = json.loads(path.read_text(encoding='utf-8'))
    rows = ledger['accomplishments']
    start, end = dt.date.fromisoformat(ledger['since']), dt.date.fromisoformat(ledger['until'])
    if start > end or len(rows) != ledger['records'] or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Invalid stress ledger range, count, or duplicate identities')
    for row in rows:
        date = dt.datetime.fromisoformat(row['completed_at'])
        if not start <= date.date() <= end:
            raise ValueError('Ledger evidence falls outside the generated range')
        quantity = row['quantity']
        if (row['measure'] is None) != (quantity is None):
            raise ValueError('Ledger measurement is incomplete')
        if quantity is not None and (type(quantity) not in (int, float) or not math.isfinite(quantity)):
            raise ValueError('Ledger contains a non-finite measurement')
    return ledger


def analyze(database, expected=None):
    """Time real reports and verify each against separately calculated totals."""
    database = Path(database).resolve()
    ledger = _load_ledger(Path(expected) if expected is not None else database.with_suffix('.expected.json'))
    service = ArchiveService(database)
    started = time.perf_counter()
    scopes = []
    for label, scope in _scopes(ledger):
        before = time.perf_counter()
        snapshot = service.history_report(**scope)
        elapsed = time.perf_counter() - before
        verify_snapshot(snapshot, _matching(ledger['accomplishments'], scope))
        scopes.append({'name': label, 'scope': scope, 'seconds': elapsed,
                       'completions': snapshot['summary']['completions']})
    totals = expected_totals(ledger['accomplishments'])
    with closing(db.connect(database)) as con:
        raw_completions = con.execute('SELECT count(*) FROM completions').fetchone()[0]
        tasks = dict(con.execute('SELECT status, count(*) FROM tasks GROUP BY status'))
        recurrences = dict(con.execute('SELECT status, count(*) FROM recurrences GROUP BY status'))
        integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok' or con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Stress archive integrity verification failed')
    timings = sorted(row['seconds'] for row in scopes)
    return {
        'database': str(database), 'seed': ledger['seed'], 'since': ledger['since'], 'until': ledger['until'],
        'verified': True, 'checks': len(scopes), 'totals': totals,
        'years': dict(sorted(Counter(row['completed_at'][:4] for row in ledger['accomplishments']).items())),
        'months': dict(sorted(Counter(row['completed_at'][:7] for row in ledger['accomplishments']).items())),
        'roles': dict(sorted(Counter(row['duty'] or 'No role' for row in ledger['accomplishments']).items())),
        'raw_completion_rows': raw_completions, 'tasks': tasks, 'recurrences': recurrences,
        'integrity': integrity, 'scopes': scopes,
        'timing': {'all_time_seconds': scopes[0]['seconds'], 'median_seconds': statistics.median(timings),
                   'p95_seconds': timings[math.ceil(len(timings) * .95) - 1],
                   'slowest_seconds': max(timings), 'total_verification_seconds': time.perf_counter() - started},
    }


def format_report(report):
    lines = ['SAGA INVENTED STRESS DATA — VERIFIED REPORT',
             f"Period: {report['since']} through {report['until']} (inclusive)",
             f"Seed: {report['seed']}",
             (f"{report['totals']['summary']['completions']:,} current accomplishments; "
              f"{report['raw_completion_rows']:,} stored revisions"),
             f"All {report['checks']} report scopes match the independent input ledger.",
             '', 'SUMMARY']
    lines += [f'{key.replace("_", " ")}: {value:,}' for key, value in report['totals']['summary'].items()]
    for title, values in (('BY YEAR', report['years']), ('BY MONTH', report['months']),
                          ('BY ROLE', report['roles'])):
        lines.extend(('', title))
        lines += [f'{name}: {value:,}' for name, value in values.items()]
    lines.extend(('', 'BY CATEGORY'))
    lines += [f"{name}: {values['completions']:,} accomplishments, {values['flagged']:,} flagged"
              for name, values in sorted(report['totals']['categories'].items())]
    lines.extend(('', 'MEASUREMENTS — SEPARATE TOTALS'))
    lines += [f"{name}: {values['total']:,.2f} across {values['occasions']:,} occasions"
              for name, values in sorted(report['totals']['measures'].items())]
    lines.extend(('', 'REPORT SERVICE TIMINGS'))
    lines += [f'{key.replace("_", " ")}: {value:.4f}' for key, value in report['timing'].items()]
    lines += ['Timings cover local service queries, not desktop rendering or another machine.',
              'This archive contains invented data only. Your real archive and app selection were not changed.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--expected', type=Path)
    args = parser.parse_args()
    report = analyze(args.db, args.expected)
    outputs = {args.db.with_suffix('.report.json'): json.dumps(report, indent=2),
               args.db.with_suffix('.report.txt'): format_report(report)}
    if any(path.exists() for path in outputs):
        parser.error('A report file already exists; refusing to overwrite it')
    for path, content in outputs.items():
        with path.open('x', encoding='utf-8') as output:
            output.write(content)
    print(format_report(report))


if __name__ == '__main__':
    main()
