"""Read-only queries. Nothing in this module modifies the database.

Every function takes an open connection and returns sqlite3.Row objects.

Date parameters default to today when omitted. They exist so that tests
can pin a date instead of depending on when they happen to run.
"""

def get_task(con, task_id):
    """One task by id, or None if there is no such task."""
    return con.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()

def open_tasks(con, category=None):
    """Every open task, optionally filtered by category. Undated ones last."""
    return con.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'open'
          AND (? IS NULL OR category = ?)
        ORDER BY due_date IS NULL, due_date, id
        """,
        (category, category),
    ).fetchall()

def due_today(con, on=None):
    """Open tasks due on the given day. Defaults to today."""
    return con.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'open'
          AND due_date = COALESCE(?, date('now', 'localtime'))
        ORDER BY category, id
        """,
        (on,),
    ).fetchall()

def overdue(con, on=None):
    """Open tasks whose due date has already passed."""
    return con.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'open'
          AND due_date < COALESCE(?, date('now', 'localtime'))
        ORDER BY due_date, id
        """,
        (on,),
    ).fetchall()

def upcoming_deadlines(con, days=30, on=None):
    """Active projects with a deadline inside `days`, and their open task counts."""
    return con.execute(
        """
        SELECT p.id,
               p.name,
               p.deadline,
               CAST(julianday(p.deadline)
                    - julianday(COALESCE(?, date('now', 'localtime'))) AS INTEGER)
                   AS days_left,
               count(t.id) AS open_tasks
        FROM projects p
        LEFT JOIN tasks t
               ON t.project_id = p.id
              AND t.status = 'open'
        WHERE p.status = 'active'
          AND p.deadline <= date(COALESCE(?, date('now', 'localtime')),
                                 '+' || ? || ' days')
        GROUP BY p.id
        ORDER BY p.deadline
        """,
        (on, on, days),
    ).fetchall()

def categories(con):
    """All category names, the review-counting ones first."""
    return con.execute(
        """
        SELECT name, counts_toward_review
        FROM categories
        ORDER BY counts_toward_review DESC, name
        """
    ).fetchall()

def measures(con):
    """All registered measure names."""
    return con.execute("SELECT name FROM measures ORDER BY name").fetchall()

def measure_usage(con):
    """Every registered measure with how often it has been recorded.

    LEFT JOIN, so a measure nothing has ever been logged against still
    appears, with a zero. Those are the ones worth noticing.
    """
    return con.execute(
        """
        SELECT m.name,
               count(c.id)     AS occasions,
               sum(c.quantity) AS total
        FROM measures m
        LEFT JOIN completions c ON c.measure = m.name
        GROUP BY m.name
        ORDER BY occasions DESC, m.name
        """
    ).fetchall()


def duties(con):
    """All registered duty names."""
    return con.execute("SELECT name FROM duties ORDER BY name").fetchall()


def duty_usage(con):
    """Every registered duty with how many completions carry it.

    LEFT JOIN, so a duty nothing has been logged against still appears
    with a zero.
    """
    return con.execute(
        """
        SELECT d.name,
               count(c.id) AS completions
        FROM duties d
        LEFT JOIN completions c ON c.duty = d.name
        GROUP BY d.name
        ORDER BY completions DESC, d.name
        """
    ).fetchall()


def due_soon(con, days=7, on=None):
    """Open tasks due after today but within `days`."""
    return con.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'open'
          AND due_date > COALESCE(?, date('now', 'localtime'))
          AND due_date <= date(COALESCE(?, date('now', 'localtime')),
                               '+' || ? || ' days')
        ORDER BY due_date, id
        """,
        (on, on, days),
    ).fetchall()