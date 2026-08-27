"""Generate data/demo.db with invented data.

Everything in this file is fictional. It exists so the queries and the CLI
can be exercised against realistic-looking data, and so screenshots never
come from the real database.

Dates are computed relative to today, so `today` and `upcoming` always have
something to show no matter when this is run.
"""

import datetime as dt

from saga.db import ROOT, connect, init_db
from saga import writes

DEMO_PATH = ROOT / "data" / "demo.db"
TODAY = dt.date.today()

def day(offset):
    """An ISO date `offset` days from today. Negative is the past."""
    return (TODAY + dt.timedelta(days=offset)).isoformat()

def stamp(offset, hour=9):
    """An ISO timestamp `offset` days from today, at `hour` o'clock."""
    when = dt.datetime.combine(TODAY + dt.timedelta(days=offset), dt.time(hour))
    return when.strftime("%Y-%m-%d %H:%M:%S")

MEASURES = ["personnel onboarded", "packages reviewed", "hours saved",
            "inspections passed"]

PROJECTS = [
    ("Training Records Migration", "Move paper records to the tracker", -60, 3),
    ("Equipment Inventory Reconciliation", "Reconcile the annual count", -30, 12),
    ("Annual Certification Cycle", "Renewals for the whole section", -90, 45),
    ("Continuous Program Oversight", "Ongoing, no end date", -400, None),
]

OPEN_TASKS = [
    ("Chase two outstanding renewals", "certs", -6, 0),
    ("Draft the migration checklist", "work", -2, 1),
    ("Reconcile bay 3 count sheet", "work", 0, 2),
    ("Submit the weekly roll-up", "work", 0, None),
    ("Read chapter 7", "school", 1, None),
    ("Sort the storage room", "personal", 4, None),
    ("Book the annual physical", "home", None, None),
    ("Verify tracker import totals", "work", 2, 1),
]

HIGHLIGHTS = [
    (-380, "work",   "Ran the spring onboarding cycle", "personnel onboarded", 6),
    (-300, "work",   "Ran the summer onboarding cycle", "personnel onboarded", 9),
    (-150, "work",   "Ran the autumn onboarding cycle", "personnel onboarded", 7),
    (-240, "work",   "Automated the weekly roll-up",    "hours saved",         12),
    (-70,  "work",   "Automated the inventory export",  "hours saved",         8),
    (-200, "certs",  "Cleared the renewal backlog",     "packages reviewed",   64),
    (-45,  "certs",  "Passed the annual programme audit","inspections passed", 1),
    (-20,  "school", "Finished the security course",    None,                  None),
    (-5,   "work",   "Fixed the early-morning outage",  "hours saved",         3),
]

ROUTINE = [
    ("Monthly package review",   "certs",    "packages reviewed",  18),
    ("Weekly roll-up submitted", "work",     None,                 None),
    ("Bay inventory spot check", "work",     "inspections passed", 1),
    ("Weekly coursework",        "school",   None,                 None),
    ("Study block",              "personal", None,                 None),
    ("Household admin",          "home",     None,                 None),
]

def seed(path=DEMO_PATH):
    """Build a fresh demo database. Overwrites any existing one."""
    path.unlink(missing_ok=True)
    init_db(path)
    con = connect(path)

    for name in MEASURES:
        writes.add_measure(con, name)

    project_ids = []
    for name, desc, start, deadline in PROJECTS:
        project_ids.append(writes.add_project(
            con, name, description=desc, start_date=day(start),
            deadline=None if deadline is None else day(deadline)))

    for title, category, due, proj in OPEN_TASKS:
        writes.add_task(
            con, title, category,
            project_id=None if proj is None else project_ids[proj],
            due_date=None if due is None else day(due))

    for offset, category, outcome, measure, qty in HIGHLIGHTS:
        writes.add_completion(con, category, outcome=outcome, measure=measure,
                              quantity=qty, flagged=True,
                              completed_at=stamp(offset, 14))

    # Routine work: one closed task per fortnight, cycling through the pool.
    for i in range(52):
        title, category, measure, qty = ROUTINE[i % len(ROUTINE)]
        due = -420 + (i * 8)
        tid = writes.add_task(con, f"{title} #{i + 1}", category,
                              due_date=day(due))
        late = 3 if i % 5 == 0 else -1
        writes.complete_task(con, tid, outcome=title, measure=measure,
                             quantity=qty, completed_at=stamp(due + late, 11))

    con.close()
    return path

if __name__ == "__main__":
    print(f"Wrote {seed()}")