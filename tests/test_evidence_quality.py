import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, export, writes


class EvidenceQualityTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / "evidence.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        writes.add_measure(self.con, "units")
        writes.add_duty(self.con, "Operations")
        writes.add_duty(self.con, "Other")

    def capture(self, **kwargs):
        fields = {"category": "work", "outcome": "Invented result", "measure": "units",
                  "quantity": 1, "duty": "Operations", "completed_at": "2020-02-29 12:00:00"}
        fields.update(kwargs)
        return writes.add_completion(self.con, **fields)

    def test_nonfinite_capture_and_correction_leave_archive_unchanged(self):
        original = self.capture()
        before = list(self.con.iterdump())
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite number"):
                    self.capture(quantity=value)
                with self.assertRaisesRegex(ValueError, "finite number"):
                    writes.correct_completion(self.con, original, "Invalid number", quantity=value)
                self.assertEqual(list(self.con.iterdump()), before)

    def test_nonfinite_completion_does_not_close_or_advance_recurrence(self):
        task = writes.add_task(self.con, "Invented task", "work", due_date="2020-01-01",
                               repeat="monthly")
        before = list(self.con.iterdump())
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite number"):
                writes.complete_task(self.con, task, measure="units", quantity=value)
            self.assertEqual(list(self.con.iterdump()), before)

    def test_zero_negative_and_fractional_quantities_remain_valid(self):
        for value in (0, -4, 2.5):
            with self.subTest(value=value):
                original = self.capture(quantity=value)
                row = self.con.execute("SELECT quantity FROM completions WHERE id=?", (original,)).fetchone()
                self.assertEqual(row[0], value)
                revision = writes.correct_completion(self.con, original, "Adjusted", quantity=value - 1)
                row = self.con.execute("SELECT quantity FROM completions WHERE id=?", (revision,)).fetchone()
                self.assertEqual(row[0], value - 1)
        self.assertEqual(analytics.evidence_gaps(self.con), [])

    def test_cli_rejects_invalid_quantities_before_refresh_or_writes(self):
        original = self.capture()
        task = writes.add_task(self.con, "Invented task", "work")
        before = list(self.con.iterdump())
        for value in ("nan", "inf", "-inf", "1e999"):
            commands = [
                ["log", "Invented", "-c", "work", "--measure", "units"],
                ["done", str(task), "--measure", "units"],
                ["correct", str(original), "--reason", "Adjusted"],
            ]
            for command in commands:
                with (self.subTest(value=value, command=command),
                      contextlib.redirect_stderr(io.StringIO()) as errors,
                      patch.object(cli, "refresh_if_stale", side_effect=AssertionError),
                      self.assertRaises(SystemExit) as exited):
                    cli.main(["--db", str(self.path), *command, f"--quantity={value}"])
                self.assertEqual(exited.exception.code, 2)
                self.assertIn("finite number", errors.getvalue())
                self.assertEqual(list(self.con.iterdump()), before)

    def test_guided_measure_reprompts_for_nonfinite_numbers(self):
        with (patch.object(cli, "ask_choice", return_value="units"),
              patch.object(cli, "ask", side_effect=["nan", "inf", "-inf", "-2.5"]),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(cli.ask_measure(self.con), ("units", -2.5))
        self.assertEqual(output.getvalue().count("Enter a finite number"), 3)

    def test_evidence_scope_and_missing_fields(self):
        missing = self.capture(outcome=" \t\n", duty=None, measure=None, quantity=None)
        self.capture(category="home", outcome=None, duty=None, measure=None, quantity=None)
        flagged = self.capture(category="home", flagged=True, duty=None)
        self.capture(quantity=0)
        before = list(self.con.iterdump())
        rows = {row["id"]: row for row in analytics.evidence_gaps(self.con)}
        self.assertEqual(set(rows), {missing, flagged})
        self.assertEqual(rows[missing]["reasons"], ["missing outcome", "missing duty", "missing measurement"])
        self.assertEqual(rows[flagged]["reasons"], ["missing duty"])
        self.assertEqual(list(self.con.iterdump()), before)

    def test_evidence_filters_current_revision_before_date_and_duty(self):
        original = self.capture(outcome=None)
        revision = writes.correct_completion(self.con, original, "Updated context", outcome="Delivered",
                                             measure=None, quantity=None, duty="Other",
                                             completed_at="2020-03-01 00:00:00")
        self.assertEqual(analytics.evidence_gaps(self.con, "2020-02-29", "2020-02-29"), [])
        self.assertEqual(analytics.evidence_gaps(self.con, duty="Operations"), [])
        rows = analytics.evidence_gaps(self.con, "2020-03-01", "2020-03-01", "Other")
        self.assertEqual([row["id"] for row in rows], [revision])
        self.assertEqual(rows[0]["reasons"], ["missing measurement"])

    def legacy_infinity(self):
        with self.con:
            return self.con.execute(
                """INSERT INTO completions(category, outcome, measure, quantity, review_counting)
                   VALUES ('work', 'Legacy result', 'units', ?, 1)""", (float("inf"),)
            ).lastrowid

    def test_legacy_invalid_evidence_can_be_inspected_and_corrected_without_refresh(self):
        original = self.legacy_infinity()
        output_dir = self.path.parent / "exports"
        writer = export.write_all
        with (patch.object(db, "DB_PATH", self.path),
              patch.object(cli, "refresh_if_stale", side_effect=AssertionError),
              patch.object(export, "write_all", side_effect=lambda con: writer(con, output_dir)),
              contextlib.redirect_stdout(io.StringIO()) as output):
            status = cli.main(["--db", str(self.path), "review", "--check-evidence"])
            self.assertEqual(status, 0)
            self.assertIn("invalid quantity (not finite)", output.getvalue())
            self.assertIn("Legacy result", output.getvalue())
            self.assertFalse(output_dir.exists())
            status = cli.main(["--db", str(self.path), "correct", str(original),
                               "--reason", "Recorded finite count", "--quantity", "3"])
            self.assertEqual(status, 0)
        self.assertTrue((output_dir / "manifest.json").exists())
        self.assertEqual(self.con.execute("SELECT quantity FROM current_completions").fetchone()[0], 3)
        self.assertEqual(self.con.execute("SELECT count(*) FROM completions").fetchone()[0], 2)

    def test_legacy_invalid_quantity_must_be_fixed_or_cleared_during_correction(self):
        original = self.legacy_infinity()
        with self.assertRaisesRegex(ValueError, "finite number"):
            writes.correct_completion(self.con, original, "New text", outcome="Changed")
        revision = writes.correct_completion(self.con, original, "Remove unknown count",
                                             measure=None, quantity=None)
        row = self.con.execute("SELECT quantity FROM completions WHERE id=?", (revision,)).fetchone()
        self.assertIsNone(row[0])

    def test_evidence_cli_empty_and_unknown_duty(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["--db", str(self.path), "review", "--check-evidence"]), 0)
        self.assertIn("No evidence gaps", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            status = cli.main(["--db", str(self.path), "review", "--check-evidence", "--duty", "Missing"])
        self.assertEqual(status, 1)
        self.assertIn("duty", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
