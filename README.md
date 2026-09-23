# Saga

A personal operations database and morning-brief generator. SQLite, a small
Python CLI, and no third-party dependencies.

## The problem

Most task managers are built around *doing* work. This one is built around
*remembering* it.

Performance reviews on a two-year cycle require describing what you
accomplished, with measurable detail, across roughly seven hundred days of
work. Recall over that span is unreliable and heavily biased toward the
last few months. The accomplishments that took the most effort are usually
the ones furthest away.

Saga treats the completion record as the primary artifact and the daily
task list as the mechanism that fills it. Every closed task is archived
with its outcome and a measurable detail, and flagged if it is the kind of
thing worth writing up later. Two years on, the review gets written from
records instead of from memory.

## What it can tell you

This is the reason it is a database and not a text file. The useful
questions are aggregate ones — cheap in SQL, miserable anywhere else.

- Completions by category across a date range.
- Flagged accomplishments, grouped and ready to draft from.
- Projects with a deadline inside thirty days that still have open tasks.
- On-time completion rate by category.
- Total volume per program across a review period.

Run against the bundled demo data:

```
$ python main.py --db data/demo.db review

REVIEW PERIOD  the beginning to today

VOLUME
       61 completions
       53 in review-counting categories
        9 flagged as review material
       26 recorded a number

MEASURES
  packages reviewed                     226   across 10 occasions
  hours saved                            23   across 3 occasions
  personnel onboarded                    22   across 3 occasions
  inspections passed                     10   across 10 occasions

BY CATEGORY
  work         24 completions    83.3% on time
  certs        11 completions    77.8% on time
  school       10 completions    77.8% on time
  home          8 completions    75.0% on time
  personal      8 completions    75.0% on time

FLAGGED - work
  2025-08-12  Ran the spring onboarding cycle   [personnel onboarded: 6]
  2025-10-31  Ran the summer onboarding cycle   [personnel onboarded: 9]
  2025-12-30  Automated the weekly roll-up   [hours saved: 12]
  2026-03-30  Ran the autumn onboarding cycle   [personnel onboarded: 7]
  2026-06-18  Automated the inventory export   [hours saved: 8]
  2026-08-22  Fixed the early-morning outage   [hours saved: 3]
```

Every figure there is summed from records. All of it is invented demo data —
no real entry has ever been in this repository.

## Status

Working end to end.

- [x] Project foundation
- [x] Schema and database layer
- [x] Write path — tasks, projects, completions
- [x] Read path — today, upcoming, overdue
- [x] Analytics and review roll-up
- [x] Command-line interface
- [x] Export for external consumers

Not built yet: recurring tasks and automatic backup scheduling/retention.

## Requirements

Python 3.14 with SQLite 3.37 or later, which is what ships with it. No
third-party packages — `sqlite3` is in the standard library.

The SQLite floor is real: every table is declared `STRICT`, which arrived
in 3.37. Check yours with:

    python -c "import sqlite3; print(sqlite3.sqlite_version)"

## Usage

```
python main.py                          # list every command
python main.py <command> --help         # details for one

python main.py init                     # create the database (first run only)

python main.py today                    # due today, plus anything overdue
python main.py list [-c NAME]           # every open task, with ids
python main.py add "text" [-c NAME] [--due DATE] [--project ID]
python main.py done ID [--outcome "..."] [--measure NAME] [--quantity N] [--flag]
python main.py log "Outcome" -c work [--duty NAME] [--measure NAME] [--quantity N] [--flag]
python main.py upcoming [--days 30]     # project deadlines approaching
python main.py project "name" [--deadline DATE]
python main.py measure ["NAME"]         # list measures, or register one
python main.py review [--since DATE] [--until DATE]
python main.py export [--out DIR]       # regenerate exports/
python main.py backup --out DIR         # create a verified database snapshot

python main.py --db PATH <command>      # run against a different database
```

`add`, `project`, and `done` prompt for anything you leave off, so
`python main.py done 12` walks you through it. Supply the flags and it never
asks — which keeps the commands usable from a scheduled task, where there is
nobody to answer.

Use `log` for work that was never a task; it creates only a completion.
Run `python main.py log` for guided entry, including an optional completion
date. A nonblank outcome is required. With an outcome and category supplied,
optional details are omitted unless provided as flags, and no prompts appear.

Both `log` and `done` accept `--date YYYY-MM-DD` for historical work:

```powershell
python main.py log "Resolved an unexpected outage" -c work --date 2026-09-01 --flag
python main.py done 12 --outcome "Delivered the report" --date 2026-09-01
```

An explicit date is stored at midnight local time; omitted dates use the
current timestamp. Invalid or future completion dates are rejected. For
`done`, supplying only an id and date still prompts for accomplishment details.
Backdating records when work happened; it does not edit an existing completion.

## Backup and recovery

Run `python main.py backup --out "D:\SagaBackups"`, replacing the directory
with your backup location. Each run creates a uniquely named `.db` snapshot
using SQLite's backup API and checks it with `PRAGMA integrity_check`.
The source is opened read-only; exports are not refreshed. A failed copy is
removed. Only use or copy a snapshot after the command reports success.

Choose a separate drive or a synced folder for the closed backup files.
A copy on the same drive does not protect against drive loss. Scheduling
and retention are manual for now; the command never prunes older backups.

To recover, stop Saga commands and the morning scheduled task, then copy a
successful backup to a **new local path**, such as `data/recovered.db`.
Run `python main.py --db data/recovered.db list` and
`python main.py --db data/recovered.db review` to inspect it. Keep the
original database intact until you have confirmed the recovered records.
Use `--db data/recovered.db` to work with the recovered copy. This procedure
does not replace the default database or change the morning task's path.
If a backup predates a schema upgrade, run `migrate` against the recovered
copy first, using the same `--db` option.

## Try it

```
python seed_demo.py
python main.py --db data/demo.db today
python main.py --db data/demo.db review
```

`seed_demo.py` builds `data/demo.db` from invented data, through the same
write functions the CLI uses. Your own database is never touched.

## Design notes

**Completions are a separate table, not columns on tasks.**
A task that recurs has many completions; storing `completed_at` on the task
row caps it at one. Tasks are mutable working state — text gets edited,
dates get moved. Completions are an append-only archive that must never
change. Two lifecycles, two tables.

**A completion does not require a task.**
`completions.task_id` is nullable. Work gets done that was never written
down — the thing that broke at 06:00 and was fixed by 07:00. It still
counts. A required foreign key would force fabricating a retroactive task
to hang it on, or losing the record entirely.

**There is no milestones table.**
A milestone is a task with a due date belonging to a project, which the
schema already expresses. A second table meaning the same thing would
force a judgment call on every insert and a lookup in two places on every
query. If the distinction ever needs to exist, it is a column.

**The database never lives in a synced folder.**
Cloud sync clients copy whole files and have no understanding of SQLite's
locking. A sync landing mid-transaction produces a corrupt remote copy, and
write-ahead-log mode's sidecar files can sync out of order into an
unrecoverable state. The database stays on local disk; a generated
`exports/` directory is what syncs.

**Nothing outside this program reads the database.**
External consumers read `exports/brief.json`, which carries a version
field. The schema can change without breaking anything downstream, and the
consumer never needs to know SQL.

**A stale export is indistinguishable from a quiet day.**
`exports/` regenerated after every write, which is not often enough. A
date passing is not a write, so a stretch with nothing completed left the
brief on disk still answering with an older date — reporting tasks as due
today that were already a day overdue. Every command now compares the
`date` field inside `brief.json` against today and regenerates first if
they differ. Modification time would have been the easier check and the
wrong one: sync clients rewrite it.

## Data handling

This system holds no personally identifiable information about anyone other
than its operator, and no protected or sensitive organizational data.

That constraint is structural rather than procedural: the schema has no
fields for personal identifiers, so there is nowhere for that data to go
even by accident. Task text describes the operator's own work.

`data/` and `exports/` are excluded from version control. No real entry has
ever been committed to this repository. Demo data used in examples and
tests is invented.

## Development checks

Use a virtual environment created with Python 3.14 and install Ruff as a
development tool. On Windows, run:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

Tests use invented records in temporary or in-memory databases. They cover
backup recovery, capture, completion rollback, date boundaries, review
filters, migration preservation, and exports. The version-zero schema in
`tests/fixtures/schema_v0.sql` is frozen from commit `7ff0f99^`; keep it
independent of the current schema. Migration tests also inject failures to
verify rollback of schema, records, and version, and successful retry.
Atomic export publication is not established by these tests.

Migrations commit one at a time, after checking their resulting schema
version. A failing migration rolls back completely; earlier successful
migrations remain applied. Each migration run retains a uniquely named
pre-migration backup. Migration SQL must leave transaction control to the
runner: do not include `BEGIN`, `COMMIT`, or `ROLLBACK` statements.

## License

MIT. See [LICENSE](LICENSE).
