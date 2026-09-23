import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saga import db, export, writes


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.output = Path(self.workspace.name)
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_exports_keep_unicode_records_and_review_period(self):
        # Pin query date arguments as well as the Python clock used by exports.
        from saga import reads

        day = dt.date(2020, 2, 29)
        for offset, title in ((-1, "Overdue"), (0, "Due today"), (1, "Café task")):
            writes.add_task(self.con, title, "work", due_date=(day + dt.timedelta(days=offset)).isoformat())
        writes.add_completion(self.con, "work", outcome="Résumé reviewed", flagged=True,
                              completed_at="2020-02-29 23:59:59")
        writes.add_completion(self.con, "work", outcome="Outside period", flagged=True,
                              completed_at="2020-03-01 00:00:00")
        queries = {name: getattr(reads, name) for name in
                   ("overdue", "due_today", "due_soon", "upcoming_deadlines")}
        with patch.object(export.dt, "date", wraps=dt.date) as clock, \
                patch.object(reads, "overdue", side_effect=lambda con: queries["overdue"](con, on=day.isoformat())), \
                patch.object(reads, "due_today", side_effect=lambda con: queries["due_today"](con, on=day.isoformat())), \
                patch.object(reads, "due_soon", side_effect=lambda con, days: queries["due_soon"](con, days, on=day.isoformat())), \
                patch.object(reads, "upcoming_deadlines", side_effect=lambda con, days: queries["upcoming_deadlines"](con, days, on=day.isoformat())):
            clock.today.return_value = day
            files = export.write_all(self.con, self.output, since=day.isoformat(), until=day.isoformat())
        self.assertEqual({p.name for p in files}, {"brief.json", "brief.md", "review.json"})
        brief = json.loads((self.output / "brief.json").read_text(encoding="utf-8"))
        review = json.loads((self.output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(brief["schema_version"], 1)
        self.assertEqual(brief["date"], "2020-02-29")
        for section, title in (("overdue", "Overdue"), ("due_today", "Due today"), ("due_soon", "Café task")):
            self.assertEqual([r["title"] for r in brief[section]], [title])
        self.assertEqual(review["schema_version"], 2)
        self.assertEqual((review["since"], review["until"]), ("2020-02-29", "2020-02-29"))
        self.assertEqual(review["volume"]["completions"], 1)
        self.assertEqual([r["outcome"] for r in review["flagged"]], ["Résumé reviewed"])
        markdown = (self.output / "brief.md").read_text(encoding="utf-8")
        for text in ("# Brief - 2020-02-29", "## Overdue", "## Due today", "## Coming up", "Café task"):
            self.assertIn(text, markdown)

    def test_empty_export_is_valid_and_has_quiet_day_message(self):
        export.write_all(self.con, self.output)
        review = json.loads((self.output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["volume"]["completions"], 0)
        self.assertEqual(review["flagged"], [])
        self.assertIn("Nothing due and no deadlines ahead.",
                      (self.output / "brief.md").read_text(encoding="utf-8"))

    def test_freshness_uses_content_date_not_modification_time(self):
        path = self.output / "brief.json"
        self.assertTrue(export.is_stale(self.output))
        for content in ("{broken", "{}", '{"date":"2020-02-28"}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with patch.object(export.dt, "date", wraps=dt.date) as clock:
                    clock.today.return_value = dt.date(2020, 2, 29)
                    self.assertTrue(export.is_stale(self.output))
        path.write_text('{"date":"2020-02-29"}', encoding="utf-8")
        with patch.object(export.dt, "date", wraps=dt.date) as clock:
            clock.today.return_value = dt.date(2020, 2, 29)
            self.assertFalse(export.is_stale(self.output))
