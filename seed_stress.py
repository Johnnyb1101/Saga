"""Build a fictional, reproducible three-year Saga archive for stress checks.

Generated completion evidence is deterministic for a fixed seed and end date.
Recording timestamps and future measurement reminders reflect the run date.
"""

import argparse
import calendar
import datetime as dt
import json
import os
import random
from contextlib import closing
from pathlib import Path

from saga import writes
from saga.db import ROOT, connect, init_db
from saga.recurrence import occurrence_date

DEFAULT_PATH = ROOT / 'data' / 'stress-demo.db'
MEASURES = ('hours', 'applications', 'sessions', 'people')
ROLES = {
    'work': ('Operations', 'Mentoring'),
    'certs': ('Compliance',),
    'school': ('Student',),
    'personal': ('Community',),
    'home': ('Household',),
    'volunteer': ('Volunteer Lead',),
    'legacy': ('Former Assignment',),
}
CATEGORIES = ('work', 'certs', 'school', 'personal', 'home', 'volunteer', 'legacy')


def _start_date(end):
    year = end.year - 3
    if year < 1:
        raise ValueError('end_date must allow three prior calendar years')
    return end.replace(year=year, day=min(end.day, calendar.monthrange(year, end.month)[1]))


def _date(value):
    if value is None:
        return dt.date.today()
    if not isinstance(value, str):
        raise TypeError('end_date must be an ISO YYYY-MM-DD date')
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError('end_date must be an ISO YYYY-MM-DD date') from None
    if parsed.isoformat() != value or parsed > dt.date.today():
        raise ValueError('end_date must be a valid date no later than today')
    return parsed


def _refuse_existing(path):
    # lexists also rejects dangling symlinks, which Path.exists misses.
    if os.path.lexists(path):
        raise FileExistsError(f'Output already exists: {path}')
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f'Output path has a symlinked parent: {path}')


def _candidate_days(start, end):
    days = []
    day = start
    while day <= end:
        # Operational work is quieter on weekends and varies by season.
        weight = 1 if day.weekday() >= 5 else (5 if day.month in (1, 4, 9, 10) else 3)
        days.extend([day] * weight)
        day += dt.timedelta(days=1)
    return days


def _evidence(rng, number, day, category, duty, *, task_id=None, due_date=None):
    status = rng.choices(('measured', 'not_applicable', 'unknown'), (70, 22, 8))[0]
    measure = rng.choice(MEASURES) if status == 'measured' else None
    quantity = rng.randint(1, 80) / 4 if measure else None
    outcome = f'Fictional {category} result {number:05d}'
    if number % 97 == 0:
        outcome += ' — café; naïve résumé, “quoted” <sample> & detail'
    if number % 211 == 0:
        outcome += ' | '.join([' Extended fictional notes for display and export.'] * 12)
    return {
        'task_id': task_id, 'category': category, 'duty': duty,
        'completed_at': f'{day.isoformat()} {rng.randrange(7, 21):02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}',
        'outcome': outcome,
        'measure': measure, 'quantity': quantity,
        'flagged': rng.random() < .13,
        'review_counting': int(category not in ('home', 'volunteer')),
        'measurement_status': status, 'due_date': due_date,
    }


def _write_completion(con, evidence):
    values = {key: evidence[key] for key in ('outcome', 'measure', 'quantity', 'flagged',
                                             'completed_at', 'measurement_status')}
    if evidence['measurement_status'] == 'unknown':
        values['remind_on'] = (dt.date.today() + dt.timedelta(days=14)).isoformat()
    if evidence['task_id'] is None:
        return writes.add_completion(con, evidence['category'], duty=evidence['duty'], **values)
    return writes.complete_task(con, evidence['task_id'], **values)


def seed(path=DEFAULT_PATH, records=10000, seed=42, end_date=None):
    """Create a fresh database and an independent expected-current ledger."""
    if type(records) is not int or records < 1:
        raise ValueError('records must be a positive integer')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    end = _date(end_date)
    start = _start_date(end)
    path = Path(path).absolute()
    expected_path = path.with_name(f'{path.stem}.expected.json')
    _refuse_existing(path)
    _refuse_existing(expected_path)
    for suffix in ('-wal', '-shm', '-journal'):
        _refuse_existing(Path(f'{path}{suffix}'))
    rng = random.Random(seed)
    days = _candidate_days(start, end)
    plan = []
    for number in range(records):
        category = rng.choices(CATEGORIES, (35, 10, 10, 15, 15, 10, 5))[0]
        duty = rng.choice(ROLES[category]) if rng.random() < .8 else None
        plan.append(_evidence(rng, number, rng.choice(days), category, duty))
    series_count = min(24, records // 20)
    series_slots = min(records // 5, series_count * 5)
    # Reserve standalone slots for boundaries so series dates cannot replace them.
    reserved = {series_slots}
    plan[series_slots]['completed_at'] = f'{start.isoformat()} 00:00:00'
    if records > series_slots + 1:
        reserved.add(records - 1)
        plan[-1]['completed_at'] = f'{end.isoformat()} 23:59:59'
    leap_days = [dt.date(year, 2, 29) for year in range(start.year, end.year + 1)
                 if calendar.isleap(year) and start <= dt.date(year, 2, 29) <= end]
    if records > series_slots + 2 and leap_days:
        reserved.add(series_slots + 1)
        plan[series_slots + 1]['completed_at'] = f'{leap_days[0].isoformat()} 12:00:00'
    if records > series_slots + 3:
        month_end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
        if month_end > end:
            month_end = end.replace(day=1) - dt.timedelta(days=1)
        reserved.add(series_slots + 2)
        plan[series_slots + 2]['completed_at'] = f'{month_end.isoformat()} 23:59:59'

    init_db(path)
    ledger = []
    with closing(connect(path)) as con:
        writes.create_category(con, 'volunteer', counts_toward_review=False)
        writes.create_category(con, 'legacy')
        for category, roles in ROLES.items():
            for role in roles:
                writes.add_duty(con, role, category)
        for measure in MEASURES:
            writes.add_measure(con, measure)
        projects = [writes.add_project(con, f'Fictional project {i + 1}',
                                       start_date=start.isoformat(), deadline=end.isoformat())
                    for i in range(8)]

        # A small number of real series exercises advancement without a huge open list.
        first_tasks = {}
        for i in range(series_slots):
            entry = plan[i]
            series = i // 5
            index = i % 5
            category = 'work' if series % 2 else 'certs'
            duty = ROLES[category][0]
            mode = series % 3
            frequency = ('weekly', 'monthly', 'monthly')[mode]
            anchor = start + dt.timedelta(days=series * 2 + 4)
            if mode == 2:
                anchor = anchor.replace(day=5) if anchor.day <= 5 else (
                    anchor.replace(day=20) if anchor.day <= 20 else
                    (anchor.replace(day=1) + dt.timedelta(days=35)).replace(day=5))
            if index == 0:
                first = occurrence_date(anchor.isoformat(), frequency, 1, 0,
                                        5 if mode == 2 else None, 20 if mode == 2 else None)
                terminal = occurrence_date(anchor.isoformat(), frequency, 1, 4,
                                           5 if mode == 2 else None, 20 if mode == 2 else None)
                task = writes.add_task(con, f'Fictional recurring task {series + 1}', category,
                                       due_date=first, duty=duty, repeat=frequency,
                                       month_day=5 if mode == 2 else None,
                                       second_month_day=20 if mode == 2 else None,
                                       end_date=terminal)
            else:
                task = con.execute("SELECT id FROM tasks WHERE recurrence_id="
                                   "(SELECT recurrence_id FROM tasks WHERE id=?) AND status='open'",
                                   (first_tasks[series],)).fetchone()[0]
            if index == 0:
                first_tasks[series] = task
            due = occurrence_date(anchor.isoformat(), frequency, 1, index,
                                  5 if mode == 2 else None, 20 if mode == 2 else None)
            entry.update(category=category, duty=duty, task_id=task, due_date=due,
                         review_counting=1,
                         completed_at=f'{due} 12:00:00')
            entry['id'] = _write_completion(con, entry)
            ledger.append(entry)

        for i in range(series_slots, records):
            entry = plan[i]
            if i % 3 != 0:
                due = dt.date.fromisoformat(entry['completed_at'][:10])
                due += dt.timedelta(days=rng.choice((-3, -1, 0, 1, 5)))
                task = writes.add_task(con, f'Fictional task {i + 1}', entry['category'],
                                       project_id=projects[i % len(projects)] if i % 4 else None,
                                       due_date=due.isoformat(), duty=entry['duty'])
                entry.update(task_id=task, due_date=due.isoformat())
            entry['id'] = _write_completion(con, entry)
            ledger.append(entry)

        # Cancelled tasks are separate from completed evidence.
        for i in range(min(12, max(1, records // 30))):
            task = writes.add_task(con, f'Fictional cancelled task {i + 1}', 'personal')
            writes.cancel_task(con, task)
        for i in range(min(35, max(1, records // 200))):
            writes.add_task(con, f'Fictional open task {i + 1}', 'work',
                            due_date=end.isoformat(), duty='Operations')

        correction_count = max(1, round(records * .05)) if records >= 20 else 0
        correction_pool = [index for index in range(records) if index not in reserved]
        for j in range(correction_count):
            target = j - 1 if j % 10 == 1 else j
            entry = ledger[correction_pool[(target * 19 + 3) % len(correction_pool)]]
            changes = {'outcome': f"{entry['outcome']} (verified {j + 1})"}
            if j % 4 == 0:
                old_day = dt.date.fromisoformat(entry['completed_at'][:10])
                revised = min(end, old_day + dt.timedelta(days=1))
                if revised == old_day:
                    revised = max(start, old_day - dt.timedelta(days=1))
                changes['completed_at'] = f"{revised.isoformat()} {entry['completed_at'][11:]}"
            elif j % 4 == 1:
                new_category = 'home' if entry['category'] != 'home' else 'work'
                changes.update(category=new_category, duty=None,
                               review_counting=int(new_category != 'home'))
            elif j % 4 == 2 and entry['measure'] is not None:
                changes['quantity'] = entry['quantity'] + .25
            new_id = writes.correct_completion(con, entry['id'], 'Fictional verification', **changes)
            entry.update(changes, id=new_id)
        writes.set_category_status(con, 'legacy', True)
        writes.set_role_status(con, 'personal', 'Community', False)

    result = {'database': str(path), 'expected': str(expected_path), 'seed': seed,
              'records': records, 'since': start.isoformat(), 'until': end.isoformat()}
    expected = {key: result[key] for key in ('seed', 'records', 'since', 'until')}
    expected['accomplishments'] = ledger
    with expected_path.open('x', encoding='utf-8') as output:
        json.dump(expected, output, indent=2)
        output.write('\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_PATH)
    parser.add_argument('--records', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--end-date')
    args = parser.parse_args(argv)
    print(json.dumps(seed(args.db, args.records, args.seed, args.end_date)))


if __name__ == '__main__':
    main()
