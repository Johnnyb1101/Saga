"""Command line interface. Parsing and presentation only.

Queries live in reads.py and analytics.py; writes live in writes.py. This
module turns arguments into calls and rows into text, and nothing else.
"""

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

from saga import db, reads, writes

def days_between(iso_date):
    """Whole days from `iso_date` until today. Positive means in the past."""
    return (dt.date.today() - dt.date.fromisoformat(iso_date)).days

def print_tasks(rows, show_due=False):
    """One line per task: id, category, optional age, title."""
    for row in rows:
        age = ""
        if show_due and row["due_date"]:
            age = f"{days_between(row['due_date']):>3}d late "
        print(f"  {row['id']:>4}  {row['category']:<9}{age}{row['title']}")

CONSTRAINT_HELP = {
    "date(": "dates must be YYYY-MM-DD with zero padding, e.g. 2026-09-04",
    "datetime(": "timestamps must be YYYY-MM-DD HH:MM:SS",
    "FOREIGN KEY": "no such category, project, or measure",
    "(measure is NULL)": "a measure and a quantity must be given together",
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

def cmd_init(args):
    print(f"Created {db.init_db(args.db)}")

def cmd_today(args):
    con = db.connect(args.db)
    late = reads.overdue(con)
    due = reads.due_today(con)

    if late:
        print("OVERDUE")
        print_tasks(late, show_due=True)
        print()
    if due:
        print("DUE TODAY")
        print_tasks(due)
        print()
    if not late and not due:
        print("Nothing due today.")

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
    parser.add_argument(
        "--db", type=Path, default=db.DB_PATH, metavar="PATH",
        help="database file to use (default: data/saga.db)",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("init", help="create the database (first run only)",
                       description="Create a new database. Refuses if one already exists.")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add", help="add a task",
                       description="Add a task. Run it bare to be prompted for everything.")
    p.add_argument("text", nargs="?", help="what the task is")
    p.add_argument("-c", "--category", help="category name")
    p.add_argument("--due", metavar="DATE", help="due date, YYYY-MM-DD")
    p.add_argument("--project", type=int, metavar="ID", help="project id")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("project", help="add a project",
                       description="Add a project. Run it bare to be prompted for everything.")
    p.add_argument("name", nargs="?", help="project name")
    p.add_argument("--deadline", metavar="DATE", help="deadline, YYYY-MM-DD")
    p.add_argument("--start", metavar="DATE", help="start date, YYYY-MM-DD")
    p.add_argument("--description", help="longer description")
    p.set_defaults(func=cmd_project)

    p = sub.add_parser("done", help="complete a task",
                       description="Complete a task and archive it. Run with just "
                                   "an id to be prompted for the rest.")
    p.add_argument("task_id", type=int, metavar="ID", help="task id (from `today`)")
    p.add_argument("--outcome", help="what happened")
    p.add_argument("--measure", metavar="NAME", help="a registered measure")
    p.add_argument("--quantity", type=float, metavar="N", help="how many")
    p.add_argument("--flag", action="store_true", help="mark as review material")
    p.set_defaults(func=cmd_done)

    p = sub.add_parser("today", help="tasks due today, plus anything overdue",
                       description="Open tasks due today, and anything already past due.")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("upcoming", help="project deadlines approaching",
                       description="Active projects with a deadline inside the window, "
                                   "and how many of their tasks are still open.")
    p.add_argument("--days", type=int, default=30, metavar="N",
                   help="how far ahead to look (default: 30)")
    p.set_defaults(func=cmd_upcoming)

    return parser

def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    try:
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