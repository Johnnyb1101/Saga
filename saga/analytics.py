"""Aggregate queries over the completion archive.

Every function takes optional `since` and `until` dates in YYYY-MM-DD
form, both inclusive. Omit them to cover the whole archive.

completed_at is a timestamp, so comparisons wrap it in date(). Comparing
the raw timestamp against a bare date silently drops everything recorded
after midnight on the closing day.
"""

def measure_totals(con, since=None, until=None):
    """Each measure with its summed quantity and how many occasions produced it."""
    return con.execute(
        """
        SELECT measure,
               sum(quantity) AS total,
               count(*)      AS occasions
        FROM completions
        WHERE measure IS NOT NULL
          AND (? IS NULL OR date(completed_at) >= ?)
          AND (? IS NULL OR date(completed_at) <= ?)
        GROUP BY measure
        ORDER BY total DESC
        """,
        (since, since, until, until),
    ).fetchall()

def volume(con, since=None, until=None):
    """Headline counts for a period. Returns a single row."""
    return con.execute(
        """
        SELECT count(*)                                            AS completions,
               count(*) FILTER (WHERE k.counts_toward_review = 1)  AS review_counting,
               count(*) FILTER (WHERE c.flagged = 1)               AS flagged,
               count(c.quantity)                                   AS with_a_number
        FROM completions c
        JOIN categories k ON k.name = c.category
        WHERE (? IS NULL OR date(c.completed_at) >= ?)
          AND (? IS NULL OR date(c.completed_at) <= ?)
        """,
        (since, since, until, until),
    ).fetchone()

def completions_by_category(con, since=None, until=None):
    """Every category with its counts, including categories with none."""
    return con.execute(
        """
        SELECT k.name AS category,
               count(c.id)                              AS completions,
               count(c.quantity)                        AS with_a_number,
               count(c.id) FILTER (WHERE c.flagged = 1) AS flagged
        FROM categories k
        LEFT JOIN completions c
               ON c.category = k.name
              AND (? IS NULL OR date(c.completed_at) >= ?)
              AND (? IS NULL OR date(c.completed_at) <= ?)
        GROUP BY k.name
        ORDER BY completions DESC, k.name
        """,
        (since, since, until, until),
    ).fetchall()

def on_time_rate(con, since=None, until=None):
    """On-time percentage by category, over tasks that actually had a due date."""
    return con.execute(
        """
        SELECT c.category,
               count(*)                                                  AS evaluated,
               count(*) FILTER (WHERE date(c.completed_at) <= t.due_date) AS on_time,
               round(100.0 * count(*) FILTER (WHERE date(c.completed_at) <= t.due_date)
                     / count(*), 1)                                      AS pct
        FROM completions c
        JOIN tasks t ON t.id = c.task_id
        WHERE t.due_date IS NOT NULL
          AND (? IS NULL OR date(c.completed_at) >= ?)
          AND (? IS NULL OR date(c.completed_at) <= ?)
        GROUP BY c.category
        ORDER BY pct DESC, c.category
        """,
        (since, since, until, until),
    ).fetchall()

def flagged_work(con, since=None, until=None):
    """Flagged completions in category order, with task and project context."""
    return con.execute(
        """
        SELECT c.id,
               c.completed_at,
               c.category,
               c.outcome,
               c.measure,
               c.quantity,
               t.title AS task_title,
               p.name  AS project_name
        FROM completions c
        LEFT JOIN tasks t    ON t.id = c.task_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE c.flagged = 1
          AND (? IS NULL OR date(c.completed_at) >= ?)
          AND (? IS NULL OR date(c.completed_at) <= ?)
        ORDER BY c.category, c.completed_at
        """,
        (since, since, until, until),
    ).fetchall()