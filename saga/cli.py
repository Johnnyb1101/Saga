"""Command line interface. Parsing and presentation only.

Queries live in reads.py and analytics.py; writes live in writes.py. This
module turns arguments into calls and rows into text, and nothing else.
"""

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path
from saga import analytics, db, export, reads, writes

def days_between(iso_date):
    """Whole days from `iso_date` until today. Positive means in the past."""
    return (dt.date.today() - dt.date.fromisoformat(iso_date)).days

def print_tasks(rows, mode=None):
    """One line per task: id, category, an optional date column, title."""
    for row in rows:
        extra = ""
        if row["due_date"] and mode == "late":
            extra = f"{days_between(row['due_date']):>3}d late  "
        elif row["due_date"] and mode == "soon":
            extra = f"{-days_between(row['due_date']):>3}d       "
        elif mode == "date":
            extra = f"{row['due_date'] or '':<12}"
        print(f"  {row['id']:>4}  {row['category']:<9}{extra}{row['title']}")

CONSTRAINT_HELP = {
    "date(": "dates must be YYYY-MM-DD with zero padding, e.g. 2026-09-04",
    "datetime(": "timestamps must be YYYY-MM-DD HH:MM:SS",
    "FOREIGN KEY": "no such category, project, or measure",
    "(measure is NULL)": "a measure and a quantity must be given together",
    "measures.name": "that measure is already registered",
}

def explain(exc):
    """Turn a constraint error into something a person can act on."""
    text = str(exc)
    for marker, hint in CONSTRAINT_HELP.items():
        if marker in text:
            return f"{hint}  ({text})"
    return text

def require_terminal():
    """Refuse to prompt when there is nothing attached to answer."""
    if not sys.stdin.isatty():
        raise ValueError("missing arguments and no terminal to prompt on; "
                         "supply them as flags instead")

def ask(question, default=None):
    """Prompt for a value. Blank input returns the default."""
    require_terminal()
    suffix = f" [{default}]" if default else ""
    return input(f"{question}{suffix}: ").strip() or default

def ask_choice(question, options):
    """Prompt for one of `options`, chosen by number."""
    require_terminal()
    print()
    for number, option in enumerate(options, start=1):
        print(f"  [{number}] {option}")
    while True:
        raw = input(f"{question}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  Enter a number from the list.")

def cmd_add(args):
    con = db.connect(args.db)
    guided = args.text is None

    text = args.text or ask("Task")
    category = args.category
    if category is None:
        category = ask_choice("Category",
                              [row["name"] for row in reads.categories(con)])
    due = args.due
    if due is None and guided:
        due = ask("Due date (YYYY-MM-DD, blank for none)")

    task_id = writes.add_task(con, text, category,
                              project_id=args.project, due_date=due)
    print(f"Added task {task_id}: {text}")
    maybe_export(args, con)

def cmd_project(args):
    con = db.connect(args.db)
    guided = args.name is None

    name = args.name or ask("Project name")
    deadline = args.deadline
    if deadline is None and guided:
        deadline = ask("Deadline (YYYY-MM-DD, blank for none)")

    project_id = writes.add_project(con, name, description=args.description,
                                    start_date=args.start, deadline=deadline)
    print(f"Added project {project_id}: {name}")
    maybe_export(args, con)

def ask_yes_no(question, default=False):
    """Prompt for yes or no. Blank input returns the default."""
    answer = ask(f"{question} ({'Y/n' if default else 'y/N'})")
    if answer is None:
        return default
    return answer.lower().startswith("y")

def ask_measure(con):
    """Prompt for a measure and quantity. Returns (measure, quantity)."""
    names = [row["name"] for row in reads.measures(con)]
    choice = ask_choice("Measure", names + ["(none)", "(register a new one)"])

    if choice == "(none)":
        return None, None
    if choice == "(register a new one)":
        choice = ask("New measure name")
        if choice is None:
            return None, None
        writes.add_measure(con, choice)

    while True:
        raw = ask("Quantity (blank to skip the measure)")
        if raw is None:
            return None, None
        try:
            return choice, float(raw)
        except ValueError:
            print("  Enter a number.")

def cmd_done(args):
    con = db.connect(args.db)
    task = reads.get_task(con, args.task_id)
    if task is None:
        raise ValueError(f"No task with id {args.task_id}.")
    if task["status"] != "open":
        raise ValueError(f"Task {args.task_id} is already {task['status']}.")

    guided = (args.outcome is None and args.measure is None
              and args.quantity is None and not args.flag)

    print(f"Task {task['id']}: {task['title']}  [{task['category']}]")

    outcome = args.outcome
    measure = args.measure
    quantity = args.quantity
    flagged = args.flag

    if guided:
        outcome = ask("Outcome")
        measure, quantity = ask_measure(con)
        flagged = ask_yes_no("Review material?")

    completion_id = writes.complete_task(
        con, task["id"], outcome=outcome, measure=measure,
        quantity=quantity, flagged=flagged)
    print(f"Logged completion {completion_id}.")
    maybe_export(args, con)

def fmt_number(value):
    """Render a quantity: no trailing .0 when whole, thousands separated."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,}"

def plural(count, word):
    """`word`, pluralised for `count`."""
    return word if count == 1 else word + "s"

def cmd_measure(args):
    con = db.connect(args.db)

    if args.name:
        writes.add_measure(con, args.name)
        print(f"Registered {args.name}.")
        return

    rows = reads.measure_usage(con)
    if not rows:
        print('No measures registered. Add one with: saga measure "NAME"')
        return

    print(f"MEASURES ({len(rows)})")
    for row in rows:
        if row["occasions"] == 0:
            print(f"  {row['name']:<34}{'-':>7}   never used")
            continue
        print(f"  {row['name']:<34}{fmt_number(row['total']):>7}"
              f"   across {row['occasions']} "
              f"{plural(row['occasions'], 'occasion')}")

def cmd_review(args):
    con = db.connect(args.db)
    since, until = args.since, args.until

    print(f"REVIEW PERIOD  {since or 'the beginning'} to {until or 'today'}")
    print()

    counts = analytics.volume(con, since, until)
    print("VOLUME")
    print(f"  {counts['completions']:>7,} completions")
    print(f"  {counts['review_counting']:>7,} in review-counting categories")
    print(f"  {counts['flagged']:>7,} flagged as review material")
    print(f"  {counts['with_a_number']:>7,} recorded a number")
    print()

    measures = analytics.measure_totals(con, since, until)
    if measures:
        print("MEASURES")
        for row in measures:
            print(f"  {row['measure']:<34}{fmt_number(row['total']):>7}"
                  f"   across {row['occasions']} "
                  f"{plural(row['occasions'], 'occasion')}")
        print()

    rates = {row["category"]: row["pct"]
             for row in analytics.on_time_rate(con, since, until)}
    print("BY CATEGORY")
    for row in analytics.completions_by_category(con, since, until):
        pct = rates.get(row["category"])
        rate = f"{pct:>5}% on time" if pct is not None else "        -"
        print(f"  {row['category']:<10}{row['completions']:>5,} completions   {rate}")
    print()

    flagged = analytics.flagged_work(con, since, until)
    if not flagged:
        print("No flagged work in this period.")
        return

    current = None
    for row in flagged:
        if row["category"] != current:
            if current is not None:
                print()
            current = row["category"]
            print(f"FLAGGED - {current}")
        detail = ""
        if row["measure"]:
            detail = f"   [{row['measure']}: {fmt_number(row['quantity'])}]"
        print(f"  {row['completed_at'][:10]}  {row['outcome']}{detail}")

def refresh_if_stale(args):
    """Bring exports/ forward when they are not from today.

    A date passing is not a write, so maybe_export never fires on a quiet
    day and the brief on disk keeps answering with yesterday's date.
    """
    if Path(args.db).resolve() != db.DB_PATH.resolve():
        return
    if export.is_stale():
        export.write_all(db.connect(args.db))

def maybe_export(args, con):
    """Regenerate exports, but only when working on the real database."""
    if Path(args.db).resolve() != db.DB_PATH.resolve():
        return
    export.write_all(con)

def cmd_export(args):
    con = db.connect(args.db)
    for path in export.write_all(con, out_dir=args.out,
                                 since=args.since, until=args.until):
        print(f"Wrote {path}")

def cmd_init(args):
    print(f"Created {db.init_db(args.db)}")

def cmd_today(args):
    con = db.connect(args.db)
    late = reads.overdue(con)
    due = reads.due_today(con)
    soon = reads.due_soon(con, days=args.days)

    if late:
        print("OVERDUE")
        print_tasks(late, mode="late")
        print()
    if due:
        print("DUE TODAY")
        print_tasks(due)
        print()
    if soon:
        print(f"COMING UP ({args.days} DAYS)")
        print_tasks(soon, mode="soon")
        print()
    if not (late or due or soon):
        print(f"Nothing due in the next {args.days} days.")

def cmd_list(args):
    con = db.connect(args.db)
    rows = reads.open_tasks(con, category=args.category)
    if not rows:
        print("No open tasks.")
        return
    print(f"OPEN TASKS ({len(rows)})")
    print_tasks(rows, mode="date")

def cmd_upcoming(args):
    con = db.connect(args.db)
    rows = reads.upcoming_deadlines(con, days=args.days)
    if not rows:
        print(f"No deadlines inside {args.days} days.")
        return
    print(f"DEADLINES WITHIN {args.days} DAYS")
    for row in rows:
        print(f"  {row['days_left']:>4}d  {row['deadline']}  "
              f"{row['name']:<38}{row['open_tasks']} open")

def build_parser():
    parser = argparse.ArgumentParser(
        prog="saga",
        description="Personal operations database.",
    )
    parser.set_defaults(refresh=True)
    parser.add_argument(
        "--db", type=Path, default=db.DB_PATH, metavar="PATH",
        help="database file to use (default: data/saga.db)",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("today", help="due today, overdue, and coming up",
                       description="Anything past due, due today, or due within "
                                   "the next few days.")
    p.add_argument("--days", type=int, default=export.SOON_DAYS, metavar="N",
                   help=f"how far ahead 'coming up' looks (default: {export.SOON_DAYS})")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("list", help="every open task, with ids",
                       description="All open tasks, soonest due first, undated last.")
    p.add_argument("-c", "--category", help="only this category")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("add", help="add a task",
                       description="Add a task. Run it bare to be prompted for everything.")
    p.add_argument("text", nargs="?", help="what the task is")
    p.add_argument("-c", "--category", help="category name")
    p.add_argument("--due", metavar="DATE", help="due date, YYYY-MM-DD")
    p.add_argument("--project", type=int, metavar="ID", help="project id")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("done", help="complete a task",
                       description="Complete a task and archive it. Run with just "
                                   "an id to be prompted for the rest.")
    p.add_argument("task_id", type=int, metavar="ID", help="task id (from `today`)")
    p.add_argument("--outcome", help="what happened")
    p.add_argument("--measure", metavar="NAME", help="a registered measure")
    p.add_argument("--quantity", type=float, metavar="N", help="how many")
    p.add_argument("--flag", action="store_true", help="mark as review material")
    p.set_defaults(func=cmd_done)

    p = sub.add_parser("upcoming", help="project deadlines approaching",
                       description="Active projects with a deadline inside the window, "
                                   "and how many of their tasks are still open.")
    p.add_argument("--days", type=int, default=export.DEADLINE_DAYS, metavar="N",
                   help=f"how far ahead to look (default: {export.DEADLINE_DAYS})")
    p.set_defaults(func=cmd_upcoming)

    p = sub.add_parser("project", help="add a project",
                       description="Add a project. Run it bare to be prompted for everything.")
    p.add_argument("name", nargs="?", help="project name")
    p.add_argument("--deadline", metavar="DATE", help="deadline, YYYY-MM-DD")
    p.add_argument("--start", metavar="DATE", help="start date, YYYY-MM-DD")
    p.add_argument("--description", help="longer description")
    p.set_defaults(func=cmd_project)

    p = sub.add_parser("measure", help="list or register measures",
                       description="Run it bare to see every registered measure "
                                   "and how often it has been used. Give it a name "
                                   "to register a new one.")
    p.add_argument("name", nargs="?", help="name of a new measure to register")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("review", help="totals and flagged work for a period",
                       description="Summarise the archive: volume, measure totals, "
                                   "per-category rates, and flagged accomplishments.")
    p.add_argument("--since", metavar="DATE", help="start of the period, YYYY-MM-DD")
    p.add_argument("--until", metavar="DATE", help="end of the period, YYYY-MM-DD")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("export", help="regenerate exports/ for outside consumers",
                       description="Write brief.json, brief.md and review.json. "
                                   "Runs automatically after every write to the "
                                   "default database.")
    p.add_argument("--out", type=Path, default=export.EXPORT_DIR, metavar="DIR",
                   help="where to write (default: exports/)")
    p.add_argument("--since", metavar="DATE", help="review period start, YYYY-MM-DD")
    p.add_argument("--until", metavar="DATE", help="review period end, YYYY-MM-DD")
    p.set_defaults(func=cmd_export, refresh=False)

    p = sub.add_parser("init", help="create the database (first run only)",
                       description="Create a new database. Refuses if one already exists.")
    p.set_defaults(func=cmd_init, refresh=False)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    try:
        if args.refresh:
            refresh_if_stale(args)
        args.func(args)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.IntegrityError as exc:
        print(f"error: {explain(exc)}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    return 0