"""Generate the files that anything outside this program reads.

The morning brief, a phone, or any other consumer reads exports/, never the
database. That boundary means the schema can change without breaking them,
and it is why brief.json carries a schema_version.
"""

import datetime as dt
import json

from saga import analytics, db, reads

SCHEMA_VERSION = 1
EXPORT_DIR = db.ROOT / "exports"


def as_dicts(rows):
    """sqlite3.Row is not JSON-serialisable. Plain dicts are."""
    return [dict(row) for row in rows]


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_brief(con, soon_days=7, deadline_days=30):
    """The daily view: what is late, what is due, what is coming."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now(),
        "date": dt.date.today().isoformat(),
        "overdue": as_dicts(reads.overdue(con)),
        "due_today": as_dicts(reads.due_today(con)),
        "due_soon": as_dicts(reads.due_soon(con, days=soon_days)),
        "upcoming": as_dicts(reads.upcoming_deadlines(con, days=deadline_days)),
    }


def build_review(con, since=None, until=None):
    """The archive view: totals and flagged work for a period."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now(),
        "since": since,
        "until": until,
        "volume": dict(analytics.volume(con, since, until)),
        "measures": as_dicts(analytics.measure_totals(con, since, until)),
        "by_category": as_dicts(analytics.completions_by_category(con, since, until)),
        "on_time": as_dicts(analytics.on_time_rate(con, since, until)),
        "flagged": as_dicts(analytics.flagged_work(con, since, until)),
    }


def brief_markdown(brief):
    """Render the brief as plain Markdown, for reading on a phone."""
    lines = [f"# Brief - {brief['date']}", ""]

    if brief["overdue"]:
        lines.append("## Overdue")
        for task in brief["overdue"]:
            lines.append(f"- {task['title']} ({task['category']}, "
                         f"due {task['due_date']})")
        lines.append("")

    if brief["due_today"]:
        lines.append("## Due today")
        for task in brief["due_today"]:
            lines.append(f"- {task['title']} ({task['category']})")
        lines.append("")

    if brief["due_soon"]:
        lines.append("## Coming up")
        for task in brief["due_soon"]:
            lines.append(f"- {task['title']} ({task['category']}, "
                         f"due {task['due_date']})")
        lines.append("")

    if brief["upcoming"]:
        lines.append("## Deadlines ahead")
        for project in brief["upcoming"]:
            lines.append(f"- {project['days_left']}d - {project['name']} "
                         f"({project['open_tasks']} open)")
        lines.append("")

    if not (brief["overdue"] or brief["due_today"]
            or brief["due_soon"] or brief["upcoming"]):
        lines.append("Nothing due and no deadlines ahead.")
        lines.append("")

    lines.append(f"_Generated {brief['generated_at']}_")
    return "\n".join(lines)


def write_all(con, out_dir=EXPORT_DIR, since=None, until=None):
    """Regenerate every export. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)

    brief = build_brief(con)
    review = build_review(con, since, until)

    written = []
    for name, content in [
        ("brief.json", json.dumps(brief, indent=2)),
        ("brief.md", brief_markdown(brief)),
        ("review.json", json.dumps(review, indent=2)),
    ]:
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written