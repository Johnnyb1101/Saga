"""Aggregate queries over the completion archive.

Every function takes optional `since` and `until` dates in YYYY-MM-DD
form, both inclusive. Omit them to cover the whole archive.

completed_at is a timestamp, so comparisons wrap it in date(). Comparing
the raw timestamp against a bare date silently drops everything recorded
after midnight on the closing day.
"""

def measure_totals(con, since=None, until=None, duty=None):
    """Each measure with its summed quantity and how many occasions produced it."""
    return con.execute(
        """
        SELECT measure,
               sum(quantity) AS total,
               count(*)      AS occasions
        FROM current_completions
        WHERE measure IS NOT NULL
          AND (? IS NULL OR date(completed_at) >= ?)
          AND (? IS NULL OR date(completed_at) <= ?)
          AND (? IS NULL OR duty = ?)
        GROUP BY measure
        ORDER BY total DESC
        """,
        (since, since, until, until, duty, duty),
    ).fetchall()

def volume(con, since=None, until=None, duty=None):
    """Headline counts for a period. Returns a single row."""
    return con.execute(
        """
        SELECT count(*)                                            AS completions,
               count(*) FILTER (WHERE c.review_counting = 1)       AS review_counting,
               count(*) FILTER (WHERE c.flagged = 1)               AS flagged,
               count(c.quantity)                                   AS with_a_number
        FROM current_completions c
        WHERE (? IS NULL OR date(c.completed_at) >= ?)
          AND (? IS NULL OR date(c.completed_at) <= ?)
          AND (? IS NULL OR c.duty = ?)
        """,
        (since, since, until, until, duty, duty),
    ).fetchone()

def completions_by_category(con, since=None, until=None, duty=None):
    """Every category with its counts, including categories with none."""
    return con.execute(
        """
        SELECT k.name AS category,
               count(c.id)                              AS completions,
               count(c.quantity)                        AS with_a_number,
               count(c.id) FILTER (WHERE c.flagged = 1) AS flagged
        FROM categories k
        LEFT JOIN current_completions c
               ON c.category = k.name
              AND (? IS NULL OR date(c.completed_at) >= ?)
              AND (? IS NULL OR date(c.completed_at) <= ?)
              AND (? IS NULL OR c.duty = ?)
        GROUP BY k.name
        ORDER BY completions DESC, k.name
        """,
        (since, since, until, until, duty, duty),
    ).fetchall()

def on_time_rate(con, since=None, until=None, duty=None):
    """On-time percentage by category, using saved completion deadlines."""
    return con.execute(
        """
        SELECT c.category,
               count(*)                                                  AS evaluated,
               count(*) FILTER (WHERE date(c.completed_at) <= c.due_date) AS on_time,
               round(100.0 * count(*) FILTER (WHERE date(c.completed_at) <= c.due_date)
                     / count(*), 1)                                      AS pct
        FROM current_completions c
        WHERE c.due_date IS NOT NULL
          AND (? IS NULL OR date(c.completed_at) >= ?)
          AND (? IS NULL OR date(c.completed_at) <= ?)
          AND (? IS NULL OR c.duty = ?)
        GROUP BY c.category
        ORDER BY pct DESC, c.category
        """,
        (since, since, until, until, duty, duty),
    ).fetchall()

def flagged_work(con, since=None, until=None, duty=None):
    """Flagged completions in category order, with task and project context."""
    return con.execute(
        """
        SELECT c.id,
               c.completed_at,
               c.category,
               c.outcome,
               c.measure,
               c.quantity,
               c.duty,
               c.task_title,
               c.project_name,
               c.snapshot_source,
               c.supersedes_id
        FROM current_completions c
        WHERE c.flagged = 1
          AND (? IS NULL OR date(c.completed_at) >= ?)
          AND (? IS NULL OR date(c.completed_at) <= ?)
          AND (? IS NULL OR c.duty = ?)
        ORDER BY c.category, c.completed_at
        """,
        (since, since, until, until, duty, duty),
    ).fetchall()
