"""Publish complete files and a final manifest for external consumers."""

import datetime as dt
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from saga import analytics, db, reads

SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 2
EXPORT_DIR = db.ROOT / "exports"
SOON_DAYS = 14
DEADLINE_DAYS = 30
EXPORT_NAMES = ("brief.json", "brief.md", "review.json")
MANIFEST_VERSION = 1


class ExportError(ValueError):
    """An export failed; existing files may require a publication retry."""


def as_dicts(rows):
    """sqlite3.Row is not JSON-serialisable. Plain dicts are."""
    return [dict(row) for row in rows]


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_brief(con, soon_days=SOON_DAYS, deadline_days=DEADLINE_DAYS):
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
        "schema_version": REVIEW_SCHEMA_VERSION,
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
    """Stage all files, replace each atomically, and publish the manifest last."""
    out_dir = Path(out_dir)
    staged = []
    try:
        # A savepoint pins all queries to one snapshot without committing any
        # caller-owned transaction. No filesystem work holds the read snapshot.
        con.execute("SAVEPOINT saga_export_snapshot")
        try:
            brief = build_brief(con)
            review = build_review(con, since, until)
        finally:
            con.execute("RELEASE SAVEPOINT saga_export_snapshot")
        stamp = now()
        brief["generated_at"] = review["generated_at"] = stamp
        contents = {
            "brief.json": json.dumps(brief, indent=2, allow_nan=False).encode("utf-8"),
            "brief.md": brief_markdown(brief).encode("utf-8"),
            "review.json": json.dumps(review, indent=2, allow_nan=False).encode("utf-8"),
        }
        manifest = {
            "schema_version": MANIFEST_VERSION,
            "generation_id": uuid.uuid4().hex,
            "generated_at": stamp,
            "date": brief["date"],
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()},
        }
        contents["manifest.json"] = json.dumps(manifest, indent=2, allow_nan=False).encode("utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, content in contents.items():
            with tempfile.NamedTemporaryFile(dir=out_dir, prefix=f".{name}-", suffix=".tmp", delete=False) as temp:
                staged.append((Path(temp.name), out_dir / name))
                temp.write(content)
                temp.flush()
                os.fsync(temp.fileno())
        for temporary, destination in staged:
            os.replace(temporary, destination)
        return [destination for _, destination in staged]
    except (OSError, ValueError) as exc:
        raise ExportError(f"Export failed: {exc}. Retry the export; validate manifest.json before using the set.") from exc
    finally:
        cleanup_errors = []
        for temporary, _ in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_errors.append(f"{temporary}: {exc}")
        if cleanup_errors:
            raise ExportError("Export temporary-file cleanup failed: " + "; ".join(cleanup_errors))


def is_stale(out_dir=EXPORT_DIR):
    """True unless today's complete export set matches its final manifest."""
    out_dir = Path(out_dir)
    try:
        manifest_bytes = (out_dir / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_VERSION:
            return True
        if manifest.get("date") != dt.date.today().isoformat():
            return True
        hashes = manifest.get("files")
        if not isinstance(hashes, dict) or set(hashes) != set(EXPORT_NAMES):
            return True
        contents = {name: (out_dir / name).read_bytes() for name in EXPORT_NAMES}
        if any(hashlib.sha256(content).hexdigest() != hashes[name] for name, content in contents.items()):
            return True
        brief = json.loads(contents["brief.json"])
        review = json.loads(contents["review.json"])
        if not isinstance(brief, dict) or not isinstance(review, dict):
            return True
        if brief.get("date") != manifest["date"] or brief.get("schema_version") != SCHEMA_VERSION:
            return True
        if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
            return True
        stamp = manifest.get("generated_at")
        if not isinstance(stamp, str) or brief.get("generated_at") != stamp or review.get("generated_at") != stamp:
            return True
        # If a writer changed the manifest while we read, retry on the next run.
        return (out_dir / "manifest.json").read_bytes() != manifest_bytes
    except (OSError, ValueError):
        return True
