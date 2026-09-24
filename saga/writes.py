"""Every write to the database happens here.

Functions take an open connection rather than opening their own, so the
caller decides what belongs in a single transaction.

All SQL uses ? placeholders. Values are never formatted into a statement.
"""

import datetime as dt
import math

from saga.recurrence import occurrence_date
from saga.roles import name_key


def add_project(con, name, description=None, start_date=None, deadline=None):
    """Insert a project. Returns its new id."""
    with con:
        cur = con.execute(
            """
            INSERT INTO projects (name, description, start_date, deadline)
            VALUES (?, ?, ?, ?)
            """,
            (name, description, start_date, deadline),
        )
    return cur.lastrowid

def _require_project_accepts_tasks(con, project_id):
    if project_id is not None:
        project = con.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise ValueError(f"No project with id {project_id}.")
        if project["status"] in ("done", "cancelled"):
            raise ValueError(f"Project {project_id} is {project['status']}; cannot add tasks.")


def _insert_task(con, title, category, project_id, due_date, duty,
                 recurrence_id=None, occurrence_index=None):
    """Insert within the caller's transaction, including recurrence advancement."""
    _require_project_accepts_tasks(con, project_id)
    require_role(con, category, duty)
    return con.execute(
        """INSERT INTO tasks (title, category, project_id, due_date, duty, recurrence_id, occurrence_index)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, category, project_id, due_date, duty, recurrence_id, occurrence_index),
    ).lastrowid


def add_task(con, title, category, project_id=None, due_date=None, duty=None,
             repeat=None, interval=1):
    """Insert a task, optionally with a schedule. Returns the first task id."""
    if repeat is None and interval != 1:
        raise ValueError("--interval requires --repeat")
    if repeat is not None:
        due_date = occurrence_date(due_date, repeat, interval, 0)
    with con:
        recurrence_id = None
        if repeat is not None:
            _require_project_accepts_tasks(con, project_id)
            recurrence_id = con.execute(
                """INSERT INTO recurrences
                   (title, category, project_id, duty, frequency, interval, anchor_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, category, project_id, duty, repeat, interval, due_date),
            ).lastrowid
        return _insert_task(con, title, category, project_id, due_date, duty,
                            recurrence_id, 0 if recurrence_id is not None else None)

def cancel_task(con, task_id):
    """Cancel an open task without creating an accomplishment."""
    with con:
        changed = con.execute("UPDATE tasks SET status='cancelled' WHERE id=? AND status='open'",
                              (task_id,))
        if changed.rowcount != 1:
            raise ValueError(f"Task {task_id} does not exist or is not open.")
        con.execute("""UPDATE recurrences SET status='stopped'
                       WHERE id=(SELECT recurrence_id FROM tasks WHERE id=?)""", (task_id,))


def stop_recurrence(con, recurrence_id):
    """Stop future generation, retaining the current task and all history."""
    with con:
        changed = con.execute("UPDATE recurrences SET status='stopped' WHERE id=? AND status='active'",
                              (recurrence_id,))
        if changed.rowcount != 1:
            raise ValueError(f"Recurrence {recurrence_id} is missing or already stopped.")


def reschedule_task(con, task_id, due_date):
    """Change or clear an open task's due date."""
    if due_date is not None:
        try:
            valid = dt.date.fromisoformat(due_date).isoformat() == due_date
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("due date must be a valid YYYY-MM-DD date")
    with con:
        changed = con.execute("UPDATE tasks SET due_date=? WHERE id=? AND status='open'",
                              (due_date, task_id))
        if changed.rowcount != 1:
            raise ValueError(f"Task {task_id} does not exist or is not open.")


def close_project(con, project_id):
    """Close an active or on-hold project only after all tasks are resolved."""
    with con:
        changed = con.execute(
            """UPDATE projects SET status='done'
               WHERE id=? AND status IN ('active', 'on_hold')
                 AND NOT EXISTS (SELECT 1 FROM tasks WHERE project_id=? AND status='open')""",
            (project_id, project_id),
        )
        if changed.rowcount != 1:
            raise ValueError(f"Project {project_id} is missing, already closed, or still has open tasks.")


def add_measure(con, name):
    """Register a measure so quantities can be recorded against it."""
    with con:
        con.execute("INSERT INTO measures (name) VALUES (?)", (name,))
    return name

def _register_role(con, name, category):
    key = name_key(name)
    if not key:
        raise ValueError("Role name must not be blank")
    duplicate = con.execute(
        "SELECT duty, status FROM category_roles WHERE category=? AND name_key=?",
        (category, key),
    ).fetchone()
    if duplicate:
        raise ValueError(f"Role {duplicate['duty']!r} already exists in {category} "
                         f"({duplicate['status']}); reuse or reactivate it")
    con.execute("INSERT INTO duties(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
    con.execute("INSERT INTO category_roles(category,duty,name_key) VALUES (?,?,?)",
                (category, name, key))


def add_duty(con, name, category):
    """Register a role in a category, or assign an unused legacy name explicitly."""
    with con:
        _register_role(con, name, category)
    return name


def require_role(con, category, duty, allow_inactive=False):
    if duty is None:
        return
    role = con.execute("SELECT status FROM category_roles WHERE category=? AND duty=?",
                       (category, duty)).fetchone()
    if role is None:
        raise ValueError(f"Role {duty!r} is not assigned to category {category!r}")
    if role['status'] != 'active' and not allow_inactive:
        raise ValueError(f"Role {duty!r} is {role['status']}; choose an active role or None")


def set_role_status(con, category, duty, active):
    with con:
        con.execute("BEGIN IMMEDIATE")
        role = con.execute("SELECT * FROM category_roles WHERE category=? AND duty=?",
                           (category, duty)).fetchone()
        if role is None:
            raise ValueError("No such role in this category")
        if not active and con.execute(
                "SELECT 1 FROM recurrences WHERE category=? AND duty=? AND status='active'",
                (category, duty)).fetchone():
            raise ValueError("Stop active recurring series using this role before retiring it")
        if active:
            if not role['name_key']:
                raise ValueError("A blank legacy name cannot be activated; retire it and add a named role")
            conflict = con.execute(
                "SELECT duty FROM category_roles WHERE category=? AND name_key=? "
                "AND duty<>? AND status='active'", (category, role['name_key'], duty)).fetchone()
            if conflict:
                raise ValueError(f"Conflicting active role: {conflict['duty']!r}. Resolve it explicitly first")
        con.execute("UPDATE category_roles SET status=? WHERE category=? AND duty=?",
                    ('active' if active else 'retired', category, duty))

def validate_quantity(quantity):
    """Reject non-finite measurements before SQLite can store or coerce them."""
    if quantity is not None and not math.isfinite(quantity):
        raise ValueError("quantity must be a finite number (not NaN or infinity)")
    return quantity


def _insert_completion(con, task_id, category, outcome, measure, quantity,
                       flagged, completed_at, duty, allow_inactive=False):
    """Shared INSERT for both completion paths. The caller owns the transaction."""
    validate_quantity(quantity)
    require_role(con, category, duty, allow_inactive)
    return con.execute(
        """
        INSERT INTO completions
            (task_id, category, completed_at, outcome, measure, quantity,
             flagged, duty, task_title, due_date, project_name, review_counting,
             recorded_at)
        VALUES (?, ?, COALESCE(?, datetime('now', 'localtime')), ?, ?, ?, ?, ?,
                (SELECT title FROM tasks WHERE id = ?),
                (SELECT due_date FROM tasks WHERE id = ?),
                (SELECT p.name FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.id = ?),
                (SELECT counts_toward_review FROM categories WHERE name = ?),
                datetime('now', 'localtime'))
        """,
        (task_id, category, completed_at, outcome, measure, quantity,
         int(flagged), duty, task_id, task_id, task_id, category),
    )

def add_completion(con, category, outcome=None, measure=None, quantity=None,
                   flagged=False, completed_at=None, duty=None):
    """Archive work that was never a task. Returns the completion id."""
    with con:
        cur = _insert_completion(con, None, category, outcome, measure,
                                 quantity, flagged, completed_at, duty)
    return cur.lastrowid

def complete_task(con, task_id, outcome=None, measure=None, quantity=None,
                  flagged=False, completed_at=None, duty=None, inherit_duty=True):
    """Archive a completion and close the task. Returns the completion id."""
    task = con.execute(
        "SELECT status, category, duty, recurrence_id, occurrence_index FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()

    if task is None:
        raise ValueError(f"No task with id {task_id}.")
    if task["status"] != "open":
        raise ValueError(f"Task {task_id} is already {task['status']}.")

    with con:
        cur = _insert_completion(con, task_id, task["category"], outcome,
                                 measure, quantity, flagged, completed_at,
                                 task["duty"] if duty is None and inherit_duty else duty,
                                 allow_inactive=(duty == task["duty"] or duty is None and inherit_duty))
        changed = con.execute("UPDATE tasks SET status = 'done' WHERE id = ? AND status='open'", (task_id,))
        if changed.rowcount != 1:
            raise ValueError(f"Task {task_id} is no longer open.")
        if task["recurrence_id"] is not None:
            series = con.execute("SELECT * FROM recurrences WHERE id=?", (task["recurrence_id"],)).fetchone()
            if series["status"] == "active":
                next_index = task["occurrence_index"] + 1
                due = occurrence_date(series["anchor_date"], series["frequency"], series["interval"], next_index)
                _insert_task(con, series["title"], series["category"], series["project_id"], due,
                             series["duty"], series["id"], next_index)
    return cur.lastrowid


def save_guided(con, action, values, *, new_duty=None, new_measure=None,
                new_project=None, expected_task=None):
    """Save a confirmed form and its new references as one transaction.

    Owns a fresh connection transaction; never call while another write is pending.
    The existing action functions commit only after references and action succeed.
    """
    actions = {"add": add_task, "log": add_completion, "done": complete_task,
               "reschedule": reschedule_task, "cancel": cancel_task}
    if action not in actions:
        raise ValueError("Unsupported guided action")
    if con.in_transaction:
        raise ValueError("Finish the current transaction before saving a guided form")
    values = dict(values)
    with con:
        con.execute("BEGIN IMMEDIATE")
        if expected_task is not None:
            current = con.execute("SELECT * FROM tasks WHERE id=?", (expected_task["id"],)).fetchone()
            if current is None or dict(current) != expected_task:
                raise ValueError("This task changed while you were answering. Cancel and select it again.")
            if values.get("task_id") != expected_task["id"]:
                raise ValueError("Selected task does not match the action")
        if new_duty is not None:
            role_category = values.get("category")
            if role_category is None:
                task = con.execute("SELECT category FROM tasks WHERE id=?", (values.get("task_id"),)).fetchone()
                if task is None:
                    raise ValueError("Choose a category before adding a role")
                role_category = task['category']
            _register_role(con, new_duty, role_category)
            values["duty"] = new_duty
        if new_measure is not None:
            con.execute("INSERT INTO measures(name) VALUES (?)", (new_measure,))
            values["measure"] = new_measure
        if new_project is not None:
            values["project_id"] = con.execute(
                "INSERT INTO projects(name) VALUES (?)", (new_project,)
            ).lastrowid
        return actions[action](con, **values)


CORRECTION_FIELDS = (
    "category", "completed_at", "outcome", "measure", "quantity", "flagged", "duty",
    "task_title", "due_date", "project_name", "review_counting",
)


def correct_completion(con, completion_id, reason, **changes):
    """Append a replacement of the latest revision, retaining all original rows."""
    if not reason or not reason.strip():
        raise ValueError("a nonblank correction reason is required")
    if not changes or set(changes) - set(CORRECTION_FIELDS):
        raise ValueError("supply supported fields to correct")
    with con:
        original = con.execute(
            "SELECT * FROM current_completions WHERE id = ?", (completion_id,)
        ).fetchone()
        if original is None:
            raise ValueError(f"Completion {completion_id} is missing or superseded; use completions/history to find its latest id.")
        values = dict(original)
        values.update(changes)
        require_role(con, values['category'], values['duty'],
                     allow_inactive=(values['category'], values['duty']) == (original['category'], original['duty']))
        validate_quantity(values["quantity"])
        if not values["outcome"] or not values["outcome"].strip():
            raise ValueError("a corrected completion must have a nonblank outcome")
        if "category" in changes and changes["category"] != original["category"] and "review_counting" not in changes:
            category = con.execute("SELECT counts_toward_review FROM categories WHERE name=?",
                                   (changes["category"],)).fetchone()
            if category is None:
                raise ValueError("no such category")
            values["review_counting"] = category[0]
        if all(values[field] == original[field] for field in CORRECTION_FIELDS):
            raise ValueError("correction does not change any recorded evidence")
        cur = con.execute(
            """INSERT INTO completions
                (task_id, category, completed_at, outcome, measure, quantity, flagged, duty,
                 task_title, due_date, project_name, review_counting, snapshot_source,
                 recorded_at, supersedes_id, correction_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), ?, ?)""",
            (values["task_id"], *(values[field] for field in CORRECTION_FIELDS),
             values["snapshot_source"], completion_id, reason.strip()),
        )
    return cur.lastrowid
