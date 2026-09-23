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

Not built yet: automatic backup scheduling/retention and a graphical interface.

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
python main.py cancel ID                # cancel an open task; no completion logged
python main.py reschedule ID --due DATE # change an open task's due date
python main.py reschedule ID --clear-due
python main.py projects                 # all project ids, statuses, and open counts
python main.py close-project ID         # mark done only when no open tasks remain
python main.py add "text" [-c NAME] [--due DATE] [--project ID]
python main.py add "text" -c work --due DATE --repeat monthly [--interval 1]
python main.py recurrences              # series ids, schedules, and current tasks
python main.py stop-recurrence ID       # stop generation; retain the current task
python main.py done ID [--outcome "..."] [--measure NAME] [--quantity N] [--flag]
python main.py log "Outcome" -c work [--duty NAME] [--measure NAME] [--quantity N] [--flag]
python main.py upcoming [--days 30]     # project deadlines approaching
python main.py project "name" [--deadline DATE]
python main.py measure ["NAME"]         # list measures, or register one
python main.py review [--since DATE] [--until DATE]
python main.py completions [--since DATE] [--until DATE] [--duty NAME]
python main.py history ID               # all revisions of a completion
python main.py correct ID --reason "..." --outcome "..."
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

## Task and project maintenance

Use `list` to find a task id and `projects` to find a project id. `cancel`
retains the task with status `cancelled` and records no accomplishment.
`reschedule` requires either `--due YYYY-MM-DD` or `--clear-due`; past dates
are valid. Both commands accept only open tasks and leave completion history
unchanged. Successfully changing the default database refreshes exports.

`projects` includes undated, on-hold, and closed projects. `close-project`
marks an active or on-hold project done only after every task is completed
or cancelled. It does not change task statuses or create completions. New
tasks cannot be assigned to done or cancelled projects. Reopening is not
part of these commands. Cancelling a recurring task also stops its series.

## Recurring tasks

Create a series with a first due date and a daily, weekly, or monthly interval:

```powershell
python main.py add "Check the monthly report" -c work --due 2026-09-30 --repeat monthly
python main.py add "Review the checklist" -c work --due 2026-09-28 --repeat weekly --interval 2
python main.py recurrences
```

`--interval` defaults to 1 and must be a positive integer. The first due date
anchors the schedule. Monthly dates clamp to the last valid day of a short
month, then return to the original day: January 31, February 28 (or 29),
March 31. Every two weeks means 14 days from each scheduled occurrence.

There is at most one open task per series. Completing it archives the result
and creates the next task in the same transaction; if either step fails,
both roll back. Early, late, or backdated completion does not move the
schedule. Missed occurrences are not silently skipped: the next task may
already be overdue. Reading a brief does not create more tasks.

Rescheduling or clearing the current task's due date affects only that
occurrence. Later instances retain the original schedule and series title,
category, duty, and project. A duty override while completing a task applies
only to that completion. Correcting an archived completion never generates
another task or changes the schedule.

`cancel TASK_ID` cancels the current task and stops its series without adding
an accomplishment. `stop-recurrence SERIES_ID` stops future generation but
keeps the current task open; completing it will not create a successor.
Use `recurrences` to find series ids and `list` to find task ids. A project
cannot close until its current tasks are resolved. To finish a recurring
project, stop its series and complete the last task, or cancel that task.
Stopped series cannot be restarted or edited in this version; create a new
series for a changed pattern. Records from the old series remain intact.

Existing databases require `python main.py migrate` for schema version 3,
followed by `python main.py export`. Existing tasks remain one-off tasks;
no completion history is changed. Brief JSON task objects gain nullable
`recurrence_id` and `occurrence_index` fields; existing fields remain intact.

## Historical context and corrections

New completions save the task title, due date, project name, and category's
review eligibility at capture time. Later edits to working tasks, projects,
or category eligibility do not change those saved values or historical
on-time results. Category, duty, and measure names already referenced by the
archive cannot be renamed: their cascading update would rewrite evidence.

Use `completions` to find the latest completion id, then append a correction:

```powershell
python main.py completions --since 2026-09-01 --duty "Operations"
python main.py correct 12 --reason "Recounted completed packages" --quantity 8
python main.py history 12
```

Completion ids are separate from task ids. Each correction gets a new id,
links to the revision it replaces, and records its reason and entry time.
Correct the latest id; `history` accepts any id in the chain and shows every
revision. Originals are retained, with database guards against updates,
deletes, and replacement inserts. These guards prevent ordinary accidental
edits; they do not make the database tamper-proof against an administrator.

Reports, exports, measure usage, and duty usage count only the latest
revision. Filtering happens after superseded rows are excluded, so changing
a completion date or duty also removes the old revision from its former
period or duty. Corrections do not reopen or otherwise change tasks.

`correct` preserves omitted fields. It supports `--outcome`, `--category`,
`--date`, `--duty`, `--measure`, `--quantity`, `--flag` / `--no-flag`,
`--task-title`, `--due-date`, `--project-name`, and
`--review-counting` / `--no-review-counting`. Use `--clear-duty`,
`--clear-task-title`, `--clear-due-date`, or `--clear-project-name` to remove
optional context. `--clear-measure` removes both the measure and quantity.
The resulting record must have a nonblank outcome and a complete measure /
quantity pair. Empty reasons, unchanged corrections, and stale ids are rejected.
Changing category uses that category's current review eligibility unless
explicitly overridden; other corrections preserve the saved eligibility.

Archive snapshots were introduced in schema version 2; `python main.py migrate`
applies that migration and any later migrations needed by the current code.
Migration keeps original completion fields intact and backfills context
from the tasks, projects, and categories as they exist **at migration time**.
It cannot reconstruct earlier values. Those rows are marked `backfilled`,
and their original entry time remains unknown. Corrections retain that
provenance; their reason documents any corrected historical context.
New records are marked `captured`, including backdated work entered today.
`recorded_at` is the entry time; `completed_at` is when the work happened.

After migrating, run `python main.py export` to regenerate existing exports.
The review JSON format is now version 2: flagged items include snapshot
provenance and predecessor ids, and totals count current revisions only.
The brief JSON format remains version 1.

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
filters, immutable snapshots, correction chains, migration preservation,
and exports. The version-zero schema in
`tests/fixtures/schema_v0.sql` is frozen from commit `7ff0f99^`; keep it
independent of the current schema. `tests/fixtures/schema_v1.sql` preserves
the schema before archive history was introduced, and `schema_v2.sql` preserves
the schema before recurrence. Migration tests also inject failures to
verify rollback of schema, records, and version, and successful retry.
Atomic export publication is not established by these tests.

Migrations commit one at a time, after checking their resulting schema
version. A failing migration rolls back completely; earlier successful
migrations remain applied. Each migration run retains a uniquely named
pre-migration backup. Migration SQL must leave transaction control to the
runner: do not include `BEGIN`, `COMMIT`, or `ROLLBACK` statements.

## License

MIT. See [LICENSE](LICENSE).
