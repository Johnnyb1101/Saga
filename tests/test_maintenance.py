import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, export, reads, writes


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.path = self.root / "tasks.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def test_cancel_removes_task_from_views_without_completion(self):
        task = writes.add_task(self.con, "Invented task", "work", due_date="2020-01-01")
        writes.cancel_task(self.con, task)
        self.assertEqual(reads.get_task(self.con, task)["status"], "cancelled")
        self.assertEqual(reads.open_tasks(self.con), [])
        self.assertEqual(reads.due_today(self.con, on="2020-01-01"), [])
        self.assertEqual(reads.overdue(self.con, on="2020-01-02"), [])
        self.assertEqual(analytics.volume(self.con)["completions"], 0)
        with self.assertRaises(ValueError):
            writes.complete_task(self.con, task)

    def test_reschedule_and_clear_change_due_views(self):
        task = writes.add_task(self.con, "Invented task", "work", due_date="2020-02-28")
        writes.reschedule_task(self.con, task, "2020-02-29")
        self.assertEqual(reads.overdue(self.con, on="2020-02-29"), [])
        self.assertEqual(reads.due_today(self.con, on="2020-02-29")[0]["id"], task)
        writes.reschedule_task(self.con, task, None)
        self.assertEqual(reads.due_today(self.con, on="2020-02-29"), [])
        self.assertIsNone(reads.open_tasks(self.con)[0]["due_date"])

    def test_invalid_dates_and_closed_or_missing_tasks_are_unchanged(self):
        task = writes.add_task(self.con, "Invented task", "work", due_date="2020-01-01")
        for date in ("2020-02-30", "20200101", "2020-1-1", ""):
            with self.subTest(date=date), self.assertRaises(ValueError):
                writes.reschedule_task(self.con, task, date)
        self.assertEqual(reads.get_task(self.con, task)["due_date"], "2020-01-01")
        completed = writes.complete_task(self.con, task, outcome="Original", completed_at="2020-01-01 12:00:00")
        before = dict(reads.completion_history(self.con, completed)[0])
        cancelled = writes.add_task(self.con, "Cancelled task", "work")
        writes.cancel_task(self.con, cancelled)
        for identity in (task, cancelled, 9999):
            with self.subTest(identity=identity):
                with self.assertRaises(ValueError):
                    writes.cancel_task(self.con, identity)
                with self.assertRaises(ValueError):
                    writes.reschedule_task(self.con, identity, "2020-03-01")
        self.assertEqual(dict(reads.completion_history(self.con, completed)[0]), before)

    def test_project_closes_only_after_tasks_are_resolved(self):
        project = writes.add_project(self.con, "Invented project", deadline="2020-02-29")
        task = writes.add_task(self.con, "Outstanding", "work", project_id=project)
        with self.assertRaises(ValueError):
            writes.close_project(self.con, project)
        self.assertEqual(reads.projects(self.con)[0]["status"], "active")
        self.assertEqual(reads.get_task(self.con, task)["status"], "open")
        writes.cancel_task(self.con, task)
        completed = writes.add_task(self.con, "Finished", "work", project_id=project)
        writes.complete_task(self.con, completed, outcome="Delivered")
        before = [dict(r) for r in reads.completion_list(self.con)]
        writes.close_project(self.con, project)
        row = reads.projects(self.con)[0]
        self.assertEqual((row["status"], row["open_tasks"]), ("done", 0))
        self.assertEqual(reads.upcoming_deadlines(self.con, on="2020-02-28"), [])
        self.assertEqual([dict(r) for r in reads.completion_list(self.con)], before)
        with self.assertRaises(ValueError):
            writes.close_project(self.con, project)

    def test_projects_include_undated_and_all_statuses_with_correct_counts(self):
        for status in ("active", "on_hold", "done", "cancelled"):
            project = writes.add_project(self.con, status)
            self.con.execute("UPDATE projects SET status=? WHERE id=?", (status, project))
            self.con.commit()
            if status in ("done", "cancelled"):
                with self.assertRaises(ValueError):
                    writes.add_task(self.con, "Rejected", "work", project_id=project)
            else:
                writes.add_task(self.con, "Allowed", "work", project_id=project)
        rows = reads.projects(self.con)
        self.assertEqual({r["status"]: r["open_tasks"] for r in rows},
                         {"active": 1, "on_hold": 1, "done": 0, "cancelled": 0})
        self.assertTrue(all(r["deadline"] is None for r in rows))
        with self.assertRaises(ValueError):
            writes.add_task(self.con, "Rejected", "work", project_id=9999)
        with self.assertRaises(ValueError):
            writes.close_project(self.con, 9999)

    def test_empty_and_on_hold_projects_can_close(self):
        for status in ("active", "on_hold"):
            project = writes.add_project(self.con, "Empty")
            self.con.execute("UPDATE projects SET status=? WHERE id=?", (status, project))
            self.con.commit()
            writes.close_project(self.con, project)
        self.assertTrue(all(r["status"] == "done" for r in reads.projects(self.con)))

    def test_cli_mutations_refresh_exports_and_project_lookup(self):
        today = dt.date.today().isoformat()
        project = writes.add_project(self.con, "CLI project", deadline=today)
        task = writes.add_task(self.con, "CLI task", "work", project_id=project, due_date=today)
        output_dir = self.root / "exports"
        write_all = export.write_all

        def run(*args):
            with patch.object(db, "DB_PATH", self.path), \
                    patch.object(cli, "refresh_if_stale"), \
                    patch.object(export, "write_all", side_effect=lambda con: write_all(con, output_dir)) as refresh, \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()):
                status = cli.main(["--db", str(self.path), *args])
            return status, output.getvalue(), refresh.call_count

        self.assertEqual(run("reschedule", str(task), "--clear-due")[::2], (0, 1))
        self.assertEqual(json.loads((output_dir / "brief.json").read_text())["due_today"], [])
        self.assertEqual(run("reschedule", str(task), "--due", today)[::2], (0, 1))
        self.assertEqual(json.loads((output_dir / "brief.json").read_text())["due_today"][0]["id"], task)
        self.assertEqual(run("close-project", str(project))[::2], (1, 0))
        self.assertEqual(run("cancel", str(task))[::2], (0, 1))
        self.assertEqual(run("close-project", str(project))[::2], (0, 1))
        self.assertEqual(json.loads((output_dir / "brief.json").read_text())["upcoming"], [])
        status, output, refreshed = run("projects")
        self.assertEqual((status, refreshed), (0, 0))
        self.assertIn("CLI project", output)
        self.assertIn("done", output)
