"""History reports display one read-only snapshot for the chosen scope."""

import os
import unittest
from importlib.util import find_spec
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None

if HAS_QT:
    from PySide6.QtCore import QDate, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit

    from saga.desktop.history import HistoryWidget


class FakeService:
    def __init__(self):
        self.filter_calls = 0
        self.report_calls = []
        self.fail = None
        self.empty = False

    def history_filters(self):
        self.filter_calls += 1
        return {"categories": ["Archived <work>", "Personal"], "roles": ["Retired <lead>"]}

    def history_report(self, **scope):
        self.report_calls.append(scope)
        if self.fail is not None:
            raise self.fail
        report = {
            **scope,
            "summary": {"completions": 1, "with_a_number": 1, "flagged": 1, "review_counting": 0},
            "measures": [
                {"measure": "hours", "total": 3, "occasions": 1},
                {"measure": "people", "total": 5, "occasions": 1},
            ],
            "categories": [{"category": "Archived <work>", "completions": 1,
                            "with_a_number": 1, "flagged": 1}],
            "accomplishments": [{
                "id": 42, "completed_at": "2024-04-30 09:15:00", "outcome": "<b>Helped people</b>",
                "task_title": "<Task>", "category": "Archived <work>", "duty": "Retired <lead>",
                "quantity": 5, "measure": "people", "flagged": 1, "review_counting": 0,
                "measurement_status": "measured", "due_date": "2024-04-29", "project_name": "<Project>",
            }],
        }
        if self.empty:
            report["summary"] = {key: 0 for key in report["summary"]}
            report["measures"] = []
            report["categories"] = []
            report["accomplishments"] = []
        return report


@unittest.skipUnless(HAS_QT, "Optional PySide6 desktop dependency is not installed")
class HistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = FakeService()
        self.widget = HistoryWidget(self.service)
        self.addCleanup(self.widget.close)

    def test_lazy_initialization_shortcuts_and_inclusive_custom_range(self):
        self.assertEqual(self.service.filter_calls, 0)
        self.assertEqual(self.service.report_calls, [])
        self.widget.refresh()
        today = QDate.currentDate()
        self.assertEqual(self.service.report_calls[-1], {
            "since": QDate(today.year(), today.month(), 1).toString("yyyy-MM-dd"),
            "until": today.toString("yyyy-MM-dd"), "category": None, "duty": None,
        })
        self.widget.this_year_button.click()
        self.assertEqual(self.service.report_calls[-1]["since"], f"{today.year()}-01-01")
        self.widget.all_time_button.click()
        self.assertIsNone(self.service.report_calls[-1]["since"])
        self.assertIsNone(self.service.report_calls[-1]["until"])
        self.widget.use_from.setChecked(True)
        self.widget.use_to.setChecked(True)
        self.widget.from_date.setDate(QDate(2024, 4, 30))
        self.widget.to_date.setDate(QDate(2024, 4, 30))
        self.widget.show_report_button.click()
        self.assertEqual(self.service.report_calls[-1]["since"], "2024-04-30")
        self.assertEqual(self.service.report_calls[-1]["until"], "2024-04-30")
        self.assertIn("inclusive", self.widget.scope_label.text())

    def test_historical_filters_and_single_snapshot_populate_all_sections(self):
        self.widget.refresh()
        self.assertEqual(self.widget.category_filter.itemText(1), "Archived <work>")
        self.assertEqual(self.widget.role_filter.itemText(1), "Retired <lead>")
        self.widget.category_filter.setCurrentIndex(1)
        self.widget.role_filter.setCurrentIndex(1)
        self.widget.refresh()
        self.assertEqual(self.service.report_calls[-1]["category"], "Archived <work>")
        self.assertEqual(self.service.report_calls[-1]["duty"], "Retired <lead>")
        self.assertEqual(self.widget.category_filter.currentData(), "Archived <work>")
        self.assertEqual(self.widget.role_filter.currentData(), "Retired <lead>")
        self.assertIn("1 completions", self.widget.summary_label.text())
        self.assertEqual(self.widget.measures_table.rowCount(), 2)
        self.assertEqual(self.widget.measures_table.item(0, 1).text(), "3")
        self.assertEqual(self.widget.measures_table.item(1, 1).text(), "5")
        self.assertEqual(self.widget.categories_table.rowCount(), 1)
        self.assertEqual(self.widget.accomplishments_table.rowCount(), 1)
        self.assertEqual(self.widget.accomplishments_table.item(0, 1).text(), "<b>Helped people</b>")
        self.assertEqual(len(self.service.report_calls), 2)

    def test_invalid_range_and_load_error_clear_old_results(self):
        self.widget.refresh()
        calls = len(self.service.report_calls)
        self.widget.from_date.setDate(QDate(2025, 1, 2))
        self.widget.to_date.setDate(QDate(2025, 1, 1))
        self.widget.refresh()
        self.assertEqual(len(self.service.report_calls), calls)
        self.assertEqual(self.widget.accomplishments_table.rowCount(), 0)
        self.assertEqual(self.widget.measures_table.rowCount(), 0)
        self.assertEqual(self.widget.scope_label.text(), "No report applied.")
        self.assertIn("From must", self.widget.status_label.text())
        self.widget.to_date.setDate(QDate(2025, 1, 2))
        self.service.fail = ValueError("archive unavailable")
        self.widget.refresh()
        self.assertEqual(self.widget.accomplishments_table.rowCount(), 0)
        self.assertIn("archive unavailable", self.widget.status_label.text())

    def test_empty_report_replaces_previous_snapshot(self):
        self.widget.refresh()
        self.service.empty = True
        self.widget.refresh()
        self.assertEqual(self.widget.accomplishments_table.rowCount(), 0)
        self.assertEqual(self.widget.measures_table.rowCount(), 0)
        self.assertIn("0 completions", self.widget.summary_label.text())
        self.assertEqual(self.widget.status_label.text(), "No accomplishments in this range.")

    def test_row_activation_opens_read_only_plain_text_snapshot(self):
        self.widget.refresh()
        row = self.widget._accomplishments[0]
        self.assertEqual(row["id"], 42)
        dialog = self.widget._detail_dialog(row)
        self.addCleanup(dialog.close)
        fields = dialog.findChildren(QLabel)
        self.assertTrue(any(label.text() == "<Project>" and
                            label.textFormat() == Qt.TextFormat.PlainText for label in fields))
        outcome = dialog.findChild(QPlainTextEdit)
        self.assertEqual(outcome.toPlainText(), "<b>Helped people</b>")
        self.assertTrue(outcome.isReadOnly())
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted) as opened:
            self.widget._open_item(self.widget.accomplishments_table.item(0, 0))
            opened.assert_called_once()


if __name__ == "__main__":
    unittest.main()
