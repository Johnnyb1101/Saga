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