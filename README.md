# Saga

A personal operations database and morning-brief generator. SQLite, a small
Python CLI, and no third-party runtime dependencies.

## The problem

Most task managers are built around *doing* work. This one is built around
*remembering* it.

Performance reviews on a two-year cycle require describing what you
accomplished, with measurable detail, across roughly seven hundred days of
work. Recall over that span is unreliable and heavily biased toward the
last few months. The accomplishments that took the most effort are usually
the ones furthest away.

Saga treats the completion record as the primary artifact and the daily
task list as the mechanism that fills it. Completing a task creates an
archive record; cancelling one does not. Outcomes, measures, and review
flags make the record useful later, but `done` does not require them.
Standalone `log` entries require an outcome. Two years on, the review can
be written from the evidence actually recorded instead of from memory.

## What it can tell you

This is the reason it is a database and not a text file. The useful
questions are aggregate ones — cheap in SQL, miserable anywhere else.

- Completions by category across a date range.
- Flagged accomplishments, grouped and ready to draft from.
- Active projects overdue or due within thirty days, with open-task counts
  (including zero).
- On-time completion rate by category.
- Volume and measures filtered by registered duty across a review period.

Excerpt from `review` against generated, invented demo data:

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

```

Every figure there is summed from invented records. The full report also
lists flagged accomplishments with their completion ids.
Demo dates are relative to the day the database is generated.

## Status

The CLI covers capture, daily work, review evidence, and recovery.

- [x] Project foundation
- [x] Schema and database layer
- [x] Write path — tasks, projects, completions
- [x] Read path — today, upcoming, overdue
- [x] Analytics and review roll-up
- [x] Command-line interface
- [x] Export for external consumers
- [x] Standalone and backdated accomplishments
- [x] Verified backups and recovery tests
- [x] Historical snapshots and append-only corrections
- [x] Transactional migrations with failure tests
- [x] Task cancellation, rescheduling, and project closure
- [x] Daily, weekly, and monthly recurrence
- [x] Morning brief script (scheduler setup is machine-specific)

The guided terminal interface covers daily capture and task selection.
Remaining work includes task-field editing, distinct unknown/not-applicable
evidence states, consistent connection cleanup, and export-destination configuration.
A future graphical interface will use the same core functions.

## Requirements

Python 3.14 with SQLite 3.37 or later. `sqlite3` is in the standard library;
Ruff is an optional development tool, not a runtime requirement.

The SQLite floor is real: every table is declared `STRICT`, which arrived
in 3.37. Check yours with:

    python -c "import sqlite3; print(sqlite3.sqlite_version)"

## Setup and upgrades

From the repository directory on Windows, create an environment using a
complete Python 3.14 installation:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.sqlite_version)"
```

If the launcher is unavailable or points to an incomplete installation, use
the full path to a working Python 3.14 executable for the first command.
Reusing an environment created with an older interpreter does not upgrade it.
The examples below use `python` for brevity; replace it with
`.\.venv\Scripts\python.exe` when the environment is not activated.

For a **new** database:

```powershell
.\.venv\Scripts\python.exe main.py init
```

For an **existing** database after updating the code:

```powershell
.\.venv\Scripts\python.exe main.py migrate
.\.venv\Scripts\python.exe main.py export
```

Do not run `init` to upgrade: it refuses an existing file. The current database
schema is version 3. Migration saves a local backup before pending changes
and commits each migration only after checking its version. That local copy
does not replace a separate-drive backup. To use another database, put
`--db PATH` before the command. When exporting an alternate database, also
use `--out DIR` to avoid overwriting the default exports.

## Usage

### Guided daily workflow

Launch Saga from a terminal without a command:

```powershell
.\.venv\Scripts\python.exe main.py
```

The menu stays open until you exit. It offers task browsing, adding and
completing tasks, recording standalone accomplishments, rescheduling, and
cancelling tasks. A missing database offers an explicit create-or-cancel
choice; an old database still requires the separate migration command.

Select tasks by their numbered **rows**, not by typing database ids. Lists
show title, category, duty, project, and deadline. Search matches title,
category, duty, and project text; category and due-today/overdue filters and
eight-row pages keep longer lists manageable. Database ids remain visible
to distinguish otherwise identical tasks and for direct-command shortcuts.

Every form requires an explicit answer. Select a category, choose existing
or new duties/projects/measures where offered, or deliberately choose
`None / not applicable`. Blank answers do not silently choose defaults.
Numbers must be finite; zero is a real measurement. Review relevance requires
an explicit Yes or No. Completion requires an outcome in this guided interface.
Completing a task asks you to confirm its existing category/project context
and choose whether to retain its duty. Choosing no duty applies only to this
completion; it does not erase the task's duty.

Before saving, review the summary and choose **Save**, **Edit answers**, or
**Discard draft**. `Back` or `:back` revisits a question while retaining draft
answers; `:discard` explicitly discards the form. At the first question,
Back keeps you there rather than silently discarding the draft. Zero follows
the displayed navigation label and remains a real answer in a quantity prompt.
The main menu has one `Exit` option. `Return` on Task action takes you back
to the task list; `Return` in the edit-answer picker keeps the completed preview.
The guided interface calls a duty a **Role or responsibility**: an ongoing
responsibility such as training or maintenance. **Category** means the area
of life, while **Project** means a specific effort with an end goal. Database
field names and direct command flags such as `--duty` are unchanged.
New reference names stay in memory with the draft: cancelling saves neither
the task/accomplishment nor its proposed duties, measures, or project. On
confirmation they are saved in the same transaction. A task changed elsewhere
while the form was open must be selected again before saving.

Successful actions return to the main menu. Export failures explicitly report
that the database change was saved; retry export without repeating the action.
The guided menu does not refresh exports before inspection. EOF or Ctrl+C
exits and discards unconfirmed answers. Existing direct commands and their
prompt behavior remain available; scripts without a terminal still receive
help rather than entering the menu.

This first menu does not edit saved task titles/categories/projects, close
projects, stop series independently, or guide review/corrections; use the
existing direct commands where supported. Adding a project within a task form
records its name only. Standalone accomplishments do not acquire a project
through this menu. `None / not applicable` is one stored empty state: distinct
unknown/not-applicable answers require a later data-model change.

### Direct commands

```
python main.py                          # guided menu in a terminal
python main.py --help                   # list every command
python main.py <command> --help         # details for one

python main.py init                     # create the database (first run only)
python main.py migrate                  # upgrade an existing database

python main.py today [--days 14]         # overdue, due today, and coming up
python main.py list [-c NAME]           # every open task, with ids
python main.py cancel ID                # cancel an open task; no completion logged
python main.py reschedule ID --due DATE # change an open task's due date
python main.py reschedule ID --clear-due
python main.py projects                 # all project ids, statuses, and open counts
python main.py close-project ID         # mark done only when no open tasks remain
python main.py add "text" [-c NAME] [--due DATE] [--project ID] [--duty NAME]
python main.py add "text" -c work --due DATE --repeat monthly [--interval 1]
python main.py recurrences              # series ids, schedules, and current tasks
python main.py stop-recurrence ID       # stop generation; retain the current task
python main.py done ID [--outcome "..."] [--measure NAME] [--quantity N] [--flag]
python main.py log "Outcome" -c work [--duty NAME] [--measure NAME] [--quantity N] [--flag]
python main.py upcoming [--days 30]     # project deadlines approaching
python main.py project "name" [--deadline DATE]
python main.py measure ["NAME"]         # list measures, or register one
python main.py duty ["NAME"]            # list duties, or register one
python main.py review [--since DATE] [--until DATE] [--duty NAME]
python main.py review --check-evidence [--since DATE] [--until DATE] [--duty NAME]
python main.py completions [--since DATE] [--until DATE] [--duty NAME]
python main.py history ID               # all revisions of a completion
python main.py correct ID --reason "..." --outcome "..."
python main.py export [--out DIR]       # regenerate exports/
python main.py backup --out DIR         # create a verified database snapshot

python main.py --db PATH <command>      # run against a different database
```

Square brackets in this reference mean optional arguments; do not type them.
Bare `add` prompts for task text, category, duty when available, and due date.
With task text supplied, category still prompts if omitted, while omitted
due date and duty stay empty. Bare `project` prompts for name and deadline;
with a name supplied it does not prompt for an omitted deadline.

`done ID` prompts for outcome, measure/quantity, duty when missing, and review
flag. Supplying any of `--outcome`, `--measure`, `--quantity`, or `--flag`
disables that guided flow; `--date` or `--duty` alone does not. Prompts require
a terminal. Unattended commands must supply enough arguments to avoid them,
such as `add "Task" -c work` or `done ID --outcome "Delivered"`.

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

Measurements must be finite numbers: `log`, `done`, and `correct` reject
`NaN`, infinity, and numeric inputs that overflow to infinity. The shared
write functions enforce this too. Zero, negative values, and fractions remain
valid; choose values appropriate to the measure. Guided entry asks again
after an invalid number. Rejected quantities do not create a completion,
close a task, advance recurrence, or append a correction.

### Checking review evidence

```powershell
python main.py review --check-evidence --since 2026-01-01
python main.py review --check-evidence --duty "Operations"
```

This mode lists current completion ids with missing outcomes, duties, or
measurements, plus any non-finite quantities already in the archive. It
examines entries whose saved context counts toward review or which are
flagged, applying the same inclusive date and duty filters as review.
Superseded revisions are excluded before filtering. A duty filter excludes
entries without that duty, including unassigned entries; omit it to find
missing duties across the archive.

Missing fields are prompts to consider, not errors: some accomplishments
have no useful numeric measure or duty. The check does not judge the quality
of prose, require extra fields, or change records. It replaces the usual
totals output and succeeds even when gaps are found. Use `correct ID --reason
"..."` with the appropriate fields to append evidence while retaining originals.

Evidence checking skips export refresh so legacy invalid numbers cannot
block inspection. Corrections also skip the pre-command refresh, then
refresh exports after saving. Correct an invalid quantity to a finite value
or use `--clear-measure`; a correction cannot carry a non-finite value into
its new revision. If other invalid entries still block export, the error
states that the database change was saved; continue with the remaining
entries, then run `export`. Do not repeat an already saved correction.

These guards apply through Saga's write functions; direct SQL can bypass
them. Export validation remains a final check, including for aggregate
overflow from very large finite measurements. No existing evidence is
automatically rewritten.

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
A copy on the same drive does not protect against drive loss. The manual
`backup` command never prunes older backups.

To recover, stop Saga commands and the morning scheduled task, then copy a
successful backup to a **new local path**, such as `data/recovered.db`.
Run `python main.py --db data/recovered.db list` and
`python main.py --db data/recovered.db review` to inspect it. Keep the
original database intact until you have confirmed the recovered records.
Use `--db data/recovered.db` to work with the recovered copy. This procedure
does not replace the default database or change the morning task's path.
If a backup predates a schema upgrade, run `migrate` against the recovered
copy first, using the same `--db` option.

### Scheduled backups

The noninteractive runner creates a snapshot, restores it into a temporary
local directory, checks integrity and foreign keys, and exercises task,
completion, and review queries. Only then can retention remove older copies.
It requires the current database schema; migrate the working database first
when upgrading Saga. No third-party packages are required.

```powershell
.\.venv\Scripts\python.exe scheduled_backup.py --out "$env:OneDrive\SagaBackups" --keep 30
.\scripts\install-backup-task.ps1 -Destination "$env:OneDrive\SagaBackups" -At '06:00' -Keep 30 -WhatIf
.\scripts\install-backup-task.ps1 -Destination "$env:OneDrive\SagaBackups" -At '06:00' -Keep 30
```

Use your chosen destination if OneDrive is not configured. The first command
runs immediately; the setup script registers a daily Windows task using the
project's Python 3.14 virtual environment and absolute paths. It runs hidden
as the current user **while signed in**, including while the screen is locked.
Missed runs are eligible to run when available; this does not wake a powered-off
computer. It permits battery operation, prevents overlapping task instances,
and limits a run to one hour. An existing task is not silently replaced.
Moving the checkout or virtual environment requires recreating the task.

`--keep 30` / `-Keep 30` explicitly enables retention of the newest 30 successful
snapshots, counted by successful run order, not file modification time. Omit
that option to keep every copy. Each source path gets its own managed subfolder
and manifest; manual snapshots, migration backups, and unregistered files are
never pruned. Treat the manifest as managed state, not a file to edit. Changing
the source path starts a separate collection. An interrupted manifest update
can leave an unregistered snapshot which is preserved for manual inspection.

A failed backup or restore check does not prune earlier copies. A retention
failure reports an error but retains the new verified backup; retrying can
finish cleanup. The runner also uses `run.lock` to prevent overlapping manual
runs. Abrupt termination can leave this lock: confirm no backup process is
running before removing **only that lock file**. Never remove a lock merely
because it is old. Do not run multiple computers against one managed folder.

Results are recorded in `scheduled-backup.log` beside the runner, with two
rotated logs of up to roughly 1 MB each. A nonzero task result indicates failure.
Inspect both status and the most recent log timestamp regularly:

```powershell
Get-ScheduledTaskInfo -TaskName 'Saga Scheduled Backup'
Get-Content .\scheduled-backup.log -Tail 10
```

The restore check verifies the local snapshot every run. It does not verify
that OneDrive has uploaded the files or that a remote copy can be downloaded.
Periodically recover a downloaded copy to a separate local path using the
recovery procedure above. Keep the working database outside OneDrive.

## Try it

```
python seed_demo.py
python main.py --db data/demo.db today
python main.py --db data/demo.db review
```

`seed_demo.py` recreates `data/demo.db` from invented data through the same
write functions the CLI uses, overwriting an existing demo database. It does
not touch the default `data/saga.db`. Keep real records out of the demo file.

## Morning brief and exports

`python morning.py` prints the `today` and `upcoming` views using Saga's CLI
and default database. It logs runs to `morning.log` and waits for Enter when
a terminal is attached. The script does not install a scheduled task. To
schedule it, configure Windows Task Scheduler with the full path to the
project's Python interpreter and `morning.py`; choose the desired time and
missed-run behavior there. The repository alone does not establish whether
a scheduled task exists or has run successfully on a particular machine.

External consumers can read `brief.json` (format version 1), `brief.md`, and
`review.json` (format version 2), with `manifest.json` (format version 1)
describing the published set. These formats are separate from database
schema version 3. The bundled morning script calls the core through the CLI;
it does not consume the JSON files.

Most commands against the default database refresh exports before running
when the manifest is missing or outdated, files are missing or damaged,
hashes disagree, or JSON metadata has an unexpected type or version.
`init`, `migrate`, `backup`, `export`, `correct`, `review --check-evidence`,
and help do not run that freshness check;
`export` explicitly writes the files itself. Task/completion changes refresh
exports afterward, but registering a measure or duty does not. Commands
using an alternate database do not automatically refresh exports.

No command running means no automatic refresh. Validation checks the files,
not whether someone changed the source database outside Saga. Consumers
needing a current snapshot should arrange an explicit export run.

Publication reads both reports from one SQLite snapshot, serializes all
outputs, and writes and flushes temporary files in the destination directory.
It then atomically replaces each named file and publishes the manifest last.
Non-finite numbers such as infinity are rejected before any file is replaced.
An export error after a database change reports that the change was saved;
retry the export rather than repeating the database operation.

Individual replacements are atomic; the set of files is not a filesystem
transaction. A failure or overlapping export runs can leave a mixed set,
which the manifest detects. Consumers needing consistency across files should
read the manifest, read each file into memory, verify every SHA-256 hash in
its `files` mapping, and reread the manifest to ensure it has not changed.
Use those verified bytes; reopening paths afterward starts a new read.
Retry if validation fails. The manifest also carries a generation id, date,
and common generation timestamp. It is a consistency check, not an authenticity
signature. Synchronization software may deliver files out of order, so remote
consumers should also validate before using the set.

Ordinary failures clean up this run's temporary files. Abrupt process
termination can leave unused `.tmp` files, which readers ignore. Existing
exports without a manifest are regenerated on the next freshness check.

## Design notes

**Completions are a separate table, not columns on tasks.**
Each recurring occurrence is a separate task row. Completing it records an
accomplishment; correcting that accomplishment appends another revision.
Tasks are mutable working state, while completion rows preserve evidence
and their historical context. Reports count the latest revision only.

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
locking. Copying during a write or synchronizing sidecar files out of order
can produce inconsistent copies. Keep the working database on local disk;
sync generated exports or closed backup snapshots instead.

**External consumers use exports; Saga's own interfaces use the core.**
External consumers read `exports/brief.json`, which carries a version
field. The schema can change without breaking anything downstream, and the
consumer never needs to know SQL.

**Freshness is based on the brief's date, not file modification time.**
A date passing is not a write. The command-time refresh described above
brings quiet days forward when Saga runs. Sync clients can rewrite file
modification times, so those are not used as the freshness signal.

## Data handling

Use Saga only for the operator's own nonsensitive workload and accomplishments.
Do not enter other people's identifiers, patient information, or protected
organizational data. The schema has no dedicated person fields, but titles,
descriptions, outcomes, and correction reasons are free text. Keeping sensitive
data out depends on what the operator enters; the schema cannot enforce it.

`data/` and `exports/` are excluded from version control. That helps prevent
accidental commits but is not a guarantee that sensitive text cannot be
committed elsewhere. Demo data used in examples and tests is invented.

## Development checks

Use a virtual environment created with Python 3.14 and install Ruff as a
development tool. On Windows, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

GitHub Actions runs the full test suite and Ruff on Windows with the latest
available Python 3.14 patch for pushes and pull requests. The stable check
name is `Tests and lint`. Dependabot checks weekly for updates to the pinned
development tools and GitHub Actions; update pull requests use the same CI.
Updates are reviewed and merged manually.

Merge enforcement requires a separate GitHub branch-protection setting;
the workflow file alone does not block merging. After the first successful
GitHub run, require `Tests and lint` on `main` and require branches to be
up to date before merging. Keep that check name stable when editing CI.
Dependabot begins scheduled updates once its configuration is on the
default branch.

Tests use invented records in temporary or in-memory databases. They cover
backup recovery, capture, completion rollback, date boundaries, review
filters, immutable snapshots, correction chains, migration preservation,
and exports. The version-zero schema in
`tests/fixtures/schema_v0.sql` is frozen from commit `7ff0f99^`; keep it
independent of the current schema. `tests/fixtures/schema_v1.sql` preserves
the schema before archive history was introduced, and `schema_v2.sql` preserves
the schema before recurrence. Migration tests also inject failures to
verify rollback of schema, records, and version, and successful retry.
Export tests cover snapshot consistency, staging/replacement failures,
manifest validation, invalid numbers, and successful retry. File replacement
is atomic individually; multi-file consistency requires manifest validation.

Migrations commit one at a time, after checking their resulting schema
version. A failing migration rolls back completely; earlier successful
migrations remain applied. Each migration run retains a uniquely named
pre-migration backup. Migration SQL must leave transaction control to the
runner: do not include `BEGIN`, `COMMIT`, or `ROLLBACK` statements.

## License

MIT. See [LICENSE](LICENSE).
