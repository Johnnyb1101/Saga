"""UI-independent desktop operations over Saga's existing database core."""

import datetime as dt
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from saga import db, export, reads, writes


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
            return {
                'date': day,
                'overdue': [dict(row) for row in reads.overdue(con, on=day)],
                'due_today': [dict(row) for row in reads.due_today(con, on=day)],
                'due_soon': [dict(row) for row in reads.due_soon(con, days=14, on=day)],
                'later': [task for task in open_tasks
                          if task['due_date'] is not None and task['due_date'] > later_boundary],
                'undated': [task for task in open_tasks if task['due_date'] is None],
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
        with closing(db.connect(self.database)) as con:
            if category not in [row['name'] for row in reads.categories(con, active_only=True)]:
                raise ValueError(f"Unknown category: {category}")
            writes.require_role(con, category, duty)
        return {
            'values': {'title': title, 'category': category, 'duty': duty,
                       'due_date': due_date},
            'new_duty': new_duty,
        }

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
