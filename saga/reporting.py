"""Verified, read-only facts for a human-readable archive report."""

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import Decimal

from saga.application import ArchiveService


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _number(value, description):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{description} must be a finite number')
    return value


def _count(value, description):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{description} must be a nonnegative integer')
    return value


def _equal_total(actual, expected, description):
    _number(actual, description)
    _number(expected, description)
    if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError(f'{description} does not match source records')


def _fact(facts, *, label, value, measure=None, dimension=None, group=None, ids=()):
    _number(value, label)
    identity = [measure, dimension, group, label]
    fact_id = 'fact-' + _digest(identity)[:24]
    if fact_id in facts:
        raise ValueError('Duplicate fact identity')
    display = format(Decimal(str(value)), ',f')
    if '.' in display:
        display = display.rstrip('0').rstrip('.')
    facts[fact_id] = {
        'label': label, 'value': value, 'display': display,
        'measure': measure, 'dimension': dimension, 'group': group,
        'source_record_ids': sorted(ids),
    }
    return fact_id


def _month_keys(records):
    """Use SQLite date() so grouping agrees with the archive's date filters."""
    with closing(sqlite3.connect(':memory:')) as con:
        months = {}
        for row in records:
            date = con.execute('SELECT date(?)', (row['completed_at'],)).fetchone()[0]
            if date is None:
                raise ValueError('A completion has an invalid date')
            months[row['id']] = date[:7]
    return months


def build_evidence(database, since=None, until=None, category=None, duty=None):
    """Build a checked fact ledger from one current-revision history snapshot."""
    snapshot = ArchiveService(database).history_report(
        since=since, until=until, category=category, duty=duty)
    scope = {key: snapshot[key] for key in ('since', 'until', 'category', 'duty')}
    records = snapshot['accomplishments']
    summary = snapshot['summary']
    ids = [row['id'] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate source record IDs')
    for row in records:
        if row['quantity'] is not None:
            _number(row['quantity'], f"Quantity for record {row['id']}")
        if (row['measure'] is None) != (row['quantity'] is None):
            raise ValueError(f"Record {row['id']} has an incomplete measurement")
    months = _month_keys(records)
    expected_counts = {
        'completions': len(records),
        'review_counting': sum(row['review_counting'] == 1 for row in records),
        'flagged': sum(row['flagged'] == 1 for row in records),
        'with_a_number': sum(row['quantity'] is not None for row in records),
    }
    for key, expected in expected_counts.items():
        _count(summary[key], f'Summary {key}')
        if summary[key] != expected:
            raise ValueError(f'Summary {key} does not match source records')

    by_category = defaultdict(list)
    for row in records:
        by_category[row['category']].append(row)
    categories = snapshot['categories']
    if len({row['category'] for row in categories}) != len(categories):
        raise ValueError('Duplicate category aggregate')
    if not set(by_category).issubset({row['category'] for row in categories}):
        raise ValueError('Missing category aggregate')
    for aggregate in categories:
        members = by_category[aggregate['category']]
        for key, expected in (
            ('completions', len(members)),
            ('with_a_number', sum(row['quantity'] is not None for row in members)),
            ('flagged', sum(row['flagged'] == 1 for row in members)),
        ):
            _count(aggregate[key], f"Category {aggregate['category']} {key}")
            if aggregate[key] != expected:
                raise ValueError(f"Category {aggregate['category']} {key} does not match source records")

    facts = {}
    for key, members in (
        ('completions', records),
        ('flagged', [r for r in records if r['flagged'] == 1]),
        ('review_counting', [r for r in records if r['review_counting'] == 1]),
        ('with_a_number', [r for r in records if r['quantity'] is not None]),
    ):
        _fact(facts, label=key.replace('_', ' ').capitalize(), value=summary[key],
              dimension='summary', group=key, ids=[r['id'] for r in members])

    measured = defaultdict(list)
    for row in records:
        if row['measure'] is not None:
            measured[row['measure']].append(row)
    sql_measures = snapshot['measures']
    if len({row['measure'] for row in sql_measures}) != len(sql_measures):
        raise ValueError('Duplicate measure aggregate')
    if set(measured) != {row['measure'] for row in sql_measures}:
        raise ValueError('Measure aggregates do not match source records')
    measurements = []
    for aggregate in sql_measures:
        measure = aggregate['measure']
        members = measured[measure]
        total = sum(row['quantity'] for row in members)
        _number(total, f'{measure} total')
        _equal_total(aggregate['total'], total, f'{measure} total')
        _count(aggregate['occasions'], f'{measure} occasions')
        if aggregate['occasions'] != len(members):
            raise ValueError(f'{measure} occasions do not match source records')
        fact_id = _fact(facts, label=f'{measure} total', value=aggregate['total'],
                        measure=measure, dimension='total', ids=[r['id'] for r in members])
        entry = {'measure': measure, 'total': aggregate['total'],
                 'occasions': len(members), 'fact_id': fact_id}
        for field, dimension, keys in (
            ('by_category', 'category', [r['category'] for r in categories]),
            ('by_role', 'role', sorted({r['duty'] for r in members}, key=lambda x: (x is not None, x or ''))),
            ('by_month', 'month', sorted({months[r['id']] for r in members})),
        ):
            groups = []
            for key in keys:
                group_members = [r for r in members if (
                    r['category'] if dimension == 'category' else
                    r['duty'] if dimension == 'role' else months[r['id']]) == key]
                group_total = sum(r['quantity'] for r in group_members)
                _number(group_total, f'{measure} {dimension} total')
                label = ('No role assigned' if key is None else f'Role: {key}') if dimension == 'role' else key
                group_id = _fact(facts, label=f'{measure} / {label}', value=group_total,
                                 measure=measure, dimension=dimension, group=key,
                                 ids=[r['id'] for r in group_members])
                groups.append({'label': label, 'total': group_total,
                               'occasions': len(group_members), 'fact_id': group_id})
            _equal_total(sum(g['total'] for g in groups), total, f'{measure} {dimension} groups')
            if sum(g['occasions'] for g in groups) != len(members):
                raise ValueError(f'{measure} {dimension} occasions do not match')
            entry[field] = groups
        measurements.append(entry)

    report_id = _digest({'scope': scope, 'summary': summary, 'records': records,
                         'measures': sql_measures, 'categories': categories})
    return {
        'schema_version': 1, 'report_id': report_id, 'scope': scope,
        'summary': summary,
        'verification': {'passed': True, 'checks': [
            'unique source records', 'finite measurements', 'summary counts',
            'category counts including empty categories', 'measure totals and occasions',
            'category, role, and month breakdowns',
        ]},
        'records': records, 'facts': facts, 'measurements': measurements,
    }


def agent_input(evidence):
    """Share bounded, provenance-free facts with a narrative writer."""
    return {key: evidence[key] for key in ('schema_version', 'report_id', 'scope', 'summary')} | {
        'facts': {fact_id: {key: fact[key] for key in
                            ('label', 'display', 'value', 'measure', 'dimension', 'group')}
                  for fact_id, fact in evidence['facts'].items()}
    }
