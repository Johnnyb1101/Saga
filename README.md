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

*Sample output added here once the query layer is built.*

## Status

In development. Sections marked *planned* describe the intended interface,
not working code.

- [x] Project foundation
- [ ] Schema and database layer
- [ ] Write path — tasks, projects, completions
- [ ] Read path — today, upcoming, overdue
- [ ] Analytics and review roll-up
- [ ] Command-line interface
- [ ] Export for external consumers

## Requirements

Python 3.11 or later. Nothing else — `sqlite3` ships with the standard
library.

## Usage *(planned)*

```
python main.py init                     # create the database
python main.py add "text" --due DATE --category NAME [--project ID]
python main.py project "name" --deadline DATE
python main.py done ID --outcome "..." --metric "..." [--flag]
python main.py today                    # tasks due today, plus overdue
python main.py upcoming [--days 30]     # deadlines approaching
python main.py review [--since DATE]    # flagged accomplishments, grouped
python main.py export                   # regenerate exports/
```


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

## Data handling

This system holds no personally identifiable information about anyone other
than its operator, and no protected or sensitive organizational data.

That constraint is structural rather than procedural: the schema has no
fields for personal identifiers, so there is nowhere for that data to go
even by accident. Task text describes the operator's own work.

`data/` and `exports/` are excluded from version control. No real entry has
ever been committed to this repository. Demo data used in examples and
tests is invented.

## License

MIT. See [LICENSE](LICENSE).