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