"""HTML presentation and narrative boundary for verified report evidence."""

import unittest

from saga.report_html import render_html, validate_narrative


def evidence():
    return {
        "schema_version": 1,
        "report_id": "reportabc",
        "scope": {"since": "2024-01-01", "until": "2024-02-01", "category": None, "duty": None},
        "summary": {"completions": 2, "review_counting": 1, "flagged": 0, "with_a_number": 1},
        "verification": {"passed": True, "checks": ["records match counts"]},
        "records": [
            {"id": 11, "completed_at": "2024-01-09 12:00:00", "category": "<work>",
             "duty": None, "outcome": '<img src=x onerror="alert(1)">', "measure": "<widgets>", "quantity": 2},
        ],
        "facts": {
            "f-total": {"label": "Total <widgets>", "value": 2, "display": "2 <widgets>",
                        "measure": "<widgets>", "dimension": None, "group": None,
                        "source_record_ids": [11]},
            "f-cat": {"label": "Category <work>", "value": 2, "display": "2 <widgets>",
                      "measure": "<widgets>", "dimension": "category", "group": "<work>",
                      "source_record_ids": [11]},
        },
        "measurements": [{"measure": "<widgets>", "total": 2, "occasions": 1,
                          "fact_id": "f-total",
                          "by_category": [{"label": "<work>", "total": 2, "occasions": 1,
                                           "fact_id": "f-cat"}],
                          "by_role": [{"label": "No role assigned", "total": 2, "occasions": 1,
                                       "fact_id": "f-total"}],
                          "by_month": [{"label": "2024-01", "total": 2, "occasions": 1,
                                        "fact_id": "f-total"}]}],
    }


def narrative():
    return {"schema_version": 1, "report_id": "reportabc", "sections": [
        {"heading": "Completed work", "paragraphs": [[
            {"text": "The recorded quantity was "}, {"fact": "f-total"},
            {"text": " across the selected scope."}]]}]}


class ReportHtmlTests(unittest.TestCase):
    def test_compact_report_escapes_labels_and_links_to_full_evidence(self):
        html = render_html(evidence())
        self.assertIn("Measured results", html)
        self.assertIn("By category", html)
        self.assertIn("<summary>Show more: by role</summary>", html)
        self.assertIn("Corrections use the latest completion revision", html)
        self.assertIn('href="evidence.json" title="Fact ID: f-total"', html)
        self.assertIn('href="evidence.json" title="Fact ID: f-cat"', html)
        self.assertIn("&lt;widgets&gt;", html)
        self.assertIn("&lt;work&gt;", html)
        self.assertNotIn("By month", html)
        self.assertNotIn("Fact references</h2>", html)
        self.assertNotIn("Completion records</h2>", html)
        self.assertNotIn("2024-01</td>", html)
        self.assertNotIn("Source records", html)
        self.assertNotIn("onerror", html)
        self.assertNotIn("record-11", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("Agent narrative", html)
        self.assertNotIn("<script", html)

    def test_valid_narrative_uses_current_fact_and_is_visibly_a_draft(self):
        html = render_html(evidence(), narrative())
        self.assertIn("Agent narrative — draft for review", html)
        self.assertIn('href="evidence.json" title="Fact ID: f-total"', html)
        self.assertIn("The wording and interpretation still need human review", html)

    def test_narrative_text_is_escaped(self):
        case = narrative()
        case["sections"][0]["paragraphs"][0][0]["text"] = '<img src=x onerror="alert()">'
        html = render_html(evidence(), case)
        self.assertIn('&lt;img src=x onerror=&quot;alert()&quot;&gt;', html)
        self.assertNotIn("<img", html)

    def test_unknown_fact_extra_key_and_mismatched_report_are_rejected(self):
        cases = []
        case = narrative()
        case["sections"][0]["paragraphs"][0][1]["fact"] = "missing"
        cases.append(case)
        case = narrative()
        case["extra"] = True
        cases.append(case)
        case = narrative()
        case["sections"][0]["paragraphs"][0][0]["unexpected"] = True
        cases.append(case)
        case = narrative()
        case["report_id"] = "another"
        cases.append(case)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_narrative(case, evidence())

    def test_literal_digits_in_text_or_heading_are_rejected(self):
        for field, value in (("heading", "Completed 2 tasks"), ("heading", "Completed ٢ tasks"),
                             ("text", "Saved 2 hours")):
            case = narrative()
            if field == "heading":
                case["sections"][0]["heading"] = value
            else:
                case["sections"][0]["paragraphs"][0][0]["text"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_narrative(case, evidence())

    def test_unverified_evidence_cannot_render_and_null_group_stays_distinct(self):
        data = evidence()
        data["verification"]["passed"] = False
        with self.assertRaises(ValueError):
            render_html(data)
        data = evidence()
        data["measurements"][0]["by_role"].append({"label": "Role: No role", "total": 3,
                                                   "occasions": 1, "fact_id": "f-cat"})
        html = render_html(data)
        self.assertIn('<td>No role assigned</td>', html)
        self.assertIn('<td>Role: No role</td>', html)
        self.assertIn('href="evidence.json" title="Fact ID: f-cat"', html)

    def test_narrative_size_is_bounded(self):
        case = narrative()
        case["sections"][0]["paragraphs"][0][0]["text"] = "a" * 2001
        with self.assertRaises(ValueError):
            validate_narrative(case, evidence())


if __name__ == "__main__":
    unittest.main()
