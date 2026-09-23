"""Calendar dates anchored to the first occurrence, independent of completion."""

import calendar
import datetime as dt


def occurrence_date(anchor_date, frequency, interval, occurrence):
    """Return the date of a zero-based occurrence without month-end drift."""
    if frequency not in ("daily", "weekly", "monthly"):
        raise ValueError("repeat must be daily, weekly, or monthly")
    if type(interval) is not int or not 1 <= interval <= 9223372036854775807:
        raise ValueError("recurrence interval must be a positive SQLite integer")
    if type(occurrence) is not int or occurrence < 0:
        raise ValueError("occurrence must be a nonnegative integer")
    try:
        anchor = dt.date.fromisoformat(anchor_date)
    except (TypeError, ValueError):
        raise ValueError("recurring tasks require a valid YYYY-MM-DD due date") from None
    if anchor.isoformat() != anchor_date:
        raise ValueError("recurring tasks require a valid YYYY-MM-DD due date")
    try:
        offset = interval * occurrence
        if frequency == "monthly":
            month_index = (anchor.year - 1) * 12 + anchor.month - 1 + offset
            year, month = divmod(month_index, 12)
            year, month = year + 1, month + 1
            day = min(anchor.day, calendar.monthrange(year, month)[1])
            return dt.date(year, month, day).isoformat()
        days = offset * (7 if frequency == "weekly" else 1)
        return (anchor + dt.timedelta(days=days)).isoformat()
    except (ValueError, OverflowError):
        raise ValueError("next recurrence exceeds the supported date range; stop the series before completing") from None
