"""Aggregate queries over the completion archive.

Every function takes optional `since` and `until` dates in YYYY-MM-DD
form, both inclusive. Omit them to cover the whole archive.

completed_at is a timestamp, so comparisons wrap it in date(). Comparing
the raw timestamp against a bare date silently drops everything recorded
after midnight on the closing day.
"""

import math

from saga import reads


def evidence_gaps(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """Review prompts for current, review-relevant evidence; never change records."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
    rows = con.execute(
        """SELECT id, completed_at, outcome, task_title, duty, measure, quantity, measurement_status
           FROM current_completions
           WHERE (review_counting = 1 OR flagged = 1)
             AND (? IS NULL OR date(completed_at) >= ?)
             AND (? IS NULL OR date(completed_at) <= ?)
             AND (? IS NULL OR duty = ?)
             AND (? IS NULL OR category = ?) AND (? = 0 OR duty IS NULL)
           ORDER BY completed_at DESC, id DESC""",
        (since, since, until, until, duty, duty, category, category, int(unassigned)),
    ).fetchall()
    gaps = []
    for row in rows:
        reasons = []
        if not row["outcome"] or not row["outcome"].strip():
            reasons.append("missing outcome")
        if row["duty"] is None:
            reasons.append("missing duty")
        if row["measure"] is None or row["quantity"] is None:
            if row['measurement_status'] == 'unknown':
                reasons.append("measurement follow-up pending")
            elif row['measurement_status'] != 'not_applicable':
                reasons.append("measurement unspecified")
        elif not math.isfinite(row["quantity"]):
            reasons.append("invalid quantity (not finite)")
        if reasons:
            gaps.append({**dict(row), "reasons": reasons})
    return gaps


def measure_totals(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """Each measure with its summed quantity and how many occasions produced it."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
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
          AND (? IS NULL OR category = ?) AND (? = 0 OR duty IS NULL)
        GROUP BY measure
        ORDER BY total DESC
        """,
        (since, since, until, until, duty, duty, category, category, int(unassigned)),
    ).fetchall()

def volume(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """Headline counts for a period. Returns a single row."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
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
          AND (? IS NULL OR c.category = ?) AND (? = 0 OR c.duty IS NULL)
        """,
        (since, since, until, until, duty, duty, category, category, int(unassigned)),
    ).fetchone()

def completions_by_category(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """Every category with its counts, including categories with none."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
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
              AND (? IS NULL OR c.category = ?) AND (? = 0 OR c.duty IS NULL)
        WHERE (? IS NULL OR k.name = ?)
        GROUP BY k.name
        ORDER BY completions DESC, k.name
        """,
        (since, since, until, until, duty, duty, category, category, int(unassigned), category, category),
    ).fetchall()

def on_time_rate(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """On-time percentage by category, using saved completion deadlines."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
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
          AND (? IS NULL OR c.category = ?) AND (? = 0 OR c.duty IS NULL)
        GROUP BY c.category
        ORDER BY pct DESC, c.category
        """,
        (since, since, until, until, duty, duty, category, category, int(unassigned)),
    ).fetchall()

def flagged_work(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """Flagged completions in category order, with task and project context."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
    return con.execute(
        """
        SELECT c.id,
               c.completed_at,
               c.category,
               c.outcome,
               c.measure,
               c.quantity,
               c.measurement_status,
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
          AND (? IS NULL OR c.category = ?) AND (? = 0 OR c.duty IS NULL)
        ORDER BY c.category, c.completed_at
        """,
        (since, since, until, until, duty, duty, category, category, int(unassigned)),
    ).fetchall()


def measurement_states(con, since=None, until=None, duty=None, category=None, unassigned=False):
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
    rows = con.execute(
        """SELECT measurement_status, count(*) AS count FROM current_completions
           WHERE (? IS NULL OR date(completed_at)>=?) AND (? IS NULL OR date(completed_at)<=?)
             AND (? IS NULL OR duty=?)
             AND (? IS NULL OR category=?) AND (?=0 OR duty IS NULL) GROUP BY measurement_status""",
        (since, since, until, until, duty, duty, category, category, int(unassigned)),
    ).fetchall()
    counts = dict.fromkeys(('measured', 'not_applicable', 'unknown', 'unspecified'), 0)
    counts.update({r['measurement_status']: r['count'] for r in rows})
    return counts


def category_role_breakdown(con, since=None, until=None, duty=None, category=None, unassigned=False):
    """Disjoint groups from current snapshots, including historical and empty roles."""
    reads.validate_review_scope(con, since, until, duty, category, unassigned)
    scope = """FROM current_completions
               WHERE (? IS NULL OR date(completed_at) >= ?)
                 AND (? IS NULL OR date(completed_at) <= ?)
                 AND (? IS NULL OR duty = ?)
                 AND (? IS NULL OR category = ?)
                 AND (? = 0 OR duty IS NULL)"""
    params = (since, since, until, until, duty, duty, category, category, int(unassigned))
    rows = con.execute(
        """SELECT category, duty, count(*) AS completions,
                  count(*) FILTER (WHERE review_counting = 1) AS review_counting,
                  count(*) FILTER (WHERE flagged = 1) AS flagged,
                  count(quantity) AS with_a_number,
                  count(*) FILTER (WHERE measurement_status = 'measured') AS measured,
                  count(*) FILTER (WHERE measurement_status = 'not_applicable') AS not_applicable,
                  count(*) FILTER (WHERE measurement_status = 'unknown') AS unknown,
                  count(*) FILTER (WHERE measurement_status = 'unspecified') AS unspecified,
                  count(due_date) AS evaluated,
                  count(*) FILTER (WHERE date(completed_at) <= due_date) AS on_time,
                  round(100.0 * count(*) FILTER (WHERE date(completed_at) <= due_date)
                        / nullif(count(due_date), 0), 1) AS pct
        """ + scope + " GROUP BY category, duty ORDER BY category, duty", params,
    ).fetchall()
    groups = {}
    for row in rows:
        group = dict(row)
        group['measurement_states'] = {state: group.pop(state) for state in
                                      ('measured', 'not_applicable', 'unknown', 'unspecified')}
        group['measures'] = []
        groups[(row['category'], row['duty'])] = group
    measures = con.execute(
        "SELECT category, duty, measure, sum(quantity) AS total, count(*) AS occasions "
        + scope + " AND measure IS NOT NULL GROUP BY category, duty, measure ORDER BY category, duty, measure",
        params,
    ).fetchall()
    for row in measures:
        groups[(row['category'], row['duty'])]['measures'].append(
            {key: row[key] for key in ('measure', 'total', 'occasions')})
    return list(groups.values())
