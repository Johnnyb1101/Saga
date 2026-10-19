# Saga

A personal operations database and morning-brief generator. SQLite and a
Python CLI with no third-party runtime dependencies, plus an optional Windows
desktop interface powered by PySide6.

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

The guided terminal interface covers daily capture, task selection, measurement
follow-ups, review browsing, and accomplishment corrections. Export output
can be configured with `SAGA_EXPORT_DIR` or an explicit `export --out` path.
Guided project and recurring-series management is also available.
An optional Windows desktop preview provides Today and manual accomplishment
capture through the same core functions. Assistant integration is planned.

## Requirements

Python 3.15 with SQLite 3.37 or later. Python 3.15 is the sole supported
runtime for the CLI, desktop preview, and Windows build. `sqlite3` is in the
standard library; Ruff is an optional development tool, not a runtime
requirement. The desktop preview additionally requires the packages in
`requirements-desktop.txt`.

The SQLite floor is real: every table is declared `STRICT`, which arrived
in 3.37. Check yours with:

    python -c "import sqlite3; print(sqlite3.sqlite_version)"

## Setup and upgrades

From the repository directory on Windows, create an environment using a
complete Python 3.15 installation:

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.sqlite_version)"
```

If the launcher is unavailable or points to an incomplete installation, use
the full path to a working Python 3.15 executable for the first command.
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
schema is version 8. Migration saves a local backup before pending changes
and commits each migration only after checking its version. That local copy
does not replace a separate-drive backup. To use another database, put
`--db PATH` before the command. When exporting an alternate database, also
use `--out DIR` to avoid overwriting the configured/default exports.

### PowerShell command

To use `saga` as a command in the current PowerShell session, dot-source the
helper with the full path to this checkout:

```powershell
. 'C:\path\to\Saga\scripts\saga-profile.ps1'
```

Then run `saga` for the guided menu, or pass the same arguments you would pass
to `main.py`, such as `saga today` or `saga add "Review packages"`. The helper
uses this checkout's `.venv`, preserves your working directory (including how
relative `--db` and `--out` paths resolve), and exposes Saga's exit code through
`$LASTEXITCODE`. Missing interpreter or entry-point files produce an error.

To load it for future sessions, create `$PROFILE` if it does not exist, then
add that same dot-sourcing line manually to the profile, preserving its contents:

```powershell
if (!(Test-Path -LiteralPath $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force }
```

Moving the checkout requires updating the path in the profile and reloading the
profile in the current shell. The helper itself does not edit your profile.

### Windows desktop preview

Install the optional desktop requirements in the existing Python 3.15 virtual
environment, then launch from the checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop.py
```

The desktop shows overdue, due-today, upcoming, later, and undated tasks, plus
due measurement follow-ups. Click any open task card in **Today** to view its
details and **Mark complete**, including overdue, future, and undated tasks.
The completion preserves the task's category, role, title, project and due date.
Review the result before confirming; Today refreshes after completion.

Choose **Change due date** in an open task's details to set a different date
or uncheck **Set a due date** to remove it. Preview the old and new dates before
saving. For recurring tasks, this changes only the current occurrence; future
dates keep the original schedule. Today refreshes after you return from saving.

In **Add Task**, check **Repeat this task** to choose Daily, Weekly,
**Every two weeks**, Monthly, or **Twice a month**. Weekly schedules let you
choose a weekday. Monthly schedules let you choose a date from 1 to 31;
twice-monthly schedules use two different dates, such as the 1st and 15th.
Daily, weekly, and monthly schedules also let you choose how often they repeat.
Choose a start date: the preview shows the first matching due date on or after
that date. Short months use the last day, then return to your chosen date.
If both monthly dates fall on the same last day, only one occurrence is created.
Completing an occurrence creates the next task on the original schedule.
Today cards show the schedule. Existing schedules keep their original dates.
Optionally check **End on** and choose the last allowed due date. Occurrences
due on that date are included; no later occurrences are created. The end date
must be on or after the first scheduled due date. Existing open tasks can still
be completed after the cutoff. Leaving **End on** unchecked keeps the schedule
running until you stop it. Existing schedules have no end date after upgrading.
To end it, open the task and choose **Stop repeating permanently**. The confirmation
explains that the current task is cancelled, no future tasks will be created,
and previous accomplishments remain saved. **Keep task** leaves it unchanged.

Existing archives need the schema 8 upgrade before this version can open them.
Run `python main.py --db "PATH_TO_YOUR_ARCHIVE" migrate` using Python 3.15
from this checkout. Migration creates a backup before updating the archive.
The desktop reports older archives rather than upgrading them automatically.

**Add Task** uses one form for future work and work already finished. Enter the
task, category and optional role once. At the bottom, check **I’ve already
finished this** to expand completion details in place: the completion date,
optional result details, numbers and review flag. The task text becomes the
completion description; any result details are added to it. Checking or
unchecking keeps all your entries. Scheduling controls are inactive while
recording completed work. Preview and save use the same buttons in either case.

**History & Reports** shows accomplishments and totals for a chosen date range.
Use **From** and **Through** dates (both dates are included), or choose **This month**,
**This year**, or **All time**. Reports use the date the work was completed,
including backdated entries, and count only the latest corrected version of
each accomplishment. Optional category and role filters include historical
categories and roles. Totals show completed work, review flags, measurements
grouped by what was counted, and completions by category. Open an accomplishment
to read its full details. Reports are read-only and do not change your archive.
Both completion routes ask **Can you put a number to this?** Choose **Yes** to
enter an amount and what you counted, **No number needed**, or **I’ll add a
number later** with a reminder date. Relevant fields appear only when needed.
New roles, counting names and reminders save atomically with the entry.
Task editing, cancellation and reminder resolution remain available through
the CLI.

On first launch, **Get started** opens Saga's default archive in the Windows
local application-data directory, creating it when needed. An existing default
archive is opened and validated; it is never overwritten. Choose **Open existing
Saga archive** to use another archive, such as `data/saga.db` for the same
records as the CLI. The first screen has no folder picker. Archive location,
export settings, and controls to create or change archives are under the
collapsed **Settings → Advanced** section. The desktop does not move or copy
archives, and the CLI's default remains unchanged. Desktop settings remember
the selected archive and its export folder locally. Older/newer schema versions
are reported rather than migrated automatically. Keep the working database
outside synced folders.

Exports are configured per archive in the desktop. New archives use an
archive-named folder beside the database, such as `saga-exports` for `saga.db`.
For existing archives, select an export folder to enable automatic exports.
The desktop passes that destination
explicitly, so `SAGA_EXPORT_DIR` does not override desktop settings. Use a
different folder for a demo or recovered archive. If export fails after saving,
the accomplishment is already recorded: use **Retry export** rather than
recording it again. Exports can also be disabled.

### Optional OneDrive backups in the desktop

Desktop **Settings → Back up to OneDrive** is off by default. When enabled, Saga
suggests `SagaBackups` under an existing OneDrive folder it detects, or you can
choose a backup folder. Saga keeps the working archive local and saves verified
snapshots to that folder while open: at startup, after saved entries or category
changes settle, and every 15 minutes. Saga keeps the latest 30 successful
backups for each archive. This setting does not create
an operating-system schedule. **Back up now** starts a manual snapshot. If a
backup fails, Saga shows the failure and keeps the saved entries; OneDrive
uploads files independently, so Saga cannot confirm cloud sync. These snapshots
are file backups, not a live multi-device database.

The desktop makes no AI calls;
provider setup and assistant chat are later milestones. PySide6 is optional for
CLI use. Python 3.15 is the sole supported desktop runtime. Passing the desktop
CI tests does not verify installer packaging. Distribution must include the
appropriate Qt license notices.

### Standalone Windows installation

The Windows build includes Python and Qt, so the installed app can run without
the checkout or a separately installed Python. Build it on Windows with Python
3.15. These build tools are optional and separate from the CLI environment:

```powershell
.\.venv\Scripts\python.exe -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt -r requirements-desktop.txt
.\scripts\build-windows.ps1
.\scripts\install-windows.ps1 -Launch
```

The build produces `dist\Saga\Saga.exe` and its supporting files. Keep the whole
`Saga` folder together. The install script copies it into a new version folder
under `%LOCALAPPDATA%\Programs\Saga`, checks that copy, and creates desktop and
Start menu shortcuts. It leaves previous versions available. This is a local,
unsigned installation; publishing and signing an installer are separate work.

Both scripts run `Saga.exe --smoke-test REPORT_PATH` from outside the checkout.
This check uses invented records in a temporary archive and writes a JSON
report. It checks bundled resources, saving, exports, settings, and the window.
The install script only updates shortcuts after the installed copy passes.
If the existing **Saga Morning Brief** scheduled task is present, installation
also points its launch action at the new app. Its time, missed-run behavior,
account, and other settings are preserved; installation does not create a task
or change the separate backup schedule. If updating the morning task fails,
the installer reports the error and attempts to restore its previous action
and both shortcuts. Any restoration failures are reported explicitly.

The installed app uses the same saved archive selection and settings as the
desktop preview. Installation does not move your records: if your selected
archive is still inside the checkout, keep that archive and its export folder.
The app itself no longer needs the source files or `.venv` to run.

In **Settings**, choose **System**, **Light**, or **Dark** appearance and a
**Teal**, **Blue**, **Magenta**, or **Purple** accent. Changes appear immediately
and are saved locally without changing your archive selection or unsaved drafts.
System appearance follows Windows; the accent remains your chosen color.

**Add Task** uses **Choose category** with **Create category** and
**Manage categories**. Create your own categories and choose whether their new
accomplishments count toward reviews. Unused categories can be deleted;
categories linked to records can be archived instead. Archiving removes a
category from new-entry choices without changing existing tasks or history.
Archived categories can be restored. Category changes save immediately;
unsaved task and accomplishment drafts remain separate.

Existing archives need the version 6 migration before this version can open
them. Close older Saga windows, run `python main.py --db "path/to/archive.db" migrate`,
then reopen Saga. Migration keeps a backup beside the archive. New archives
already include category management.

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

Manage projects can close projects, and Manage recurring series can stop
future occurrences while retaining the current task. Adding a project within a task form
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
python main.py duty ["NAME" --category NAME] # list roles, or register in a category
python main.py retire-role "NAME" --category NAME
python main.py activate-role "NAME" --category NAME
python main.py review [--since DATE] [--until DATE] [--category NAME] [--duty NAME | --unassigned]
python main.py review --check-evidence [--since DATE] [--until DATE] [--category NAME] [--duty NAME | --unassigned]
python main.py completions [--since DATE] [--until DATE] [--category NAME] [--duty NAME | --unassigned]
python main.py history ID               # all revisions of a completion
python main.py correct ID --reason "..." --outcome "..."
python main.py export [--out DIR]       # regenerate exports at the selected destination
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

### Narrative evidence and measurement reminders

A useful accomplishment does not need a numeric result. The guided menu
requires an explicit measurement choice:

- **Measured:** choose or add a unit and enter a finite number; zero is valid.
- **Not applicable:** there is no useful numeric measure. Describe the result
  and why it mattered in the required outcome.
- **Not known yet:** the result is expected later. Enter a reminder date,
  today or later; the accomplishment and reminder save together.

**Unspecified** identifies older entries or direct commands that omitted a
measurement decision. Migration 5 labels existing numbers Measured and empty
measurements Unspecified, without guessing intent or creating reminders.
It preserves all previously recorded evidence and correction history.

Open **Measurement follow-ups** from the main menu to select a reminder by
row number. Record the number, choose Not applicable, or set a new reminder
date. Resolving appends an evidence correction and closes the reminder in one
transaction; it never counts another accomplishment. Due and overdue reminders
appear at menu startup, in `today`, and in the morning brief, and remain visible
until resolved or rescheduled. These are in-app/brief reminders; Saga does not
send background push notifications. Future reminders remain in the follow-up list.

The reminder date is separate from the **actual completion date**. When closing
a task, enter when the work actually finished, even if recording it later.
Reports compare that date against the saved task deadline: early or on-time
work remains on time even when entered after the deadline. Resolving a reminder
preserves the actual completion date by default and offers an explicit date
correction. Entry time is retained separately; a late measurement does not
make the work late. Review periods also use the actual completion date.

Direct-command equivalents (substitute your ids and dates):

```powershell
python main.py log "Simplified the handoff process" -c work --measurement-status not_applicable
python main.py done 12 --outcome "Completed the audit" --date 2026-09-01 --measurement-status unknown --remind-on 2026-10-01
python main.py followups
python main.py reschedule-followup 3 --date 2026-10-08
python main.py correct 18 --reason "Result confirmed" --measure "items" --quantity 0
python main.py correct 19 --reason "No useful numeric measure" --measurement-status not_applicable --clear-measure
```

Register a unit with `measure "items"` before using it in direct commands.
`correct` takes the latest **completion id**, while `reschedule-followup`
takes the **reminder id** shown by `followups`. The guided menu handles those
ids for you. Direct `log`/`done` commands infer Measured from a unit and number;
omitting both without a status remains Unspecified for compatibility.
Run `python main.py migrate` and then `python main.py export` to upgrade an
existing database before using these features or its scheduled backup runner.

### Reviewing accomplishments

Choose **Review accomplishments** in the guided menu, then explicitly select
all recorded dates or an inclusive date range, a category or all categories,
and a historical role, all roles, or **No role assigned**. Role choices come
from current completion records in the selected period/category, so retired
roles remain available. Back revisits the previous selector.

The review menu offers the summary, searchable eight-row accomplishment pages,
evidence gaps, and filter changes. Select a row to inspect its saved context,
measurement state, actual completion date, and entry time, or view every
revision in its history. Returning from details retains the review filters.
Review inspection does not change records or refresh exports. Select
**Correct accomplishment** from a record's actions to append corrected evidence.
Direct `correct` commands remain available; use Measurement follow-ups for
reminder-only rescheduling.

Direct commands accept the same filters:

```powershell
python main.py review --since 2026-01-01 --until 2026-12-31 --category work --duty "Training"
python main.py completions --category work --unassigned
python main.py review --check-evidence --category work
```

Dates must be valid `YYYY-MM-DD` values; the start cannot follow the end.
Omitted boundaries impose no date limit. `--duty` alone continues to span
categories; combine it with `--category` to narrow a same-named role.
`--unassigned` selects records whose role is empty and cannot be combined
with `--duty`.

**By category / role** groups each current accomplishment once using its
saved category and role. Same-named roles in different categories are separate;
retired roles and No role assigned remain visible. These groups partition the
headline totals, rather than adding more accomplishments to category totals.
Groups with no matching completions are omitted; the category summary still
includes zero-count categories within the selected scope.

Each group includes completion and flagged counts, all four measurement
states, totals per measurement unit, and the on-time numerator and denominator.
Only records with a saved deadline enter that denominator. Explicit Not
applicable remains valid narrative evidence, and resolving a follow-up or
appending a correction never adds a second accomplishment. Superseded revisions
are excluded before filters and grouping. Overall totals continue to include
all selected completions; evidence checks inspect review-counting or flagged
entries only.

### Checking review evidence

```powershell
python main.py review --check-evidence --since 2026-01-01
python main.py review --check-evidence --duty "Operations"
```

This mode lists current completion ids with missing outcomes or duties,
unspecified measurements, pending measurement follow-ups, and any non-finite
quantities already in the archive. Explicit Not applicable is not a
measurement gap. It
examines entries whose saved context counts toward review or which are
flagged, applying the same inclusive date, category, and role filters as review.
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

## Category-linked roles and responsibilities

Choose a category first, then choose one of its active roles. A role name can
appear under more than one category; each category has its own availability
status. New registrations reject duplicates within that category, ignoring
capitalization and repeated or surrounding whitespace (including Unicode
case folding). Retired names must be reactivated, not registered again.

The guided main menu now has **Manage roles**. Choose a category to add,
search, retire, or activate its roles. Long lists use search and pagination.
Editing a draft's category requires explicitly choosing its role again.
Choose None when no ongoing responsibility applies. Direct commands are:

```powershell
python main.py duty "Training" --category work
python main.py duty --category work
python main.py retire-role "Training" --category work
python main.py activate-role "Training" --category work
```

Retirement hides a role from new assignments but preserves existing tasks and
all archived evidence. Existing tasks can still be completed with that role;
corrections can retain an unchanged historical category/role pair. Assigning
a different pair requires an active role in the selected category. Stop any
active recurring series using a role before retiring it. This prevents a
future occurrence from acquiring a retired role. Other categories using the
same name are unaffected.

Schema version 4 adds category-role links without rewriting completion rows,
original duty names, or correction chains. Migration derives links from all
existing tasks, completion revisions, and recurring series. Unused legacy
roles stay unassigned: use Manage roles to choose their category explicitly.
Normalized-name collisions are marked `needs_review` and excluded from new
assignments. Choose which spelling to activate and retire the others; nothing
is silently merged. A conflicting active spelling must be retired before
another is activated. A blank legacy name cannot be activated. Active series
with a role needing review require resolution before another occurrence can
be created, or can be stopped first. Historical queries still find every name.

The new category checks apply through shared write functions, including direct
commands; direct SQL bypasses application validation. Historical review filters
such as `--duty "Training"` still span categories and include retired roles.
Category/role report breakdowns use that saved context. Role registration
itself needs no export format change; the review aggregates use format 4.
Run `migrate` and then `export` before using upgraded code;
the installed backup runner also requires the current database version.

## Task and project maintenance

Choose **Manage projects** to create or browse projects. Creation asks for a
name and explicit choices for optional description, start date, and deadline,
then offers Save, Edit answers, or Discard draft. Back revisits a question;
`:discard`, EOF, or Ctrl+C discards unconfirmed answers. Dates use `YYYY-MM-DD`;
past dates are allowed. Projects are created active, and duplicate names remain
allowed; visible ids distinguish them.

Project browsing offers search and eight-row pages, including on-hold and
closed projects. Lists show status, deadline, and open-task count. Select a row
to inspect its description/dates, view its associated open tasks, or close it.
Closure requires explicit confirmation and no open tasks; it marks an active
or on-hold project done without changing tasks or archive records. Resolve open
tasks through the existing task workflows first. Project editing, reopening,
and new lifecycle actions are not included.

Management inspection does not refresh exports. Successful changes refresh
exports for the default database and return to the management menu/list. If
export fails after saving, retry export without repeating the saved action.
Closure and series stopping recheck the selected record and its open tasks
under a write lock; if either changed after inspection, select it again.

To edit an open task, launch the guided menu, choose **Browse tasks / today**,
select its row, then choose **Edit task**. You can keep or change its title,
category, role/responsibility, and project. The preview shows before/after
values and marks unchanged fields. Choose Save, Edit answers, or Discard draft.
New roles and projects are committed together with the task edit, only after
confirmation. Blank titles, incompatible roles, closed projects as new
assignments, and edits to completed/cancelled tasks are rejected. An unchanged
form reports that there is nothing to save.

Changing category requires an explicit compatible role or None. An existing
retired role can be kept when its category is unchanged, but cannot be newly
assigned. If another command changes the task while the form is open, discard
the draft and select the task again. Existing archived accomplishments and
correction history are never rewritten by task editing.

For a recurring task, these edits affect **this occurrence only**. Future
occurrences retain the series title, category, role, and project. The schedule
is unchanged; use Reschedule to change this occurrence's deadline. Editing
the recurring series itself remains separate future work.

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

Choose **Manage recurring series** to search and browse schedules by numbered
rows. Lists show status, schedule, category, role, project, and current task;
details also show the anchor date and the current task's title and due date.
Stopped series remain visible, including those with no open occurrence.
Confirm **Stop future occurrences** to stop an active series while leaving
its current task open. Completing that task afterward creates no successor.
Returning from confirmation changes nothing. Series editing/restarting remains
deferred; create a new repeating task for a different schedule.

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

Existing databases require `python main.py migrate` for schema version 4,
followed by `python main.py export`. Existing tasks remain one-off tasks;
no completion history is changed. Brief JSON task objects gain nullable
`recurrence_id` and `occurrence_index` fields; existing fields remain intact.

## Historical context and corrections

In the guided menu, open **Review accomplishments**, browse accomplishments
or evidence gaps, select a row, and choose **Correct accomplishment**. Select
only the fields you want to change; other evidence stays as recorded. Outcome,
category, role, actual completion date, measurement, and review flag offer
explicit Keep/Change choices. **Historical context** optionally corrects the
saved task title, deadline, project name, or review eligibility. These are
archive snapshots: changing them does not edit live tasks/projects or create
a project. Optional saved labels and deadlines can be explicitly cleared.

The preview shows every before/after value, including unchanged evidence.
Changing category requires choosing a compatible role or None and previews
that category's current review eligibility. Historical context can explicitly
override that eligibility. An unchanged historical category/role pair can
retain a retired role; a new assignment requires an active role. Proposed new
roles and measurement units remain in the draft until Save.

**Save correction** requires a nonblank reason and a final Save/Edit answers/
Discard draft choice. Back returns to the field picker; within Historical
context it returns to that section or the correction picker. `:discard`, EOF,
and Ctrl+C discard unconfirmed changes. Unchanged evidence saves nothing.
Corrected evidence requires a nonblank outcome and finite measurements.

Keeping the actual completion date preserves its exact timestamp. Keeping an
unknown measurement preserves its reminder, even when overdue. Resolving it
as Measured or Not applicable closes the reminder; choosing Not known yet
requires a date today or later. Reminder-only date changes belong in
Measurement follow-ups. The completion revision, new roles/units, and reminder
changes save together or roll back together. If the revision or reminder
changed while the draft was open, discard and select the record again.

After saving, the browser reapplies your review filters to current revisions.
A corrected record can therefore leave the selected period, category, role,
or evidence-gap list. Inspection skips export refresh; a saved correction
refreshes exports for the default database. An export failure explicitly says
the correction was saved: retry export, not the correction. Database schema 5,
brief format 2, and review format 4 are unchanged by this workflow.

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
Review JSON format 4 adds `by_category_role` to the existing report fields.
Each group has `category`, nullable `duty`, `completions`, `review_counting`,
`flagged`, `with_a_number`, `measurement_states`, `evaluated`, `on_time`, `pct`,
and `measures` (separate unit totals and occasion counts). `pct` is null when
no deadlines can be evaluated. Existing fields retain their meaning, including
measurement-state counts, flagged-item status, snapshot provenance, and
predecessor ids. Totals count current revisions only. Brief JSON remains
format 2, and the database remains schema 5; this report change requires no
migration. Regenerate exports with `python main.py export` when ready to
publish format 4, and update consumers that enforce the review format version.

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

### CLI scheduled backups

This is a separate option for making backups when Saga is closed. It uses the
command line and a Windows scheduled task; enabling desktop OneDrive backups
does not install or change this task.

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
project's Python 3.15 virtual environment and absolute paths. It runs hidden
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

### Three-year stress dataset

For a larger, separate archive of invented records, run with Python 3.15:

```powershell
.\.venv\Scripts\python.exe seed_stress.py --db data/stress-demo.db --records 10000 --seed 42
.\.venv\Scripts\python.exe stress_report.py --db data/stress-demo.db
```

The generator creates roughly three years of varied accomplishments, numbers,
categories, roles, projects, recurring tasks, cancellations and corrections.
It also writes `data/stress-demo.expected.json`, an independent ledger built
from the planned inputs. A fixed `--seed` and `--end-date YYYY-MM-DD` reproduce
the same generated completion evidence; recording timestamps and future
measurement reminders reflect the run date.
Change `--records` to adjust the workload. Both tools refuse to overwrite their
output files: choose a new database name for another run.

The checker compares actual History & Reports results with independently
calculated counts, separate measurement totals and individual records. It checks
yearly, monthly, category, role and date-boundary scopes, then writes
`data/stress-demo.report.json` and the readable `data/stress-demo.report.txt`.
The text report includes activity by year and month, category and role counts,
measurement totals, and local service-query timings. Query timings exclude
desktop rendering and do not predict performance on another machine.

Generated files remain outside version control. These commands do not modify
your real archive, exports, desktop selection or scheduled tasks. You can choose
the invented archive through **Settings → Advanced** to browse it manually;
remember to select your real archive again afterward.

### Readable report and agent evidence package

In **History & Reports**, select your dates, category and role, then choose
**Save readable report**. Choose a folder; Saga creates a new report subfolder
and offers to open the HTML report. It uses the currently selected controls,
even if you have not pressed Show report. Both end dates are included.

All final counts and totals are calculated and checked by Saga code, without AI.

**Prepare for** offers **General report**, **EPB**, and **CV**. General report
keeps the compact totals. EPB and CV first ask you to choose accomplishments:
EPB prepares saved action/result material with missing impact marked explicitly;
CV groups selected accomplishment bullets by saved role/category. These are
preparation drafts, not an official EPB form or a complete employment history.
There are no assumed official section or character limits.

The verified totals cover the full date range and filters, while the draft uses
only your selected accomplishments. Saved text is source wording, not proof of
any numbers it happens to mention. Only separately recorded quantities and
Saga-calculated totals are verified numerical facts. No missing impact,
qualification, employment dates, or measurements are invented.

EPB/CV packages include `preparation.json` with selected source records and
Saga-generated per-record measurement facts. Format-specific agent instructions
are included, but no AI service is connected. You can use the preparation draft
without AI. Optional agent wording stays a draft and must reference Saga facts
for numbers.

CLI example (repeat `--record-id` for each selected accomplishment):

```powershell
.\.venv\Scripts\python.exe report_bundle.py --db data/stress-demo.db --out data/epb-draft --format epb --record-id 42 --record-id 43
```

Use `--format cv` for a CV preparation draft; General report is the default.
Selected records must belong to the chosen date range and filters. If a saved
correction changes a record while you are choosing, a stale selection is refused;
select again from the current records.


You can also generate an HTML report for a chosen archive and inclusive date
range, using a new output folder:

```powershell
.\.venv\Scripts\python.exe report_bundle.py --db data/stress-demo.db --out data/readable-report --since 2023-10-17 --until 2026-10-17
```

Open `data/readable-report/report.html` in a browser. The fixed report shows
final counts and measurement totals with a category breakdown. Role breakdowns
are tucked under **Show more**. Monthly totals and individual accomplishments
remain in the supporting evidence file rather than lengthening the report. Exact measurement names stay separate: `hours`, `hours spent`
and `hours saved` are different measures. A generic `hours` entry does not prove
how all working time was spent. Missing measurements are not treated as zero.
Optional `--category` and `--role` filters apply to the same snapshot. Omit the
date arguments for all recorded dates. No archive or app selection is changed.

The same folder contains `evidence.json` (checked figures and source records),
`agent-input.json` (compact facts for a writer), and `agent-guide.txt` (the draft
format and instructions). An agent can produce a JSON narrative using those
fact IDs. Include it in a new report folder with:

```powershell
.\.venv\Scripts\python.exe report_bundle.py --db data/stress-demo.db --out data/readable-report-with-draft --since 2023-10-17 --until 2026-10-17 --narrative data/narrative.json
```

Use the same date range and filters when including the draft. Drafts from a
different or changed evidence snapshot are rejected. The renderer supplies
numbers from verified facts; literal digits and unknown fact references in the
draft are rejected. This validates fact references, not the meaning of prose,
spelled-out numbers or claims of cause and effect. The narrative is therefore
labeled **draft for review**, and the fixed report remains the numerical
reference. These tools do not call an AI service or send records elsewhere.

`manifest.json` is written last and records file hashes to detect missing or
changed files. It is an integrity check, not a digital signature or proof of
authenticity. Existing output folders are refused. A write failure may leave a
partial new folder; it is not a complete package without a valid manifest.

## Morning brief and exports

Export destination precedence is **explicit `export --out DIR`**, then
**`SAGA_EXPORT_DIR`**, then the repository's **`exports`** directory. An unset,
empty, or whitespace-only variable uses the repository default. Relative paths
in `SAGA_EXPORT_DIR` are relative to the repository root, so changing a
scheduler's working directory does not move exports. Explicit relative `--out`
paths remain relative to the command's working directory. Path text is literal:
Saga does not expand `~` or embedded environment-variable names.
On Windows, use a full absolute path such as `C:\SagaExports` or an ordinary
relative path such as `synced-exports`; ambiguous `C:exports` and `\exports`
environment paths are rejected.

The repository ignores the `synced-exports/` example directory. Before using
any other custom destination inside the checkout, add its directory to
`.gitignore`; choosing a destination outside the checkout avoids that concern.

For example, choose a destination and set it for the current PowerShell session:

```powershell
$env:SAGA_EXPORT_DIR = 'D:\SagaExports'
.\.venv\Scripts\python.exe main.py export
```

To use OneDrive, assign an already-expanded path such as
`$env:SAGA_EXPORT_DIR = Join-Path $env:OneDrive 'SagaExports'` when OneDrive is
configured. Keep the working database on local disk; only exports belong in
the synced destination. The setting is read when exports are checked or
written, and applies to direct commands, guided post-save exports, and the
morning script's normal freshness checks. Scheduled processes must receive
the setting in their own environment; setting it in one terminal does not
configure an existing scheduler process. Saga does not change your environment
or scheduler configuration.

An explicit `--out` overrides the environment for that export only. Changing
the destination creates a new export set there; it does not move or delete
older exports. No fallback destination is used when a configured path fails.
Post-save failures still report that the database change was saved. Correct
the destination and retry export rather than repeating the database action.
Backup destinations remain controlled by their separate `--out` option.

Alternate databases still do not automatically refresh exports. An explicit
export from any database uses the same destination precedence; supply a
separate `--out` to keep a demo or recovered database's exports apart:

```powershell
.\.venv\Scripts\python.exe main.py --db data/demo.db export --out demo-exports
```

`python morning.py` prints the `today` and `upcoming` views using Saga's CLI
and default database. It logs runs to `morning.log` and waits for Enter when
a terminal is attached. The script does not install a scheduled task. To
schedule it, configure Windows Task Scheduler with the full path to the
project's Python interpreter and `morning.py`; choose the desired time and
missed-run behavior there. The repository alone does not establish whether
a scheduled task exists or has run successfully on a particular machine.

External consumers can read `brief.json` (format version 2), `brief.md`, and
`review.json` (format version 4), with `manifest.json` (format version 1)
describing the published set. These formats are separate from database
schema version 6. The bundled morning script calls the core through the CLI;
it does not consume the JSON files.

Most commands against the default database refresh exports before running
when the manifest is missing or outdated, files are missing or damaged,
hashes disagree, or JSON metadata has an unexpected type or version.
`init`, `migrate`, `backup`, `export`, `correct`, `duty`, role-status commands, `review --check-evidence`,
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
External consumers read `brief.json` in the selected export directory, which carries a version
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

`data/`, `exports/`, and the documented `synced-exports/` example directory are
excluded from version control. Other custom export directories created inside
the checkout need an ignore rule before use; an export directory outside the
checkout avoids that concern. These exclusions help prevent accidental commits
but do not guarantee that sensitive text cannot be committed elsewhere. Demo
data used in examples and tests is invented.

## Development checks

Use a virtual environment created with Python 3.15 and install Ruff as a
development tool. On Windows, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

GitHub Actions runs the full test suite and Ruff on Windows with the latest
available Python 3.15 patch for pushes and pull requests. The stable check
name is `Tests and lint`. Dependabot checks weekly for updates to the pinned
development tools and GitHub Actions; update pull requests use the same CI.
Updates are reviewed and merged manually.

The optional desktop tests run when PySide6 is installed; core-only environments
skip those tests explicitly. The desktop CI job installs the optional
requirements and runs the full suite on Python 3.15, with Qt's offscreen
platform. These checks cover interactions and rendering setup, not native
screen-reader behavior or installer packaging. Local desktop validation should
also check keyboard navigation and layout at Windows display scaling settings.

Merge enforcement requires a separate GitHub branch-protection setting;
the workflow file alone does not block merging. After the first successful
GitHub run, require `Tests and lint` on `main` and require branches to be
up to date before merging. Keep that check name stable when editing CI.
Dependabot begins scheduled updates once its configuration is on the
default branch.

Tests use invented records in temporary or in-memory databases. They cover
CLI connection cleanup on success, failure, and interrupted prompts, including
preservation of committed writes when export fails. Schema validation failures
also release their connection; successful connections remain owned by the caller.
The tests also cover
backup recovery, capture, completion rollback, date boundaries, review
filters, immutable snapshots, correction chains, migration preservation,
and exports. The version-zero schema in
`tests/fixtures/schema_v0.sql` is frozen from commit `7ff0f99^`; keep it
independent of the current schema. `tests/fixtures/schema_v1.sql` preserves
the schema before archive history was introduced, and `schema_v2.sql` preserves
the schema before recurrence. `schema_v4.sql` preserves the schema before
measurement states and follow-ups. Migration tests also inject failures to
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
