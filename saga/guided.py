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


@dataclass(frozen=True)
class Measurement:
    status: str
    unit: Reference
    quantity: float | None = None
    remind_on: str | None = None

    def __str__(self):
        if self.status == 'measured':
            return f"{self.quantity:g} {self.unit}"
        if self.status == 'unknown':
            return f"Not known yet; remind me on {self.remind_on}"
        if self.status == 'unspecified':
            return "Unspecified (no measurement decision recorded)"
        return "Not applicable (narrative evidence)"


def reminder_day():
    while True:
        value = answer("Remind me on (YYYY-MM-DD, today or later)")
        try:
            return writes.validate_reminder(value)
        except ValueError as exc:
            print(exc)


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
    if kind == "measure":
        choices[0] = ("Not applicable - this accomplishment has no useful numeric measure", Reference(None, "Not applicable"))
        choices.append(("Not known yet - set a reminder", Reference(None, "Not known yet")))
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
        if selected.label == 'Not known yet':
            return Measurement('unknown', selected, remind_on=reminder_day())
        return Measurement('not_applicable', selected)
    while True:
        raw = answer("Quantity (0 is an actual result; negative values and fractions are allowed)")
        try:
            return Measurement("measured", selected, writes.validate_quantity(float(raw)))
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
        measurement = draft["measurement"]
        selected = measurement.unit
        values.update(measure=None if selected.new else selected.value, quantity=measurement.quantity,
                      measurement_status=measurement.status, remind_on=measurement.remind_on)
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


def manage_followups(con, path):
    rows = reads.measurement_followups(con)
    if not rows:
        print("No open measurement follow-ups.")
        return
    row = pick("Measurement follow-ups", [
        ((f"{r['remind_on']} - {r['outcome'] or r['task_title'] or '(no outcome)'} "
          f"[{r['category']}; {r['duty'] or 'no role'}; completed {r['completed_at'][:10]}; ID {r['id']}]") ,
         dict(r)) for r in rows])
    print(f"Work completed: {row['completed_at']}; original task deadline: {row['due_date'] or 'none'}")
    print("Reminder overdue does not mean the work was completed late.")
    action = choose("Follow-up action", [("Record measurement", "measured"),
        ("Not applicable", "not_applicable"), ("Remind me later", "later")], cancel_label="Return")
    if action == 'later':
        def postpone(draft):
            writes.reschedule_followup(con, row['id'], draft['remind_on'])
            saved(con, path, "Reminder rescheduled. Work completion date unchanged.")
        form("Reschedule reminder", [("remind_on", "Reminder date", reminder_day)], postpone)
        return

    def completion_date():
        keep = choose("Actual completion date", [(f"Keep {row['completed_at']}", True),
                                                 ("Correct the actual date", False)])
        if keep:
            return row['completed_at']
        return day(completed=True) or dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    questions = [("completed_at", "Actual completion date", completion_date)]
    if not row['outcome'] or not row['outcome'].strip():
        questions.append(("outcome", "Outcome", lambda: answer("What happened and why did it matter?")))
    if action == 'measured':
        questions.append(("measurement", "Measurement", lambda: measurement(con)))
    questions.append(("reason", "Correction reason", lambda: answer("Reason for this evidence update")))

    def resolve(draft):
        value = draft.get('measurement', Measurement('not_applicable', Reference(None, 'Not applicable')))
        if value.status == 'unknown':
            raise ValueError("Use Remind me later to postpone a follow-up")
        unit = value.unit
        changes = {'measurement_status': value.status, 'measure': None if unit.new else unit.value,
                   'quantity': value.quantity, 'completed_at': draft['completed_at']}
        if 'outcome' in draft:
            changes['outcome'] = draft['outcome']
        writes.resolve_followup(con, row['id'], row['completion_id'], draft['reason'],
                                new_measure=unit.value if unit.new else None, **changes)
        saved(con, path, "Evidence updated and follow-up resolved. No additional accomplishment counted.")

    title = "Record measurement" if action == 'measured' else "Resolve follow-up: Not applicable"
    form(title, questions, resolve)


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


def review_period(con):
    selected = choose("Review period", [("All recorded dates", "all"), ("Enter an inclusive date range", "range")])
    if selected == "all":
        return None, None
    while True:
        since = answer("Start date (YYYY-MM-DD)")
        until = answer("End date (YYYY-MM-DD)")
        try:
            reads.validate_review_scope(con, since, until)
            return since, until
        except ValueError as exc:
            print(exc)


def review_scope(con):
    """Explicit selectors with Back navigation; roles come from historical evidence."""
    step = 0
    since = until = selected_category = None
    while True:
        try:
            if step == 0:
                since, until = review_period(con)
            elif step == 1:
                selected_category = pick("Review category", [("All categories", None),
                    *[(r['name'], r['name']) for r in reads.categories(con)]])
            else:
                rows = reads.completion_list(con, since, until, category=selected_category)
                names = sorted({r['duty'] for r in rows if r['duty'] is not None})
                duty, unassigned = pick("Historical role or responsibility", [
                    ("All roles", (None, False)), ("No role assigned", (None, True)),
                    *[(name, (name, False)) for name in names]])
                return since, until, duty, selected_category, unassigned
            step += 1
        except Back:
            if step == 0:
                raise
            step -= 1


def show_completion(row, current=True):
    print(f"\nCOMPLETION {row['id']} ({'current' if current else 'superseded'})")
    for field in ('completed_at', 'recorded_at', 'category', 'duty', 'outcome',
                  'task_title', 'project_name', 'due_date', 'review_counting',
                  'flagged', 'measurement_status', 'measure', 'quantity',
                  'snapshot_source', 'supersedes_id', 'correction_reason'):
        label = 'Role or responsibility' if field == 'duty' else field.replace('_', ' ').capitalize()
        print(f"  {label}: {line(row[field]) if row[field] is not None else '-'}")


def correction_preview(original, draft, reason):
    print("\nREVIEW BEFORE SAVING - Correct accomplishment")
    labels = {'outcome': 'Outcome', 'category': 'Category', 'duty': 'Role or responsibility',
              'completed_at': 'Actual completion date', 'measurement': 'Measurement',
              'flagged': 'Flag for review', 'task_title': 'Saved task title',
              'due_date': 'Saved deadline', 'project_name': 'Saved project name',
              'review_counting': 'Saved review eligibility'}
    for field, label in labels.items():
        before, after = display(original[field]), display(draft[field])
        print(f"  {label}: {before} -> {after}" if draft[field] != original[field]
              else f"  {label}: {after} (unchanged)")
    print(f"  Correction reason: {line(reason) if reason else '(required before saving)'}")
    print("Saving appends a revision. Original evidence and working tasks/projects remain unchanged.")


def correction_context(draft):
    """Edit saved historical labels, never live tasks or project registrations."""
    while True:
        try:
            field = choose("Historical context", [("Saved task title", 'task_title'),
                ("Saved deadline", 'due_date'), ("Saved project name", 'project_name'),
                ("Saved review eligibility", 'review_counting')])
        except Back:
            return
        try:
            if field == 'review_counting':
                draft[field] = choose("Saved review eligibility", [
                    (f"Keep {display(draft[field])}", draft[field]), ("Counts toward review", True),
                    ("Does not count toward review", False)])
            else:
                action = choose(f"Saved {field.replace('_', ' ')}: {display(draft[field])}",
                                [("Keep", 'keep'), ("Change", 'change'), ("Clear", 'clear')])
                if action == 'clear':
                    draft[field] = None
                elif action == 'change':
                    draft[field] = day() if field == 'due_date' else answer("Corrected saved text")
        except Back:
            continue


def correct_accomplishment(con, path, selected):
    """Draft explicit evidence changes, preview all effects, then append one revision."""
    row = con.execute("SELECT * FROM current_completions WHERE id=?", (selected['id'],)).fetchone()
    if row is None:
        print("This accomplishment was superseded. Select its latest revision again.")
        return False
    original_row = dict(row)
    followup = con.execute("SELECT * FROM measurement_followups WHERE completion_id=?", (row['id'],)).fetchone()
    expected_followup = dict(followup) if followup is not None else None
    initial = {field: row[field] for field in ('outcome', 'category', 'completed_at',
               'task_title', 'due_date', 'project_name')}
    initial.update(duty=Reference(row['duty'], row['duty'] or 'None / not applicable'),
                   flagged=bool(row['flagged']), review_counting=bool(row['review_counting']),
                   measurement=Measurement(row['measurement_status'],
                       Reference(row['measure'], row['measure'] or 'No unit'), row['quantity'],
                       followup['remind_on'] if followup is not None and followup['status'] == 'open' else None))
    draft = dict(initial)
    reason = None
    while True:
        correction_preview(initial, draft, reason)
        try:
            field = choose("Correct accomplishment", [("Outcome", 'outcome'), ("Category", 'category'),
                ("Role or responsibility", 'duty'), ("Actual completion date", 'completed_at'),
                ("Measurement", 'measurement'), ("Flag for review", 'flagged'),
                ("Historical context (optional)", 'context'), ("Correction reason", 'reason'),
                ("Save correction", 'save')], cancel_label="Discard draft")
            if field == 'context':
                correction_context(draft)
                continue
            if field == 'reason':
                reason = answer("Why is this evidence being corrected?")
                continue
            if field == 'save':
                values, new = refs({'duty': draft['duty']})
                values.update({key: draft[key] for key in ('outcome', 'category', 'completed_at',
                              'flagged', 'task_title', 'due_date', 'project_name', 'review_counting')})
                if draft['measurement'] != initial['measurement']:
                    measured, registration = refs({'measurement': draft['measurement']})
                    values.update(measured)
                    new.update(registration)
                changes = {key: value for key, value in values.items()
                           if key == 'remind_on' or value != original_row[key]}
                # Include all measurement fields together so inference cannot turn a
                # deliberate measurement state into an unspecified one.
                if draft['measurement'] != initial['measurement']:
                    changes.update(measured)
                if draft['category'] != initial['category']:
                    changes['review_counting'] = draft['review_counting']
                if not new and not any(key in writes.CORRECTION_FIELDS and value != original_row[key]
                                       for key, value in changes.items()):
                    print("No evidence changes to save. Use Measurement follow-ups for reminder-only rescheduling.")
                    continue
                if reason is None:
                    reason = answer("Why is this evidence being corrected?")
                correction_preview(initial, draft, reason)
                decision = choose("Confirm correction", [("Save", 'save'), ("Edit answers", 'edit'),
                                                           ("Discard draft", 'discard')])
                if decision == 'discard':
                    return False
                if decision == 'edit':
                    continue
                try:
                    revision = writes.save_guided_correction(con, original_row, expected_followup,
                                                             reason, **new, **changes)
                except (ValueError, sqlite3.Error, OSError) as exc:
                    print(f"Could not save: {exc}. Edit or discard this draft.")
                    continue
                saved(con, path, f"Correction {revision} saved. No additional accomplishment counted.")
                return True
            if choose(f"{field.replace('_', ' ').capitalize()}: {display(draft[field])}",
                      [("Keep", True), ("Change", False)]):
                continue
            if field == 'outcome':
                draft[field] = answer("What happened and why did it matter?")
            elif field == 'category':
                selected_category = category(con)
                if selected_category != draft['category']:
                    print("Choose a compatible role or None. Review eligibility will use this category's current setting.")
                    role = reference(con, 'duty', selected_category)
                    eligibility = next(r['counts_toward_review'] for r in reads.categories(con)
                                       if r['name'] == selected_category)
                    draft.update(category=selected_category, duty=role, review_counting=bool(eligibility))
            elif field == 'duty':
                draft[field] = reference(con, 'duty', draft['category'])
            elif field == 'completed_at':
                draft[field] = day(completed=True) or dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            elif field == 'measurement':
                draft[field] = measurement(con)
            elif field == 'flagged':
                draft[field] = choose("Flag for review?", [("Yes", True), ("No", False)])
        except Back:
            continue
        except Cancel:
            return False


def browse_completions(con, scope, gaps_only=False, *, path):
    from saga import analytics

    while True:
        rows = reads.completion_list(con, *scope)
        gaps = {r['id']: r['reasons'] for r in analytics.evidence_gaps(con, *scope)} if gaps_only else {}
        if gaps_only:
            rows = [r for r in rows if r['id'] in gaps]
        if not rows:
            print("No evidence gaps found in the selected current entries." if gaps_only
                  else "No completions in this period.")
            return
        choices = []
        for row in rows:
            label = (f"{row['completed_at'][:10]} — {row['outcome'] or row['task_title'] or '(no outcome)'} "
                     f"[{row['category']}; {row['duty'] or 'No role assigned'}; "
                     f"{row['project_name'] or 'no project'}; ID {row['id']}]")
            if gaps_only:
                label += " — " + "; ".join(gaps[row['id']])
            choices.append((label, row))
        try:
            row = pick("Review evidence" if gaps_only else "Accomplishments", choices)
        except Back:
            return
        while True:
            show_completion(row)
            try:
                action = choose("Accomplishment action", [("View revision history", "history"),
                    ("Correct accomplishment", "correct")], cancel_label="Return")
                if action == 'correct':
                    correct_accomplishment(con, path, row)
                    # Requery current revisions and filters even after a stale selection.
                    break
                history = reads.completion_history(con, row['id'])
                for revision in history:
                    show_completion(revision, revision['id'] == history[-1]['id'])
            except (Back, Cancel):
                break


def review(con, path):
    from saga.cli import show_review, show_review_scope

    scope = review_scope(con)
    while True:
        show_review_scope(*scope)
        try:
            action = choose("Review", [("Summary and category / role breakdown", "summary"),
                ("Browse accomplishments", "browse"), ("Inspect evidence gaps", "gaps"),
                ("Change review filters", "filters")], cancel_label="Return")
        except (Back, Cancel):
            return
        if action == "summary":
            show_review(con, *scope)
        elif action == "filters":
            try:
                scope = review_scope(con)
            except (Back, Cancel):
                pass
        else:
            if action == "gaps":
                print("Review-counting or flagged entries; missing fields are prompts, not requirements.")
                print("Select an accomplishment to correct it, or use Measurement follow-ups for reminder-only changes.")
            browse_completions(con, scope, gaps_only=action == "gaps", path=path)


def project_day(label):
    selected = choose(label, [("No date", None), ("Today", 'today'), ("Enter a date", 'date')])
    if selected is None:
        return None
    if selected == 'today':
        return dt.date.today().isoformat()
    while True:
        value = answer(f"{label} (YYYY-MM-DD)")
        try:
            if dt.date.fromisoformat(value).isoformat() == value:
                return value
        except ValueError:
            pass
        print("Enter a valid date in YYYY-MM-DD form.")


def create_project(con, path):
    def description():
        include = choose("Description", [("Enter text", True), ("None", False)])
        return answer("Project description") if include else None

    def save(draft):
        project_id = writes.add_project(con, **draft)
        saved(con, path, f"Created project {project_id}: {line(draft['name'])}.")

    form("Create project", [("name", "Project name", lambda: answer("Project name")),
        ("description", "Description", description),
        ("start_date", "Start date", lambda: project_day("Start date")),
        ("deadline", "Deadline", lambda: project_day("Deadline"))], save)


def manage_projects(con, path):
    while True:
        try:
            action = choose("Manage projects", [("Browse projects", 'browse'), ("Create project", 'create')],
                            cancel_label="Return")
        except (Back, Cancel):
            return
        try:
            if action == 'create':
                create_project(con, path)
                continue
            while True:
                rows = reads.projects(con)
                if not rows:
                    print("No projects. Choose Create project to add one.")
                    break
                selected = pick("Projects", [((f"{r['name']} [{r['status']}; deadline {r['deadline'] or 'none'}; "
                                               f"{r['open_tasks']} open tasks; ID {r['id']}]"), r['id']) for r in rows])
                row = reads.project_details(con, selected)
                if row is None:
                    print("Project changed; select it again.")
                    continue
                tasks = reads.project_open_tasks(con, selected)
                print(f"\nPROJECT {row['id']}: {line(row['name'])}")
                for field in ('status', 'description', 'start_date', 'deadline'):
                    print(f"  {field.replace('_', ' ').capitalize()}: {display(row[field])}")
                print(f"  {len(tasks)} open tasks")
                try:
                    action = choose("Project action", [("View open tasks", 'tasks'), ("Close project", 'close')],
                                    cancel_label="Return")
                    if action == 'tasks':
                        if not tasks:
                            print("No open tasks.")
                        else:
                            pick("Open project tasks (select to return)", [((f"{t['title']} [{t['category']}; "
                                f"{t['duty'] or 'no role'}; due {t['due_date'] or 'none'}; ID {t['id']}]"), t['id'])
                                for t in tasks])
                    elif tasks:
                        print("Cannot close this project while tasks remain open. Complete or cancel them first.")
                    elif row['status'] not in ('active', 'on_hold'):
                        print("This project is already closed.")
                    else:
                        choose(f"Close {line(row['name'])}? Status will become done; history is retained.",
                               [("Close project", True)], cancel_label="Return")
                        writes.save_guided_management(con, 'close_project', dict(row), [])
                        saved(con, path, f"Closed project {row['id']}.")
                except (Back, Cancel):
                    continue
                except (ValueError, sqlite3.Error, OSError) as exc:
                    print(f"Could not finish project action: {exc}")
        except (Back, Cancel):
            continue


def manage_series(con, path):
    while True:
        rows = reads.recurrences(con)
        if not rows:
            print("No recurring series. Create a repeating task through Add a task.")
            return
        projects = {r['id']: r['name'] for r in reads.projects(con)}
        try:
            selected = pick("Recurring series", [((f"{r['title']} [{r['status']}; {r['category']}; "
                f"{r['duty'] or 'no role'}; {projects.get(r['project_id'], 'no project')}; "
                f"every {r['interval']} {r['frequency']} interval(s); "
                f"current task {r['task_id'] if r['task_id'] is not None else 'none'}; "
                f"due {r['due_date'] or 'none'}; ID {r['id']}]"), r['id']) for r in rows])
        except (Back, Cancel):
            return
        row = reads.recurrence_details(con, selected)
        if row is None:
            print("Series changed; select it again.")
            continue
        task = reads.open_occurrence(con, selected)
        print(f"\nSERIES {row['id']}: {line(row['title'])} [{row['status']}]")
        print(f"  Schedule: every {row['interval']} {row['frequency']} interval(s), anchored {row['anchor_date']}")
        print(f"  Category: {row['category']}; role: {row['duty'] or 'none'}; "
              f"project: {line(projects.get(row['project_id'], 'none'))}")
        if task is None:
            print("  No open occurrence.")
        else:
            print(f"  Current task {task['id']}: {line(task['title'])}; due {task['due_date'] or 'none'}")
        if row['status'] == 'stopped':
            print("This series is already stopped. Its remaining task can still be completed or cancelled.")
            continue
        try:
            choose("Stop future occurrences? The current task stays open; completing it will not create a successor.",
                   [("Stop future occurrences", True)], cancel_label="Return")
            writes.save_guided_management(con, 'stop_recurrence', dict(row), [] if task is None else [dict(task)])
            saved(con, path, f"Stopped series {row['id']}; current task retained.")
        except (Back, Cancel):
            continue
        except (ValueError, sqlite3.Error, OSError) as exc:
            print(f"Could not stop series: {exc}")


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
                due = reads.measurement_followups(con, due_only=True)
                if due:
                    print(f"\n{len(due)} measurement follow-up(s) due or overdue. Open Measurement follow-ups.")
                try:
                    action = choose("Main menu", [("Browse tasks / today", "browse"), ("Add a task", "add"),
                        ("Complete a task", "done"), ("Record an accomplishment", "log"),
                        ("Reschedule a task", "reschedule"), ("Cancel a task", "cancel"), ("Manage roles", "roles"), ("Measurement follow-ups", "followups"), ("Review accomplishments", "review"),
                        ("Manage projects", "projects"), ("Manage recurring series", "series")],
                        cancel_label="Exit")
                except (Back, Cancel):
                    return 0
                try:
                    if action == "add":
                        add_task(con, path)
                    elif action == "log":
                        capture(con, path)
                    elif action == "review":
                        review(con, path)
                    elif action == "projects":
                        manage_projects(con, path)
                    elif action == "series":
                        manage_series(con, path)
                    elif action == "followups":
                        manage_followups(con, path)
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
