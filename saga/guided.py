"""A terminal menu over Saga's shared core. Draft answers stay in memory."""

import datetime as dt
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from saga import db, export, reads, writes
from saga.recurrence import occurrence_date

PAGE_SIZE = 8


class Back(Exception):
    """Return to the previous question."""


class Cancel(Exception):
    """Discard this form or leave this menu."""


@dataclass(frozen=True)
class Reference:
    value: str | int | None
    label: str
    new: bool = False

    def __str__(self):
        return self.label + (" (new; saved only on confirmation)" if self.new else "")


def line(value):
    return " ".join(str(value).split())


def answer(prompt):
    while True:
        raw = input(f"{prompt}: ").strip()
        if raw.casefold() == ":back":
            raise Back
        if raw.casefold() in (":discard", ":cancel"):
            raise Cancel
        if raw:
            return raw
        print("Please answer explicitly. Use :back for the previous question or :discard to discard the draft.")


def choose(prompt, choices, cancel_label="Back"):
    """Choices are (display label, value); Back preserves the current draft."""
    print(f"\n{prompt}")
    for index, (label, _) in enumerate(choices, 1):
        print(f"  {index}. {line(label)}")
    print(f"  0. {cancel_label}")
    while True:
        raw = answer("Choose")
        if raw == "0":
            if cancel_label == "Back":
                raise Back
            raise Cancel
        if raw.isdecimal() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1][1]
        print("Choose a number from this list.")


def pick(prompt, choices):
    """Paginate reference selections too, so long duty/project lists stay usable."""
    page = 0
    original_choices = choices
    while True:
        subset = choices[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        print(f"\n{prompt} — page {page + 1} of {max(1, (len(choices) + PAGE_SIZE - 1) // PAGE_SIZE)}")
        for index, (label, _) in enumerate(subset, 1):
            print(f"  {index}. {line(label)}")
        print("  s. Search   a. Clear search   n. Next page   p. Previous page   0. Back")
        raw = answer("Choose").lower()
        if raw == "0":
            raise Back
        if raw == "s":
            needle = answer("Search names").casefold()
            choices = [(label, value) for label, value in original_choices if needle in str(label).casefold()]
            page = 0
        elif raw == "a":
            choices, page = original_choices, 0
        elif raw == "n" and (page + 1) * PAGE_SIZE < len(choices):
            page += 1
        elif raw == "p" and page:
            page -= 1
        elif raw.isdecimal() and 1 <= int(raw) <= len(subset):
            return subset[int(raw) - 1][1]
        else:
            print("Choose a listed number or an available page.")


def category(con):
    return pick("Category — which area of life? (work, school, home, etc.)", [(r["name"], r["name"]) for r in reads.categories(con)])


def reference(con, kind, category_name=None):
    label = {"duty": "role or responsibility", "project": "project", "measure": "measurement unit"}[kind]
    explanation = {
        "duty": "Which ongoing responsibility does this belong to? For example: training or maintenance.",
        "project": "Which specific effort with an end goal does this belong to? Choose none for standalone work.",
        "measure": "What are you counting? For example: hours saved or packages completed.",
    }[kind]
    print(explanation)
    if kind == "project":
        rows = [r for r in reads.projects(con) if r["status"] in ("active", "on_hold")]
        refs = [Reference(r["id"], f"{r['name']} — {r['status']} — deadline {r['deadline'] or 'none'} (ID {r['id']})") for r in rows]
    else:
        rows = reads.duties(con, category_name) if kind == "duty" else reads.measures(con)
        refs = [Reference(r["name"], r["name"]) for r in rows]
    choices = [("None / not applicable", Reference(None, "None / not applicable")),
               (f"Add a new {label}", "new"), *[(str(r), r) for r in refs]]
    selected = pick(f"Choose {label}", choices)
    if selected == "new":
        while True:
            name = answer(f"New {label} name")
            if kind != "project" and any(r.value == name for r in refs):
                print("That name already exists. Choose it from the list instead.")
                continue
            return Reference(name, name, True)
    return selected


def day(completed=False):
    options = [("Today", "today"), ("Enter another date", "date")]
    if not completed:
        options.append(("No deadline", None))
    selected = choose("Completion date" if completed else "Due date", options)
    if selected is None:
        return None
    if selected == "today":
        return None if completed else dt.date.today().isoformat()
    while True:
        value = answer("Date (YYYY-MM-DD)")
        try:
            parsed = dt.date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
            if completed and parsed > dt.date.today():
                print("Completion date cannot be in the future.")
                continue
            return f"{value} 00:00:00" if completed else value
        except ValueError:
            print("Enter a valid date in YYYY-MM-DD form.")


def measurement(con):
    selected = reference(con, "measure")
    if selected.value is None:
        return selected, None
    while True:
        raw = answer("Quantity (0 is an actual result; negative values and fractions are allowed)")
        try:
            return selected, writes.validate_quantity(float(raw))
        except ValueError:
            print("Enter a finite number, not NaN or infinity.")


def repeat():
    frequency = choose("Repeat", [("One-time task", None), ("Daily", "daily"),
                                  ("Weekly", "weekly"), ("Monthly", "monthly")])
    if frequency is None:
        return None, 1
    while True:
        raw = answer("Every how many intervals? (enter 1 for every interval)")
        try:
            interval = int(raw)
            if not 1 <= interval <= 9223372036854775807:
                raise ValueError
            return frequency, interval
        except ValueError:
            print("Enter a positive whole number within SQLite's integer range.")


def display(value):
    if isinstance(value, tuple):
        first, second = value
        if isinstance(first, Reference):
            return str(first) if first.value is None else f"{second:g} {first}"
        if first is None:
            return "One-time task"
        unit = {"daily": "day", "weekly": "week", "monthly": "month"}[first]
        return f"Every {second} {unit}{'s' if second != 1 else ''}"
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return line(value)


def form(title, questions, save, initial=None):
    """questions: (key, label, ask callable); no database writes until Save."""
    draft = dict(initial or {})
    index = 0
    editing = False
    print(f"\n{title}\nAnswer every question. :back revisits a question; :discard discards the draft.")
    while True:
        if index < len(questions):
            key, label, prompt = questions[index]
            print(f"\n{index + 1}/{len(questions)} — {label}")
            if key in draft:
                print(f"Previous answer: {display(draft[key])}")
            try:
                previous = draft.get(key)
                draft[key] = prompt()
                if editing and key == "category" and draft[key] != previous:
                    draft.pop("duty", None)
                    index = next(i for i, (name, _, _) in enumerate(questions) if name == "duty")
                    print("Category changed. Choose a role for this category explicitly.")
                else:
                    index = len(questions) if editing else index + 1
            except Back:
                if index:
                    index -= 1
                else:
                    print("This is the first question. Use :discard to discard the draft.")
            continue
        missing = next((i for i, (key, _, _) in enumerate(questions) if key not in draft), None)
        if missing is not None:
            index = missing
            continue
        print(f"\nREVIEW BEFORE SAVING — {title}")
        for key, label, _ in questions:
            value = "Today (time of save)" if key == "completed_at" and draft[key] is None else display(draft[key])
            if initial is not None:
                before = display(initial[key])
                print(f"  {label}: {before} -> {value}" if draft[key] != initial[key]
                      else f"  {label}: {value} (unchanged)")
            else:
                print(f"  {label}: {value}")
        try:
            decision = choose("Next", [("Save", "save"), ("Edit answers", "edit"), ("Discard draft", "cancel")])
            if decision == "cancel":
                raise Cancel
            if decision == "edit":
                try:
                    index = choose("Which answer?", [(label, i) for i, (_, label, _) in enumerate(questions)],
                                   cancel_label="Return")
                except (Back, Cancel):
                    continue
                editing = True
                continue
            try:
                save(draft)
                return
            except (ValueError, sqlite3.Error, OSError) as exc:
                print(f"Could not save: {exc}. Your draft is still here; edit or discard the draft.")
        except Back:
            index = len(questions) - 1
            editing = True


def saved(con, path, message):
    print(f"\nSaved. {message}")
    if Path(path).resolve() == db.DB_PATH.resolve():
        try:
            export.write_all(con)
        except (export.ExportError, sqlite3.Error, OSError) as exc:
            print(f"The database change was saved, but export failed: {exc}. Retry export, not this action.")


def refs(draft):
    """Translate display references into write parameters and deferred registrations."""
    values, new = {}, {}
    for kind in ("duty", "project"):
        if kind not in draft:
            continue
        selected = draft[kind]
        values["project_id" if kind == "project" else kind] = None if selected.new else selected.value
        if selected.new:
            new[f"new_{kind}"] = selected.value
    if "measurement" in draft:
        selected, quantity = draft["measurement"]
        values.update(measure=None if selected.new else selected.value, quantity=quantity)
        if selected.new:
            new["new_measure"] = selected.value
    return values, new


def add_task(con, path):
    selected_category = {}
    def select_category():
        selected_category['name'] = category(con)
        return selected_category['name']
    questions = [
        ("title", "Task title", lambda: answer("What needs to be done?")),
        ("category", "Category", select_category),
        ("duty", "Role or responsibility", lambda: reference(con, "duty", selected_category["name"])),
        ("project", "Project", lambda: reference(con, "project")),
        ("due_date", "Due date", day),
        ("repeat", "Repeat", repeat),
    ]

    def save(draft):
        values, new = refs(draft)
        frequency, interval = draft["repeat"]
        if frequency:
            occurrence_date(draft["due_date"], frequency, interval, 0)
        values.update(title=draft["title"], category=draft["category"], due_date=draft["due_date"],
                      repeat=frequency, interval=interval)
        task_id = writes.save_guided(con, "add", values, **new)
        saved(con, path, f"Added {draft['title']} (task ID {task_id}).")

    form("Add a task", questions, save)


def capture(con, path, task=None):
    selected_category = {"name": task["category"]} if task else {}
    def select_category():
        selected_category['name'] = category(con)
        return selected_category['name']
    questions = []
    if task:
        print(f"\nSelected task: {line(task['title'])} — category {task['category']}")
        projects = {r["id"]: r["name"] for r in reads.projects(con)}
        context = f"Category {task['category']}; project {projects.get(task['project_id'], 'none')}"
        print("Category and project carry forward from the task; task-field editing is separate.")
        questions.append(("context", context, lambda: choose("Confirm task context", [(context, "Confirmed")])))
    else:
        questions.append(("category", "Category", select_category))
    questions.extend([
        ("outcome", "Outcome (required)", lambda: answer("What did you accomplish, and what changed?")),
        ("completed_at", "Completion date", lambda: day(completed=True)),
    ])
    if task and task["duty"] is not None:
        def duty():
            keep = choose("Role or responsibility", [(f"Keep {task['duty']}", True), ("Choose another role or responsibility, or none", False)])
            return Reference(task["duty"], task["duty"]) if keep else reference(con, "duty", selected_category["name"])
        questions.append(("duty", "Role or responsibility", duty))
    else:
        questions.append(("duty", "Role or responsibility", lambda: reference(con, "duty", selected_category["name"])))
    questions.extend([
        ("measurement", "Measurement", lambda: measurement(con)),
        ("flagged", "Flag for review", lambda: choose("Useful for a performance review?", [("Yes", True), ("No", False)])),
    ])

    def save(draft):
        values, new = refs(draft)
        values.update({key: draft[key] for key in ("outcome", "completed_at", "flagged")})
        if task:
            values.update(task_id=task["id"], inherit_duty=False)
            completion_id = writes.save_guided(con, "done", values, expected_task=task, **new)
        else:
            values["category"] = draft["category"]
            completion_id = writes.save_guided(con, "log", values, **new)
        saved(con, path, f"Recorded accomplishment (completion ID {completion_id}).")

    title = f"Complete: {line(task['title'])}" if task else "Record an accomplishment"
    if task and task["recurrence_id"] is not None:
        print("Completing this occurrence creates the next task if its series is active.")
    form(title, questions, save)


def task_picker(con):
    search, selected_category, due_only, page = "", None, False, 0
    while True:
        rows = reads.selectable_tasks(con, search, selected_category, due_only)
        page = min(page, max(0, (len(rows) - 1) // PAGE_SIZE))
        subset = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        print(f"\nTASKS — {len(rows)} matches; page {page + 1}")
        print(f"Search: {search or '(all)'}; category: {selected_category or '(all)'}; "
              f"due: {'today or overdue' if due_only else 'all open'}")
        for index, row in enumerate(subset, 1):
            print(f"  {index}. {line(row['title'])} — {row['category']} — due {row['due_date'] or 'none'}")
            print(f"     Role or responsibility: {line(row['duty'] or 'none')}; project: {line(row['project_name'] or 'none')}; ID {row['id']}")
        if not rows:
            print("No matching tasks. Add one from the main menu or clear the filters.")
        print("  s. Search   c. Category   d. Toggle due today/overdue   a. Clear filters")
        print("  n. Next page   p. Previous page   0. Main menu")
        raw = answer("Select a row number or command").lower()
        if raw == "0":
            raise Cancel
        if raw.isdecimal() and 1 <= int(raw) <= len(subset):
            # Only the row number is user input; the database ID comes from this list.
            selected = dict(subset[int(raw) - 1])
            selected.pop("project_name")
            return selected
        if raw == "s":
            try:
                search = answer("Search title, role or responsibility, category, or project")
                page = 0
            except Back:
                pass
        elif raw == "c":
            try:
                selected_category = choose("Category filter", [("All categories", None),
                    *[(r["name"], r["name"]) for r in reads.categories(con)]], cancel_label="Return")
                page = 0
            except (Back, Cancel):
                pass
        elif raw == "d":
            due_only, page = not due_only, 0
        elif raw == "a":
            search, selected_category, due_only, page = "", None, False, 0
        elif raw == "n" and (page + 1) * PAGE_SIZE < len(rows):
            page += 1
        elif raw == "p" and page:
            page -= 1
        else:
            print("Choose a listed row number or command.")


def edit_task(con, path, task):
    projects = {r['id']: r['name'] for r in reads.projects(con)}
    initial = {"title": task['title'], "category": task['category'],
               "duty": Reference(task['duty'], task['duty'] or 'None / not applicable'),
               "project": Reference(task['project_id'], projects.get(task['project_id'], 'None / not applicable'))}
    selected_category = {"name": task['category']}

    def keep_or_change(label, current, prompt):
        keep = choose(label, [(f"Keep saved value: {display(current)}", True), ("Change", False)])
        return current if keep else prompt()

    def select_category():
        selected_category['name'] = keep_or_change("Category", task['category'], lambda: category(con))
        return selected_category['name']

    def select_role():
        prompt = lambda: reference(con, "duty", selected_category['name'])
        if selected_category['name'] != task['category']:
            return prompt()
        return keep_or_change("Role or responsibility", initial['duty'], prompt)

    questions = [
        ("title", "Task title", lambda: keep_or_change("Title", task['title'], lambda: answer("New task title"))),
        ("category", "Category", select_category),
        ("duty", "Role or responsibility", select_role),
        ("project", "Project", lambda: keep_or_change("Project", initial['project'], lambda: reference(con, "project"))),
    ]

    def save(draft):
        values, new = refs(draft)
        values.update(task_id=task['id'], title=draft['title'], category=draft['category'])
        writes.save_guided(con, "edit", values, expected_task=task, **new)
        saved(con, path, f"Updated {line(draft['title'])} (task ID {task['id']}).")

    if task['recurrence_id'] is not None:
        print("This edit changes only this occurrence. Future occurrences keep the series settings.")
    form(f"Edit task: {line(task['title'])}", questions, save, initial=initial)


def task_action(con, path, task, action=None):
    print(f"\n{line(task['title'])} — {task['category']} — due {task['due_date'] or 'none'} (ID {task['id']})")
    if action is None:
        action = choose("Task action", [("Complete", "done"), ("Reschedule", "reschedule"),
                                        ("View details", "details"), ("Cancel task", "cancel"),
                                        ("Edit task", "edit")], cancel_label="Return")
    if action == "edit":
        edit_task(con, path, task)
        return
    if action == "details":
        projects = {r["id"]: r["name"] for r in reads.projects(con)}
        for label, value in (("Title", task["title"]), ("Category", task["category"]),
                             ("Role or responsibility", task["duty"]), ("Project", projects.get(task["project_id"])),
                             ("Due", task["due_date"]), ("Status", task["status"]),
                             ("Created", task["created_at"])):
            print(f"  {label}: {display(value)}")
        if task["recurrence_id"] is not None:
            series = next(r for r in reads.recurrences(con) if r["id"] == task["recurrence_id"])
            print(f"  Repeat: {display((series['frequency'], series['interval']))} ({series['status']})")
        else:
            print("  Repeat: One-time task")
        return
    if action == "done":
        capture(con, path, task)
        return
    if task["recurrence_id"] is not None:
        print("Cancelling stops the recurring series." if action == "cancel" else
              "Rescheduling changes only this occurrence, not the series anchor.")
    questions = [("due_date", "New due date", day)] if action == "reschedule" else [
        ("confirm", "Cancel this task (no accomplishment recorded)", lambda: choose("Cancel this task?", [("Yes", True), ("No", False)]))]

    def save(draft):
        if action == "cancel" and not draft["confirm"]:
            raise Cancel
        values = {"task_id": task["id"]}
        if action == "reschedule":
            values["due_date"] = draft["due_date"]
        writes.save_guided(con, action, values, expected_task=task)
        saved(con, path, f"{'Rescheduled' if action == 'reschedule' else 'Cancelled'} {line(task['title'])}.")

    form(f"{action.title()}: {line(task['title'])}", questions, save)


def manage_roles(con):
    selected = category(con)
    while True:
        rows = reads.duties(con, selected, include_inactive=True)
        print(f"\nRoles in {selected}: {len(rows)}")
        pending = sum(row['status'] == 'needs_review' for row in rows)
        if pending:
            print(f"{pending} legacy names need review. Explicitly activate one or retire them.")
        unassigned = reads.unassigned_roles(con)
        if unassigned:
            print(f"{len(unassigned)} legacy roles still need a category assignment.")
        try:
            action = choose("Manage roles", [("Add / assign a role", "add"),
                ("Retire a role", "retire"), ("Activate a role", "activate"),
                ("View / search roles", "view"), ("Assign an unused legacy role", "assign")], cancel_label="Return")
            if action == "view":
                pick("Roles (select to return)", [(f"{r['name']} [{r['status']}]", r['name']) for r in rows])
                continue
            if action == "assign":
                name = pick("Unassigned legacy roles", [(r['name'], r['name']) for r in unassigned])
                choose(f"Assign {name} to {selected}?", [("Save", True)])
                writes.add_duty(con, name, selected)
            elif action == "add":
                name = answer("Role name (use the exact legacy name to assign an unassigned role)")
                choose(f"Add {name} to {selected}?", [("Save", True)])
                writes.add_duty(con, name, selected)
            else:
                name = pick("Choose a role", [(f"{r['name']} [{r['status']}]", r['name']) for r in rows])
                choose(f"{action.title()} {name} in {selected}?", [("Save", True)])
                writes.set_role_status(con, selected, name, action == "activate")
            print("Role saved. Historical records are unchanged.")
        except Back:
            continue
        except Cancel:
            return
        except (ValueError, sqlite3.Error) as exc:
            print(f"Could not save role: {exc}")


def run(path):
    if not sys.stdin.isatty():
        print("The guided menu requires a terminal.", file=sys.stderr)
        return 1
    try:
        if not Path(path).exists():
            choose(f"No database at {path}. Create one?", [("Create a new Saga database", True)], cancel_label="Exit")
            db.init_db(path)
        with closing(db.connect(path)) as con:
            print("\nSAGA — guided daily workflow\nUse row numbers to select tasks; no IDs to remember.")
            while True:
                try:
                    action = choose("Main menu", [("Browse tasks / today", "browse"), ("Add a task", "add"),
                        ("Complete a task", "done"), ("Record an accomplishment", "log"),
                        ("Reschedule a task", "reschedule"), ("Cancel a task", "cancel"), ("Manage roles", "roles")],
                        cancel_label="Exit")
                except (Back, Cancel):
                    return 0
                try:
                    if action == "add":
                        add_task(con, path)
                    elif action == "log":
                        capture(con, path)
                    elif action == "roles":
                        manage_roles(con)
                    else:
                        while True:
                            task = task_picker(con)
                            try:
                                task_action(con, path, task, None if action == "browse" else action)
                            except (Back, Cancel):
                                print("Returned to task list. No draft changes saved.")
                                continue
                            break
                except (Back, Cancel):
                    print("Returned to main menu. No draft changes saved.")
                except (ValueError, sqlite3.Error, OSError) as exc:
                    print(f"Could not finish that action: {exc}")
    except (Back, Cancel):
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nExited. Unconfirmed draft answers were discarded.")
        return 130
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"Cannot open Saga: {exc}", file=sys.stderr)
        return 1
