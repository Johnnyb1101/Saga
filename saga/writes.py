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

def add_task(con, title, category, project_id=None, due_date=None):
    """Insert a task. Returns its new id."""
    with con:
        cur = con.execute(
            """
            INSERT INTO tasks (title, category, project_id, due_date)
            VALUES (?, ?, ?, ?)
            """,
            (title, category, project_id, due_date),
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
             flagged, duty)
        VALUES (?, ?, COALESCE(?, datetime('now', 'localtime')), ?, ?, ?, ?, ?)
        """,
        (task_id, category, completed_at, outcome, measure, quantity,
         int(flagged), duty),
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