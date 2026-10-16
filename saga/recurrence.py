"""Calendar dates anchored to the first occurrence, independent of completion."""

import calendar
import datetime as dt
import math
from bisect import bisect_right


class RecurrenceDateOverflow(ValueError):
    """A valid forward schedule has advanced beyond Python's date range."""


def occurrence_date(anchor_date, frequency, interval, occurrence,
                    month_day=None, second_month_day=None):
    """Return the date of a zero-based occurrence without month-end drift."""
    if frequency not in ("daily", "weekly", "monthly"):
        raise ValueError("repeat must be daily, weekly, or monthly")
    if type(interval) is not int or not 1 <= interval <= 9223372036854775807:
        raise ValueError("recurrence interval must be a positive SQLite integer")
    if type(occurrence) is not int or occurrence < 0:
        raise ValueError("occurrence must be a nonnegative integer")
    for day in (month_day, second_month_day):
        if day is not None and (type(day) is not int or not 1 <= day <= 31):
            raise ValueError("monthly days must be integers from 1 to 31")
    if (month_day is not None or second_month_day is not None) and frequency != "monthly":
        raise ValueError("monthly days require monthly recurrence")
    if second_month_day is not None and (month_day is None or second_month_day <= month_day):
        raise ValueError("twice-monthly days must be distinct and in ascending order")
    try:
        anchor = dt.date.fromisoformat(anchor_date)
    except (TypeError, ValueError):
        raise ValueError("recurring tasks require a valid YYYY-MM-DD due date") from None
    if anchor.isoformat() != anchor_date:
        raise ValueError("recurring tasks require a valid YYYY-MM-DD due date")
    if month_day is not None and anchor.day not in _month_days(
            anchor.year, anchor.month, month_day, second_month_day):
        raise ValueError("anchor date must match a selected monthly day")
    try:
        offset = interval * occurrence
        if frequency == "monthly":
            if second_month_day is not None:
                return _twice_monthly(anchor, interval, occurrence, month_day, second_month_day)
            month_index = (anchor.year - 1) * 12 + anchor.month - 1 + offset
            year, month = divmod(month_index, 12)
            year, month = year + 1, month + 1
            day = min(month_day or anchor.day, calendar.monthrange(year, month)[1])
            return dt.date(year, month, day).isoformat()
        days = offset * (7 if frequency == "weekly" else 1)
        return (anchor + dt.timedelta(days=days)).isoformat()
    except (ValueError, OverflowError):
        raise RecurrenceDateOverflow("next recurrence exceeds the supported date range; stop the series before completing") from None


def _month_days(year, month, first, second):
    last = calendar.monthrange(year, month)[1]
    return sorted({min(day, last) for day in (first, second) if day is not None})


def _twice_monthly(anchor, interval, occurrence, first, second):
    """Locate an occurrence using at most one Gregorian 400-year cycle."""
    start = (anchor.year - 1) * 12 + anchor.month - 1
    # Include the skipped early date in the absolute sequence index when the
    # series starts on its second date. A clamped duplicate counts only once.
    occurrence += _month_days(anchor.year, anchor.month, first, second).index(anchor.day)
    period = 4800 // math.gcd(4800, interval)
    counts = [0]
    for slot in range(period):
        year, month = divmod((start + slot * interval) % 4800, 12)
        counts.append(counts[-1] + len(_month_days(year + 1, month + 1, first, second)))
    cycles, within = divmod(occurrence, counts[-1])
    slot = bisect_right(counts, within) - 1
    month_index = start + (cycles * period + slot) * interval
    year, month = divmod(month_index, 12)
    year, month = year + 1, month + 1
    days = _month_days(year, month, first, second)
    return dt.date(year, month, days[within - counts[slot]]).isoformat()
