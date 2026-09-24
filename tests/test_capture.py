import contextlib
import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import analytics, cli, db, writes


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.path = Path(self.workspace.name) / "capture.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        writes.add_duty(self.con, "Operations", "work")
        writes.add_measure(self.con, "packages")

    def run_cli(self, *args):
        connect = db.connect

        def tracked_connect(*values, **kwargs):
            con = connect(*values, **kwargs)
            self.addCleanup(con.close)
            return con

        with contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors, \
                patch.object(db, "connect", side_effect=tracked_connect):
            status = cli.main(["--db", str(self.path), *args])
        return status, output.getvalue(), errors.getvalue()

    def test_standalone_backdated_record_and_review(self):
        with patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            status, _, errors = self.run_cli(
                "log", "Resolved backlog", "-c", "work", "--date", "2020-02-29",
                "--duty", "Operations", "--measure", "packages", "--quantity", "4", "--flag")
        self.assertEqual(status, 0, errors)
        row = self.con.execute("SELECT * FROM completions").fetchone()
        self.assertIsNone(row["task_id"])
        self.assertEqual(row["completed_at"], "2020-02-29 00:00:00")
        self.assertEqual((row["duty"], row["quantity"], row["flagged"]), ("Operations", 4, 1))
        self.assertEqual(self.con.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(analytics.volume(self.con, "2020-02-29", "2020-02-29")["completions"], 1)
        self.assertEqual(analytics.volume(self.con, "2020-03-01")["completions"], 0)
        status, output, _ = self.run_cli("review", "--since", "2020-02-29", "--until", "2020-02-29")
        self.assertEqual(status, 0)
        self.assertIn("Resolved backlog", output)

    def test_done_backdates_and_keeps_task_duty(self):
        task = writes.add_task(self.con, "Invented task", "work", duty="Operations")
        status, _, errors = self.run_cli("done", str(task), "--outcome", "Delivered", "--date", "2020-01-01")
        self.assertEqual(status, 0, errors)
        row = self.con.execute("SELECT * FROM completions").fetchone()
        self.assertEqual((row["task_id"], row["duty"], row["completed_at"]),
                         (task, "Operations", "2020-01-01 00:00:00"))
        self.assertEqual(self.con.execute("SELECT status FROM tasks").fetchone()[0], "done")

    def test_invalid_dates_do_not_complete_or_log(self):
        task = writes.add_task(self.con, "Invented task", "work")
        future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        for value in ("2020-02-30", "20200101", "2020-1-1", "", future):
            for command in (("log", "Outcome", "-c", "work"),
                            ("done", str(task), "--outcome", "Outcome")):
                with self.subTest(date=value, command=command):
                    self.assertEqual(self.run_cli(*command, "--date", value)[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM completions").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT status FROM tasks").fetchone()[0], "open")

    def test_guided_entry(self):
        # Category work=4; packages=1; Operations=1.
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=["Invented outcome", "4", "2020-01-01", "1", "3", "1", "y"]):
            status, _, errors = self.run_cli("log")
        self.assertEqual(status, 0, errors)
        row = self.con.execute("SELECT * FROM completions").fetchone()
        self.assertEqual((row["category"], row["outcome"], row["quantity"], row["duty"]),
                         ("work", "Invented outcome", 3, "Operations"))

    def test_default_timestamp_is_current(self):
        before = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(self.run_cli("log", "Outcome", "-c", "work")[0], 0)
        stamp = self.con.execute("SELECT completed_at FROM completions").fetchone()[0]
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def test_bad_inputs_leave_no_completion(self):
        for args in (("log", " ", "-c", "work"), ("log", "Outcome", "-c", "missing"),
                     ("log", "Outcome", "-c", "work", "--measure", "packages")):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(*args)[0], 1)
        with patch.object(cli.sys.stdin, "isatty", return_value=False):
            self.assertEqual(self.run_cli("log")[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM completions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
