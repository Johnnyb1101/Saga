import sqlite3
import unittest

from saga import analytics, db, reads, writes


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_failure_after_completion_insert_rolls_back_both_changes(self):
        task = writes.add_task(self.con, "Invented task", "work")
        self.con.executescript("""
            CREATE TRIGGER reject_close BEFORE UPDATE ON tasks
            BEGIN SELECT RAISE(ABORT, 'injected close failure'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected close failure"):
            writes.complete_task(self.con, task, outcome="Invented outcome")
        self.assertEqual(reads.get_task(self.con, task)["status"], "open")
        self.assertEqual(analytics.volume(self.con)["completions"], 0)

    def test_duplicate_completion_is_rejected(self):
        task = writes.add_task(self.con, "Invented task", "work")
        writes.complete_task(self.con, task, outcome="Original outcome")
        with self.assertRaisesRegex(ValueError, "already done"):
            writes.complete_task(self.con, task, outcome="Duplicate")
        self.assertEqual(analytics.volume(self.con)["completions"], 1)
        self.assertEqual(self.con.execute("SELECT outcome FROM completions").fetchone()[0],
                         "Original outcome")

    def test_due_windows_have_no_overlap_and_exclude_closed_tasks(self):
        ids = {}
        for title, due in (("late", "2020-02-28"), ("today", "2020-02-29"),
                           ("tomorrow", "2020-03-01"), ("edge", "2020-03-02"),
                           ("outside", "2020-03-03"), ("undated", None),
                           ("closed", "2020-02-29")):
            ids[title] = writes.add_task(self.con, title, "work", due_date=due)
        writes.complete_task(self.con, ids["closed"])
        self.assertEqual([r["id"] for r in reads.overdue(self.con, on="2020-02-29")], [ids["late"]])
        self.assertEqual([r["id"] for r in reads.due_today(self.con, on="2020-02-29")], [ids["today"]])
        self.assertEqual([r["id"] for r in reads.due_soon(self.con, days=2, on="2020-02-29")],
                         [ids["tomorrow"], ids["edge"]])
        self.assertEqual(reads.due_soon(self.con, days=0, on="2020-02-29"), [])

    def test_project_deadline_boundary_and_open_task_counts(self):
        edge = writes.add_project(self.con, "Edge", deadline="2020-03-02")
        writes.add_project(self.con, "Outside", deadline="2020-03-03")
        writes.add_project(self.con, "Undated")
        writes.add_task(self.con, "Open", "work", project_id=edge)
        closed = writes.add_task(self.con, "Closed", "work", project_id=edge)
        writes.complete_task(self.con, closed)
        rows = reads.upcoming_deadlines(self.con, days=2, on="2020-02-29")
        self.assertEqual([(r["id"], r["days_left"], r["open_tasks"]) for r in rows], [(edge, 2, 1)])

    def test_review_dates_and_duty_filter_apply_to_every_aggregate(self):
        writes.add_duty(self.con, "Operations", "work")
        writes.add_duty(self.con, "Training", "work")
        writes.add_measure(self.con, "packages")
        for stamp, duty, quantity in (
            ("2020-02-28 23:59:59", "Operations", 100),
            ("2020-02-29 00:00:00", "Operations", 2),
            ("2020-02-29 23:59:59", "Operations", 3),
            ("2020-03-01 00:00:00", "Operations", 100),
            ("2020-02-29 12:00:00", "Training", 100),
        ):
            task = writes.add_task(self.con, "Invented work", "work", due_date="2020-02-29", duty=duty)
            writes.complete_task(self.con, task, outcome="Invented outcome", measure="packages",
                                 quantity=quantity, flagged=True, completed_at=stamp)
        args = (self.con, "2020-02-29", "2020-02-29", "Operations")
        self.assertEqual(dict(analytics.volume(*args)),
                         {"completions": 2, "review_counting": 2, "flagged": 2, "with_a_number": 2})
        self.assertEqual(dict(analytics.measure_totals(*args)[0]),
                         {"measure": "packages", "total": 5, "occasions": 2})
        self.assertEqual(dict(analytics.on_time_rate(*args)[0]),
                         {"category": "work", "evaluated": 2, "on_time": 2, "pct": 100})
        categories = {r["category"]: r["completions"] for r in analytics.completions_by_category(*args)}
        self.assertEqual(categories, {"work": 2, "certs": 0, "school": 0, "home": 0, "personal": 0})
        self.assertEqual(len(analytics.flagged_work(*args)), 2)

    def test_empty_archive_returns_zero_counts_and_empty_details(self):
        self.assertEqual(dict(analytics.volume(self.con)),
                         {"completions": 0, "review_counting": 0, "flagged": 0, "with_a_number": 0})
        for query in (analytics.measure_totals, analytics.on_time_rate, analytics.flagged_work,
                      reads.overdue, reads.due_today, reads.due_soon, reads.upcoming_deadlines):
            with self.subTest(query=query.__name__):
                self.assertEqual(query(self.con), [])
