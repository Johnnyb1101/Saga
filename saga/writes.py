"""Every write to the database happens here.

Functions take an open connection rather than opening their own, so the
caller decides what belongs in a single transaction.

All SQL uses ? placeholders. Values are never formatted into a statement.
"""

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

def add_task(con, title, category, project_id=None, due_date=None, duty=None):
    """Insert a task. Returns its new id."""
    with con:
        cur = con.execute(
            """
            INSERT INTO tasks (title, category, project_id, due_date, duty)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, category, project_id, due_date, duty),
        )
    return cur.lastrowid

def add_measure(con, name):
    """Register a measure so quantities can be recorded against it."""
    with con:
        con.execute("INSERT INTO measures (name) VALUES (?)", (name,))
    return name

def add_duty(con, name):
    """Register a duty so tasks and completions can be filed under it."""
    with con:
        con.execute("INSERT INTO duties (name) VALUES (?)", (name,))
    return name

def _insert_completion(con, task_id, category, outcome, measure, quantity,
                       flagged, completed_at, duty):
    """Shared INSERT for both completion paths. The caller owns the transaction."""
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
                  flagged=False, completed_at=None, duty=None):
    """Archive a completion and close the task. Returns the completion id."""
    task = con.execute(
        "SELECT status, category, duty FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()

    if task is None:
        raise ValueError(f"No task with id {task_id}.")
    if task["status"] != "open":
        raise ValueError(f"Task {task_id} is already {task['status']}.")

    with con:
        cur = _insert_completion(con, task_id, task["category"], outcome,
                                 measure, quantity, flagged, completed_at,
                                 task["duty"] if duty is None else duty)
        con.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
    return cur.lastrowid


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
