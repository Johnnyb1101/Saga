"""UI-independent desktop operations over Saga's existing database core."""

import calendar
import datetime as dt
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from saga import analytics, db, export, reads, writes
from saga.recurrence import occurrence_date


@dataclass(frozen=True)
class SaveResult:
    completion_id: int
    export_error: str | None = None


@dataclass(frozen=True)
class TaskSaveResult:
    task_id: int
    export_error: str | None = None


def create_archive(path):
    """Create only an explicitly selected new archive; never replace one."""
    return db.init_db(Path(path).resolve())


def _text(value, label, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        if not required and value == '':
            return None
        raise ValueError(f"{label} must not be blank")
    return value.strip()


def _completion_evidence(draft):
    """Normalize answers shared by standalone capture and task completion."""
    outcome = _text(draft.get('outcome'), 'Outcome', required=True)
    date = draft.get('completed_date')
    completed_at = None
    if date is not None:
        try:
            parsed = dt.date.fromisoformat(date)
        except (TypeError, ValueError):
            raise ValueError("Completion date must use YYYY-MM-DD") from None
        if parsed.isoformat() != date or parsed > dt.date.today():
            raise ValueError("Completion date must be today or earlier, in YYYY-MM-DD form")
        completed_at = f'{date} 00:00:00'
    measure = _text(draft.get('measure'), "What you're counting")
    new_measure = _text(draft.get('new_measure'), "What you're counting",
                        required=draft.get('new_measure') is not None)
    if measure is not None and new_measure is not None:
        raise ValueError("Choose what you're counting or enter a new name")
    status = draft.get('measurement_status')
    if status not in ('measured', 'not_applicable', 'unknown'):
        raise ValueError("Choose Yes, No number needed, or I'll add a number later")
    quantity = draft.get('quantity')
    if quantity is not None:
        if isinstance(quantity, bool):
            raise ValueError("Number must be a finite number")
        try:
            quantity = float(quantity)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Number must be a finite number") from None
    unit = measure or new_measure
    if status == 'measured' and (unit is None or quantity is None):
        raise ValueError("Enter an amount and what you're counting")
    if status != 'measured' and (unit is not None or quantity is not None):
        raise ValueError("Choose Yes to add an amount and what you're counting, or clear them")
    writes.measurement_state(unit, quantity, status)
    reminder = draft.get('remind_on')
    if status == 'unknown':
        try:
            writes.validate_reminder(reminder)
        except ValueError:
            raise ValueError("Follow-up date must be today or later, in YYYY-MM-DD form") from None
    elif reminder is not None:
        raise ValueError("A follow-up date is only valid for I'll add a number later")
    flagged = draft.get('flagged', False)
    if not isinstance(flagged, bool):
        raise ValueError("Review flag must be Yes or No")  # noqa: TRY004 - invalid form data
    return {
        'outcome': outcome, 'completed_at': completed_at, 'measure': measure,
        'quantity': quantity, 'measurement_status': status,
        'remind_on': reminder, 'flagged': flagged,
    }, new_measure


def _ordinal(day):
    suffix = 'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    return f'{day}{suffix}'


def schedule_description(frequency, interval, anchor_date, month_day=None,
                         second_month_day=None, end_date=None):
    """Describe a persisted schedule or a normalized task preview."""
    if frequency == 'monthly' and second_month_day is not None:
        schedule = ('Twice a month' if interval == 1
                    else f'Twice every {interval} months')
    elif interval == 1:
        schedule = f'Repeats {frequency}'
    else:
        unit = {'daily': 'days', 'weekly': 'weeks', 'monthly': 'months'}[frequency]
        schedule = f'Every {interval} {unit}'
    if frequency == 'weekly' and anchor_date is not None:
        schedule += f' on {dt.date.fromisoformat(anchor_date):%A}'
    elif frequency == 'monthly' and month_day is not None:
        days = f'the {_ordinal(month_day)}'
        if second_month_day is not None:
            days += f' and {_ordinal(second_month_day)}'
        schedule += f' on {days}'
    if end_date is not None:
        schedule += f', ends {end_date}'
    return schedule


def _repeat_label(series):
    if series is None:
        return None
    schedule = schedule_description(series['frequency'], series['interval'],
                                    series['anchor_date'], series['month_day'],
                                    series['second_month_day'], series['end_date'])
    return schedule if series['status'] == 'active' else f'Stopped: {schedule}'


def _monthly_start(start, *days):
    """Find the first selected calendar day on or after start."""
    month_index = (start.year - 1) * 12 + start.month - 1
    while month_index < 120000:
        year, month = divmod(month_index, 12)
        year, month = year + 1, month + 1
        last_day = calendar.monthrange(year, month)[1]
        candidates = sorted({dt.date(year, month, min(day, last_day)) for day in days})
        for candidate in candidates:
            if candidate >= start:
                return candidate.isoformat()
        month_index += 1
    raise ValueError('No selected due date fits in the supported date range')


class ArchiveService:
    """Each operation owns a connection; no connections escape to widgets."""

    def __init__(self, database, export_dir=None):
        self.database = Path(database).resolve()
        self.export_dir = Path(export_dir).resolve() if export_dir is not None else None

    def validate_archive(self):
        with closing(db.connect(self.database)):
            pass

    def today(self, on=None):
        day = dt.date.today().isoformat() if on is None else on
        if dt.date.fromisoformat(day).isoformat() != day:
            raise ValueError("Date must use YYYY-MM-DD")
        day_date = dt.date.fromisoformat(day)
        later_start = min(day_date.toordinal() + 14, dt.date.max.toordinal())
        later_boundary = dt.date.fromordinal(later_start).isoformat()
        with closing(db.connect(self.database)) as con:
            # Keep all sections on the same date and SQLite snapshot.
            con.execute('BEGIN')
            open_tasks = [dict(row) for row in reads.open_tasks(con)]
            series = {row['id']: row for row in con.execute(
                'SELECT id, frequency, interval, anchor_date, month_day, '
                'second_month_day, end_date, status FROM recurrences')}

            def labeled(rows):
                return [{**dict(row), 'repeat_label': _repeat_label(
                    series.get(row['recurrence_id']))} for row in rows]

            return {
                'date': day,
                'overdue': labeled(reads.overdue(con, on=day)),
                'due_today': labeled(reads.due_today(con, on=day)),
                'due_soon': labeled(reads.due_soon(con, days=14, on=day)),
                'later': labeled(task for task in open_tasks
                                 if task['due_date'] is not None and task['due_date'] > later_boundary),
                'undated': labeled(task for task in open_tasks if task['due_date'] is None),
                'followups': [dict(row) for row in reads.measurement_followups(con, due_only=True, on=day)],
            }

    def references(self):
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN')
            categories = [row['name'] for row in reads.categories(con, active_only=True)]
            return {
                'categories': categories,
                'roles': {category: [row['name'] for row in reads.duties(con, category)]
                          for category in categories},
                'measures': [row['name'] for row in reads.measures(con)],
            }

    def history_filters(self):
        """Include archived references and names preserved in completion snapshots."""
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN')
            categories = {row['name'] for row in reads.categories(con)}
            categories.update(row['category'] for row in con.execute(
                'SELECT DISTINCT category FROM completions'))
            roles = {row['name'] for row in reads.duties(con)}
            roles.update(row['duty'] for row in con.execute(
                'SELECT DISTINCT duty FROM completions WHERE duty IS NOT NULL'))
            return {
                'categories': sorted(categories, key=str.casefold),
                'roles': sorted(roles, key=str.casefold),
            }

    def history_report(self, since=None, until=None, category=None, duty=None):
        """Read historical counts and latest completion snapshots in one transaction."""
        for label, value in (('Start date', since), ('End date', until)):
            if value is not None:
                try:
                    parsed = dt.date.fromisoformat(value)
                except (TypeError, ValueError):
                    raise ValueError(f'{label} must use YYYY-MM-DD') from None
                if not isinstance(value, str) or parsed.isoformat() != value:
                    raise ValueError(f'{label} must use YYYY-MM-DD')
        if since is not None and until is not None and since > until:
            raise ValueError('Start date must not be after end date')
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN')
            scope = {'since': since, 'until': until, 'category': category, 'duty': duty}
            return {
                **scope,
                'summary': dict(analytics.volume(con, **scope)),
                'measures': [dict(row) for row in analytics.measure_totals(con, **scope)],
                'categories': [dict(row) for row in analytics.completions_by_category(con, **scope)],
                'accomplishments': [dict(row) for row in reads.completion_list(con, **scope)],
            }

    def category_options(self):
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN')
            return [
                {'name': row['name'], 'archived': row['status'] == 'archived',
                 'in_use': bool(con.execute(
                     """SELECT 1 FROM category_roles WHERE category=?
                        UNION SELECT 1 FROM tasks WHERE category=?
                        UNION SELECT 1 FROM recurrences WHERE category=?
                        UNION SELECT 1 FROM completions WHERE category=? LIMIT 1""",
                     (row['name'],) * 4,
                 ).fetchone()), 'counts_toward_review': bool(row['counts_toward_review'])}
                for row in reads.categories(con)
            ]

    def _save_category_change(self, operation, *args):
        with closing(db.connect(self.database)) as con:
            operation(con, *args)
            if self.export_dir is not None:
                try:
                    export.write_all(con, self.export_dir)
                except (export.ExportError, sqlite3.Error, OSError) as exc:
                    return str(exc)
        return None

    def create_category(self, name, counts_toward_review=True):
        return self._save_category_change(writes.create_category, name, counts_toward_review)

    def archive_category(self, name):
        return self._save_category_change(writes.set_category_status, name, True)

    def restore_category(self, name):
        return self._save_category_change(writes.set_category_status, name, False)

    def delete_category(self, name):
        return self._save_category_change(writes.delete_category, name)

    def preview_task(self, draft):
        """Normalize a task draft without creating rows or registering a role."""
        title = _text(draft.get('title'), 'Title', required=True)
        category = _text(draft.get('category'), 'Category', required=True)
        duty = _text(draft.get('duty'), 'Role')
        new_duty = _text(draft.get('new_duty'), 'New role', required=draft.get('new_duty') is not None)
        if duty is not None and new_duty is not None:
            raise ValueError("Choose an existing role or create a new one")
        due_date = draft.get('due_date')
        if due_date is not None:
            try:
                parsed = dt.date.fromisoformat(due_date)
            except (TypeError, ValueError):
                raise ValueError("Due date must use YYYY-MM-DD") from None
            if parsed.isoformat() != due_date:
                raise ValueError("Due date must use YYYY-MM-DD")
        repeat = draft.get('repeat')
        end_date = draft.get('end_date')
        if end_date is not None:
            if not isinstance(end_date, str):
                raise ValueError('End date must use YYYY-MM-DD')
            try:
                parsed_end = dt.date.fromisoformat(end_date)
            except ValueError:
                raise ValueError('End date must use YYYY-MM-DD') from None
            if parsed_end.isoformat() != end_date:
                raise ValueError('End date must use YYYY-MM-DD')
        interval = draft.get('interval', 1)
        weekday = draft.get('weekday')
        month_day = draft.get('month_day')
        second_month_day = draft.get('second_month_day')
        if weekday is not None and (type(weekday) is not int or not 0 <= weekday <= 6):
            raise ValueError('Weekday must be a number from 0 (Monday) to 6 (Sunday)')
        for name, value in (('Month day', month_day), ('Second month day', second_month_day)):
            if value is not None and (type(value) is not int or not 1 <= value <= 31):
                raise ValueError(f'{name} must be a number from 1 to 31')
        if repeat is None:
            if end_date is not None:
                raise ValueError('End date requires a repeat schedule')
            if interval != 1 or type(interval) is not int:
                raise ValueError("Interval requires a repeat schedule")
            if any(value is not None for value in (weekday, month_day, second_month_day)):
                raise ValueError('Schedule selectors require a repeat schedule')
        else:
            if repeat == 'weekly':
                if month_day is not None or second_month_day is not None:
                    raise ValueError('Month days require a monthly repeat schedule')
                if weekday is not None and due_date is not None:
                    start = dt.date.fromisoformat(due_date)
                    try:
                        due_date = (start + dt.timedelta(
                            days=(weekday - start.weekday()) % 7)).isoformat()
                    except OverflowError:
                        raise ValueError('No selected due date fits in the supported date range') from None
            elif repeat == 'monthly':
                if weekday is not None:
                    raise ValueError('Weekday requires a weekly repeat schedule')
                if second_month_day is not None and month_day is None:
                    raise ValueError('Second month day requires a first month day')
                if second_month_day is not None:
                    if second_month_day == month_day:
                        raise ValueError('Choose two different month days')
                    if interval != 1:
                        raise ValueError('Two month days require a monthly interval of 1')
                    month_day, second_month_day = sorted((month_day, second_month_day))
                if month_day is not None and due_date is not None:
                    due_date = _monthly_start(dt.date.fromisoformat(due_date),
                                              *((month_day, second_month_day)
                                                if second_month_day is not None else (month_day,)))
            elif any(value is not None for value in (weekday, month_day, second_month_day)):
                raise ValueError('Schedule selectors require weekly or monthly repeat')
            occurrence_date(due_date, repeat, interval, 0, month_day, second_month_day)
            if end_date is not None and end_date < due_date:
                raise ValueError('End date must be on or after the first due date')
        with closing(db.connect(self.database)) as con:
            if category not in [row['name'] for row in reads.categories(con, active_only=True)]:
                raise ValueError(f"Unknown category: {category}")
            writes.require_role(con, category, duty)
        prepared = {
            'values': {'title': title, 'category': category, 'duty': duty,
                       'due_date': due_date},
            'new_duty': new_duty,
        }
        if repeat is not None:
            prepared['values'].update(repeat=repeat, interval=interval)
            if end_date is not None:
                prepared['values']['end_date'] = end_date
            if month_day is not None:
                prepared['values'].update(month_day=month_day,
                                          second_month_day=second_month_day)
        return prepared

    def save_task(self, draft):
        prepared = self.preview_task(draft)
        with closing(db.connect(self.database)) as con:
            task_id = writes.save_guided(con, 'add', **prepared)
            export_error = None
            if self.export_dir is not None:
                try:
                    export.write_all(con, self.export_dir)
                except (export.ExportError, sqlite3.Error, OSError) as exc:
                    # The task is committed; retry the export, never the save.
                    export_error = str(exc)
            return TaskSaveResult(task_id, export_error)

    def task_details(self, task_id):
        """Return a full snapshot of an open task for a completion draft."""
        with closing(db.connect(self.database)) as con:
            task = reads.get_task(con, task_id)
            if task is None or task['status'] != 'open':
                raise ValueError("Choose an open task to complete")
            return dict(task)

    def recurrence_details(self, task_id):
        """Return the raw series snapshot for a task, if it belongs to one."""
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN')
            task = reads.get_task(con, task_id)
            if task is None or task['recurrence_id'] is None:
                return None
            series = reads.recurrence_details(con, task['recurrence_id'])
            return None if series is None else dict(series)

    def stop_repeating(self, expected_task, expected_series):
        """Cancel an open occurrence and stop its series after snapshot checks."""
        with closing(db.connect(self.database)) as con:
            with con:
                con.execute('BEGIN IMMEDIATE')
                task = reads.get_task(con, expected_task['id'])
                series = reads.recurrence_details(con, expected_series['id'])
                if (task is None or dict(task) != expected_task or task['status'] != 'open'
                        or series is None or dict(series) != expected_series
                        or series['status'] != 'active'
                        or task['recurrence_id'] != series['id']):
                    raise ValueError("This recurring task changed. Select it again")
                writes.cancel_task(con, task['id'])
            export_error = None
            if self.export_dir is not None:
                try:
                    export.write_all(con, self.export_dir)
                except (export.ExportError, sqlite3.Error, OSError) as exc:
                    export_error = str(exc)
            return TaskSaveResult(expected_task['id'], export_error)

    def preview_due_date(self, expected_task, due_date):
        """Validate a new due date against a full, current open-task snapshot."""
        if due_date is not None:
            try:
                parsed = dt.date.fromisoformat(due_date)
            except (TypeError, ValueError):
                raise ValueError("Due date must use YYYY-MM-DD") from None
            if parsed.isoformat() != due_date:
                raise ValueError("Due date must use YYYY-MM-DD")
        with closing(db.connect(self.database)) as con:
            task = reads.get_task(con, expected_task['id'])
            if task is None or dict(task) != expected_task or task['status'] != 'open':
                raise ValueError("This task changed. Select it again")
        return due_date

    def save_due_date(self, expected_task, due_date):
        """Change one open occurrence and report any post-commit export failure."""
        normalized = self.preview_due_date(expected_task, due_date)
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN IMMEDIATE')
            with con:
                task = reads.get_task(con, expected_task['id'])
                if task is None or dict(task) != expected_task or task['status'] != 'open':
                    raise ValueError("This task changed. Select it again")
                writes.reschedule_task(con, expected_task['id'], normalized)
            export_error = None
            if self.export_dir is not None:
                try:
                    export.write_all(con, self.export_dir)
                except (export.ExportError, sqlite3.Error, OSError) as exc:
                    export_error = str(exc)
            return TaskSaveResult(expected_task['id'], export_error)

    def preview_completion(self, expected_task, draft):
        """Normalize a task completion without writing or changing task context."""
        with closing(db.connect(self.database)) as con:
            task = reads.get_task(con, expected_task['id'])
            if task is None or dict(task) != expected_task or task['status'] != 'open':
                raise ValueError("This task changed. Select it again")
            values, new_measure = _completion_evidence(draft)
            if values['measure'] is not None and values['measure'] not in [
                    row['name'] for row in reads.measures(con)]:
                raise ValueError(f"Unknown unit: {values['measure']}")
        values['task_id'] = expected_task['id']
        return {'values': values, 'new_measure': new_measure}

    def save_completion(self, expected_task, draft):
        prepared = self.preview_completion(expected_task, draft)
        with closing(db.connect(self.database)) as con:
            completion_id = writes.save_guided(
                con, 'done', **prepared, expected_task=expected_task,
            )
            export_error = None
            if self.export_dir is not None:
                try:
                    export.write_all(con, self.export_dir)
                except (export.ExportError, sqlite3.Error, OSError) as exc:
                    export_error = str(exc)
            return SaveResult(completion_id, export_error)

    def preview_capture(self, draft):
        """Normalize a draft without creating rows or registering references."""
        category = _text(draft.get('category'), 'Category', required=True)
        values, new_measure = _completion_evidence(draft)
        duty = _text(draft.get('duty'), 'Role')
        new_duty = _text(draft.get('new_duty'), 'New role', required=draft.get('new_duty') is not None)
        if duty is not None and new_duty is not None:
            raise ValueError("Choose an existing role or create a new one")
        with closing(db.connect(self.database)) as con:
            if category not in [row['name'] for row in reads.categories(con, active_only=True)]:
                raise ValueError(f"Unknown category: {category}")
            writes.require_role(con, category, duty)
            if values['measure'] is not None and values['measure'] not in [
                    row['name'] for row in reads.measures(con)]:
                raise ValueError(f"Unknown unit: {values['measure']}")
        values.update(category=category, duty=duty)
        return {
            'values': values,
            'new_duty': new_duty, 'new_measure': new_measure,
        }

    def save_capture(self, draft):
        prepared = self.preview_capture(draft)
        with closing(db.connect(self.database)) as con:
            completion_id = writes.save_guided(con, 'log', **prepared)
            export_error = None
            if self.export_dir is not None:
                try:
                    export.write_all(con, self.export_dir)
                except (export.ExportError, sqlite3.Error, OSError) as exc:
                    # The archive write already committed. Never offer a save retry.
                    export_error = str(exc)
            return SaveResult(completion_id, export_error)

    def retry_export(self):
        if self.export_dir is None:
            raise ValueError("Choose an export folder first")
        with closing(db.connect(self.database)) as con:
            export.write_all(con, self.export_dir)
