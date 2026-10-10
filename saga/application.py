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
        with closing(db.connect(self.database)) as con:
            # Keep all sections on the same date and SQLite snapshot.
            con.execute('BEGIN')
            return {
                'date': day,
                'overdue': [dict(row) for row in reads.overdue(con, on=day)],
                'due_today': [dict(row) for row in reads.due_today(con, on=day)],
                'due_soon': [dict(row) for row in reads.due_soon(con, days=14, on=day)],
                'followups': [dict(row) for row in reads.measurement_followups(con, due_only=True, on=day)],
            }

    def references(self):
        with closing(db.connect(self.database)) as con:
            con.execute('BEGIN')
            categories = [row['name'] for row in reads.categories(con)]
            return {
                'categories': categories,
                'roles': {category: [row['name'] for row in reads.duties(con, category)]
                          for category in categories},
                'measures': [row['name'] for row in reads.measures(con)],
            }

    def preview_capture(self, draft):
        """Normalize a draft without creating rows or registering references."""
        category = _text(draft.get('category'), 'Category', required=True)
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
        duty = _text(draft.get('duty'), 'Role')
        new_duty = _text(draft.get('new_duty'), 'New role', required=draft.get('new_duty') is not None)
        measure = _text(draft.get('measure'), 'Unit')
        new_measure = _text(draft.get('new_measure'), 'New unit', required=draft.get('new_measure') is not None)
        if duty is not None and new_duty is not None:
            raise ValueError("Choose an existing role or create a new one")
        if measure is not None and new_measure is not None:
            raise ValueError("Choose an existing unit or create a new one")
        status = draft.get('measurement_status')
        if status not in ('measured', 'not_applicable', 'unknown'):
            raise ValueError("Choose Measured, Not applicable, or Not known yet")
        quantity = draft.get('quantity')
        if quantity is not None:
            if isinstance(quantity, bool):
                raise ValueError("Quantity must be a number")
            try:
                quantity = float(quantity)
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Quantity must be a finite number") from None
        unit = measure or new_measure
        writes.measurement_state(unit, quantity, status)
        if status == 'measured' and (unit is None or quantity is None):
            raise ValueError("Measured requires both a unit and quantity")
        reminder = draft.get('remind_on')
        if status == 'unknown':
            writes.validate_reminder(reminder)
        elif reminder is not None:
            raise ValueError("A reminder is only valid for Not known yet")
        flagged = draft.get('flagged', False)
        if not isinstance(flagged, bool):
            raise ValueError("Review flag must be Yes or No")  # noqa: TRY004 - invalid form data
        with closing(db.connect(self.database)) as con:
            if category not in [row['name'] for row in reads.categories(con)]:
                raise ValueError(f"Unknown category: {category}")
            writes.require_role(con, category, duty)
            if measure is not None and measure not in [row['name'] for row in reads.measures(con)]:
                raise ValueError(f"Unknown unit: {measure}")
        return {
            'values': {'category': category, 'outcome': outcome, 'completed_at': completed_at,
                       'duty': duty, 'measure': measure, 'quantity': quantity,
                       'measurement_status': status, 'remind_on': reminder, 'flagged': flagged},
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
