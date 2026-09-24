import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import cli, db, export, guided, reads, writes


class GuidedTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / "guided.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def run_menu(self, answers):
        with (patch.object(cli.sys.stdin, "isatty", return_value=True),
              patch("builtins.input", side_effect=answers),
              contextlib.redirect_stdout(io.StringIO()) as output):
            status = cli.main(["--db", str(self.path)])
        return status, output.getvalue()

    def task(self, title="Invented task", **kwargs):
        return writes.add_task(self.con, title, "work", **kwargs)

    def test_terminal_startup_and_noninteractive_help(self):
        status, output = self.run_menu(["0"])
        self.assertEqual(status, 0)
        self.assertIn("Main menu", output)
        self.assertEqual(output.count("0. Exit"), 1)
        self.assertNotIn("Exit Saga", output)
        self.assertNotIn("7. Exit", output)
        with (patch.object(cli.sys.stdin, "isatty", return_value=False),
              patch("builtins.input", side_effect=AssertionError("must not prompt")),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(cli.main(["--db", str(self.path)]), 1)
        self.assertIn("usage:", output.getvalue())

    def test_missing_database_requires_confirmation(self):
        missing = self.path.parent / "new.db"
        with (patch.object(cli.sys.stdin, "isatty", return_value=True),
              patch("builtins.input", side_effect=["0"]),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(guided.run(missing), 0)
        self.assertFalse(missing.exists())
        with (patch.object(cli.sys.stdin, "isatty", return_value=True),
              patch("builtins.input", side_effect=["1", "0"]),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(guided.run(missing), 0)
        self.assertTrue(missing.exists())

    def test_empty_task_list_returns_to_menu(self):
        status, output = self.run_menu(["1", "0", "0"])
        self.assertEqual(status, 0)
        self.assertIn("No matching tasks", output)

    def test_task_action_return_goes_to_task_list_without_changes(self):
        self.task()
        before = list(self.con.iterdump())
        status, output = self.run_menu(["1", "1", "0", "0", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(output.count("TASKS —"), 2)
        self.assertIn("0. Return", output)
        self.assertNotIn("Cancel / return", output)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_back_at_first_question_does_not_discard_draft(self):
        status, output = self.run_menu(["2", ":back", "Invented", "4", "1", "1", "3", "1", "1", "0"])
        self.assertEqual(status, 0)
        self.assertIn("This is the first question", output)
        self.assertEqual(self.con.execute("SELECT title FROM tasks").fetchone()[0], "Invented")

    def test_return_from_edit_picker_preserves_completed_draft(self):
        status, output = self.run_menu(["2", "Invented", "4", "1", "1", "3", "1", "2", "0", "1", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(self.con.execute("SELECT title FROM tasks").fetchone()[0], "Invented")
        self.assertIn("Role or responsibility", output)
        self.assertIn("Which ongoing responsibility", output)
        self.assertIn("specific effort with an end goal", output)

    def test_explicit_discard_does_not_register_draft_references(self):
        before = list(self.con.iterdump())
        status, _ = self.run_menu(["2", "Invented", "4", "2", "New role", ":discard", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_add_task_commits_only_after_preview_with_references(self):
        # Category work is fourth; reference choice 2 registers a pending name.
        answers = iter(["2", "Invented task", "4", "2", "Operations", "2", "Invented project",
                        "3", "1", "1", "0"])
        def respond(prompt):
            value = next(answers)
            if value == "1" and prompt == "Choose: ":
                # No references can appear before the first successful save.
                self.assertEqual(self.con.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
            return value
        status, output = self.run_menu(respond)
        self.assertEqual(status, 0)
        row = self.con.execute("SELECT * FROM tasks").fetchone()
        self.assertEqual((row["title"], row["category"], row["duty"]), ("Invented task", "work", "Operations"))
        self.assertIsNotNone(row["project_id"])
        self.assertIsNone(row["due_date"])
        self.assertIn("REVIEW BEFORE SAVING", output)

    def test_cancel_draft_discards_new_duty_project_and_task(self):
        before = list(self.con.iterdump())
        status, _ = self.run_menu(["2", "Invented", "4", "2", "New duty", "2", "New project",
                                   "3", "1", "3", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_cancel_capture_discards_new_measure_and_duty(self):
        before = list(self.con.iterdump())
        status, _ = self.run_menu(["4", "4", "Invented result", "1", "2", "New duty",
                                   "2", "New unit", "0", "2", "3", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_capture_zero_and_explicit_no_and_invalid_answers(self):
        status, output = self.run_menu(["4", "", "99", "4", "", "Invented result", "1",
                                       "1", "2", "units", "nan", "inf", "0", "maybe", "2", "1", "0"])
        self.assertEqual(status, 0)
        row = self.con.execute("SELECT * FROM completions").fetchone()
        self.assertEqual((row["quantity"], row["flagged"], row["duty"]), (0, 0, None))
        self.assertIn("Please answer explicitly", output)
        self.assertIn("finite number", output)

    def test_edit_one_answer_returns_to_preview_and_saves_latest_value(self):
        status, output = self.run_menu(["2", "Old title", "4", "1", "1", "3", "1",
                                       "2", "1", "New title", "1", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(self.con.execute("SELECT title FROM tasks").fetchone()[0], "New title")
        self.assertEqual(output.count("REVIEW BEFORE SAVING"), 2)

    def test_back_revisits_question_without_writing(self):
        status, _ = self.run_menu(["2", "Old title", ":back", "New title", "4", "1", "1",
                                   "3", "1", "1", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(self.con.execute("SELECT title FROM tasks").fetchone()[0], "New title")

    def test_end_of_input_discards_draft(self):
        before = list(self.con.iterdump())
        status, output = self.run_menu(["2", "Invented", "4", "2", "Pending duty", EOFError])
        self.assertEqual(status, 130)
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertIn("discarded", output)

    def test_picker_pagination_uses_row_number_not_database_id(self):
        ids = [self.task(f"Invented task {i}") for i in range(10)]
        writes.cancel_task(self.con, ids[0])
        with (patch("builtins.input", side_effect=["n", "1"]),
              contextlib.redirect_stdout(io.StringIO()) as output):
            selected = guided.task_picker(self.con)
        self.assertEqual(selected["id"], ids[9])
        self.assertIn("page 2", output.getvalue())

    def test_picker_search_and_filters_distinguish_duplicate_titles(self):
        first = self.task("Same title", due_date="2020-01-01")
        project = writes.add_project(self.con, "Distinct project")
        second = self.task("Same title", project_id=project)
        with (patch("builtins.input", side_effect=["s", "Distinct project", "1"]),
              contextlib.redirect_stdout(io.StringIO()) as output):
            selected = guided.task_picker(self.con)
        self.assertEqual(selected["id"], second)
        self.assertIn("Distinct project", output.getvalue())
        self.assertEqual([r["id"] for r in reads.selectable_tasks(self.con, due_only=True, on="2020-01-01")], [first])
        self.assertEqual(reads.selectable_tasks(self.con, category="home"), [])

    def test_completion_selects_correct_task_and_confirms_context(self):
        first = self.task("Same title")
        second = self.task("Same title")
        status, output = self.run_menu(["3", "2", "1", "Finished second task", "1", "1", "1", "2", "1", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(reads.get_task(self.con, first)["status"], "open")
        self.assertEqual(reads.get_task(self.con, second)["status"], "done")
        self.assertIn("Confirm task context", output)

    def test_explicit_no_duty_does_not_inherit_task_duty(self):
        writes.add_duty(self.con, "Operations", "work")
        task = self.task(duty="Operations")
        status, _ = self.run_menu(["3", "1", "1", "Finished", "1", "2", "1", "1", "2", "1", "0"])
        self.assertEqual(status, 0)
        row = self.con.execute("SELECT duty FROM completions").fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(reads.get_task(self.con, task)["duty"], "Operations")

    def test_reschedule_and_cancel_selected_tasks(self):
        task = self.task(due_date="2020-01-01")
        status, _ = self.run_menu(["5", "1", "3", "1", "6", "1", "1", "1", "0"])
        self.assertEqual(status, 0)
        row = reads.get_task(self.con, task)
        self.assertIsNone(row["due_date"])
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(self.con.execute("SELECT count(*) FROM completions").fetchone()[0], 0)

    def test_recurring_task_completion_advances_once(self):
        task = self.task(due_date="2020-01-31", repeat="monthly")
        status, _ = self.run_menu(["3", "1", "1", "Finished", "1", "1", "1", "2", "1", "0"])
        self.assertEqual(status, 0)
        following = reads.open_occurrence(self.con, reads.get_task(self.con, task)["recurrence_id"])
        self.assertEqual(following["due_date"], "2020-02-29")
        self.assertEqual(self.con.execute("SELECT count(*) FROM completions").fetchone()[0], 1)

    def test_reference_registration_rolls_back_with_failed_task_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            writes.save_guided(self.con, "add", {"title": "Invented", "category": "missing"},
                               new_duty="New duty", new_project="New project")
        self.assertEqual(self.con.execute("SELECT count(*) FROM duties").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM projects").fetchone()[0], 0)
        self.assertFalse(self.con.in_transaction)

    def test_reference_registration_rolls_back_on_failed_completion(self):
        task = self.task(due_date="9999-12-31", repeat="daily")
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, "date range"):
            writes.save_guided(self.con, "done", {"task_id": task, "outcome": "Done", "quantity": 1},
                               new_duty="New duty", new_measure="New unit")
        self.assertEqual(list(self.con.iterdump()), before)

    def test_concurrently_changed_task_rejected_before_new_references(self):
        task = self.task()
        expected = dict(reads.get_task(self.con, task))
        writes.reschedule_task(self.con, task, "2020-01-01")
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(ValueError, "changed while"):
            writes.save_guided(self.con, "done", {"task_id": task, "outcome": "Done"},
                               new_duty="New duty", expected_task=expected)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_failed_export_does_not_offer_to_save_again(self):
        with (patch.object(db, "DB_PATH", self.path),
              patch.object(export, "write_all", side_effect=export.ExportError("blocked"))):
            status, output = self.run_menu(["2", "Invented", "4", "1", "1", "3", "1", "1", "0"])
        self.assertEqual(status, 0)
        self.assertIn("Retry export, not this action", output)
        self.assertEqual(self.con.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_export_query_failure_also_preserves_saved_state(self):
        with (patch.object(db, "DB_PATH", self.path),
              patch.object(export, "write_all", side_effect=sqlite3.OperationalError("read failed"))):
            status, output = self.run_menu(["2", "Invented", "4", "1", "1", "3", "1", "1", "0"])
        self.assertEqual(status, 0)
        self.assertNotIn("Could not save", output)
        self.assertIn("database change was saved", output)

    def test_invalid_recurrence_draft_can_be_fixed_without_partial_saves(self):
        # A repeating task needs a due date; edit only the due-date answer.
        status, output = self.run_menu(["2", "Invented", "4", "2", "Pending duty", "1", "3",
                                       "2", "1", "1", "2", "5", "1", "1", "0"])
        self.assertEqual(status, 0)
        self.assertIn("Could not save", output)
        self.assertEqual(self.con.execute("SELECT count(*) FROM duties").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM recurrences").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_invalid_date_reprompts_without_discarding_form(self):
        status, output = self.run_menu(["2", "Invented", "4", "1", "1", "2", "2020-02-30",
                                       "2020-02-29", "1", "1", "0"])
        self.assertEqual(status, 0)
        self.assertIn("valid date", output)
        self.assertEqual(self.con.execute("SELECT due_date FROM tasks").fetchone()[0], "2020-02-29")

    def test_task_details_use_names_and_readable_schedule(self):
        project = writes.add_project(self.con, "Invented project")
        self.task(project_id=project, due_date="2020-01-01", repeat="monthly", interval=2)
        status, output = self.run_menu(["1", "1", "3", "0"])
        self.assertEqual(status, 0)
        self.assertIn("Project: Invented project", output)
        self.assertIn("Repeat: Every 2 months (active)", output)
        self.assertNotIn("occurrence index", output)


if __name__ == "__main__":
    unittest.main()
