"""HTML presentation and narrative boundary for verified report evidence."""

import unittest

from saga.preparation import build_preparation
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
        self.assertLess(html.index("<h2>Scope</h2>"), html.index("Agent narrative — draft for review"))
        self.assertLess(html.index("Agent narrative — draft for review"),
                        html.index("<summary>Verified totals and supporting details</summary>"))
        self.assertLess(html.index("<summary>Verified totals and supporting details</summary>"),
                        html.index("<h2>Summary</h2>"))

    def test_fixed_report_stays_visible_without_narrative(self):
        html = render_html(evidence())
        self.assertNotIn("<summary>Verified totals and supporting details</summary>", html)
        self.assertIn("<h2>Summary</h2>", html)
        self.assertIn("across 1 occasion.", html)
        for label in ("Accomplishments", "Count toward reviews", "Flagged for review",
                      "With a recorded number"):
            self.assertIn(f"<span>{label}</span>", html)

    def test_unobserved_group_rows_are_hidden_but_recorded_zero_is_shown(self):
        data = evidence()
        data["facts"]["f-zero"] = {"display": "0 <widgets>"}
        data["measurements"][0]["by_category"].extend([
            {"label": "Unused category", "total": 0, "occasions": 0, "fact_id": "f-zero"},
            {"label": "Recorded zero", "total": 0, "occasions": 1, "fact_id": "f-zero"},
        ])
        data["measurements"][0]["by_role"] = [
            {"label": "Unused role", "total": 0, "occasions": 0, "fact_id": "f-zero"},
        ]
        html = render_html(data)
        self.assertNotIn("Unused category", html)
        self.assertNotIn("Unused role", html)
        self.assertNotIn("Show more: by role", html)
        self.assertIn("Recorded zero", html)
        self.assertIn('href="evidence.json" title="Fact ID: f-zero"', html)

    def test_selected_preparation_is_nested_below_narrative_and_totals(self):
        data = evidence()
        preparation = build_preparation(data, "epb", [11])
        draft = narrative()
        draft["preparation_id"] = preparation["preparation_id"]
        html = render_html(data, draft, preparation)
        self.assertLess(html.index("Agent narrative — draft for review"),
                        html.index("<summary>Verified totals and supporting details</summary>"))
        self.assertLess(html.index("<summary>Verified totals and supporting details</summary>"),
                        html.index("<summary>Selected preparation records</summary>"))
        self.assertLess(html.index("<summary>Selected preparation records</summary>"),
                        html.index("EPB preparation — draft for review"))

    def test_draft_only_keeps_verified_facts_without_supporting_tables(self):
        data = evidence()
        html = render_html(data, narrative(), draft_only=True)
        self.assertIn("Agent narrative — draft for review", html)
        self.assertIn('href="evidence.json" title="Fact ID: f-total"', html)
        self.assertIn("2 &lt;widgets&gt;", html)
        self.assertIn("Verified totals are available in the separate totals tab and saved report.", html)
        self.assertNotIn("<h2>Summary</h2>", html)
        self.assertNotIn("<h2>Measured results</h2>", html)
        self.assertNotIn("<table>", html)
        self.assertNotIn("Selected preparation records", html)
        with self.assertRaises(ValueError):
            render_html(data, draft_only=True)
        draft = narrative()
        draft["sections"][0]["paragraphs"][0][1]["fact"] = "unknown"
        with self.assertRaises(ValueError):
            render_html(data, draft, draft_only=True)

    def test_award_draft_heading_and_selected_sources(self):
        data = evidence()
        preparation = build_preparation(data, "award", [11])
        draft = narrative()
        draft["preparation_id"] = preparation["preparation_id"]
        full_html = render_html(data, draft, preparation)
        draft_html = render_html(data, draft, preparation, draft_only=True)
        self.assertIn("<h1>Award draft</h1>", full_html)
        self.assertIn("Award preparation — draft for review", full_html)
        self.assertIn("Action — saved task title", full_html)
        self.assertIn("Result — saved outcome", full_html)
        self.assertIn("<dt>Impact</dt>", full_html)
        self.assertIn("Selected record 11", full_html)
        self.assertIn("<h1>Award draft</h1>", draft_html)
        self.assertNotIn("Selected record 11", draft_html)

    def test_omitted_results_are_inserted_from_snapshot_without_repeating_inline_facts(self):
        data = evidence()
        data["facts"]["f-hours"] = {"display": "40", "measure": "volunteer hours"}
        data["facts"]["f-count"] = {"display": "2", "dimension": "summary", "group": "completions"}
        data["measurements"].append({"measure": "volunteer hours", "total": 40,
                                     "occasions": 1, "fact_id": "f-hours"})
        html = render_html(data, narrative(), draft_only=True)
        self.assertIn("Recorded results — full filtered range", html)
        self.assertIn('volunteer hours: <a href="evidence.json" title="Fact ID: f-hours">40</a>', html)
        self.assertIn('Accomplishments recorded: <a href="evidence.json" title="Fact ID: f-count">2</a>', html)
        self.assertEqual(html.count('title="Fact ID: f-total"'), 1)
        self.assertNotIn("<table>", html)
        html = render_html(evidence(), narrative(), draft_only=True)
        self.assertNotIn("Recorded results —", html)

    def test_inserted_results_preserve_observed_zero_and_omit_unobserved_totals(self):
        data = evidence()
        data["facts"]["f-zero"] = {"display": "0"}
        data["facts"]["f-unused"] = {"display": "0"}
        data["measurements"].extend([
            {"measure": "recorded zero", "total": 0, "occasions": 1, "fact_id": "f-zero"},
            {"measure": "unobserved measure", "total": 0, "occasions": 0, "fact_id": "f-unused"},
        ])
        html = render_html(data, narrative(), draft_only=True)
        self.assertIn('recorded zero: <a href="evidence.json" title="Fact ID: f-zero">0</a>', html)
        self.assertNotIn("unobserved measure", html)

    def test_selected_inserted_results_never_use_full_range_or_unselected_measurements(self):
        data = evidence()
        data["records"].append({"id": 12, "outcome": "Selected second result", "category": "work",
                                "measure": "hours", "quantity": 3})
        data["records"].append({"id": 13, "outcome": "PRIVATE UNSELECTED", "category": "work",
                                "measure": "private measure", "quantity": 99})
        preparation = build_preparation(data, "award", [11, 12])
        draft = narrative()
        draft["preparation_id"] = preparation["preparation_id"]
        draft["sections"][0]["paragraphs"][0][1] = {"fact": "record-11-quantity"}
        html = render_html(data, draft, preparation, draft_only=True)
        self.assertIn("Recorded results — selected accomplishments", html)
        self.assertIn('title="Fact ID: record-12-quantity">3 hours</a>', html)
        self.assertEqual(html.count('title="Fact ID: record-11-quantity"'), 1)
        for omitted in ("full filtered range", "PRIVATE UNSELECTED", "private measure", "99", "Fact ID: f-total"):
            self.assertNotIn(omitted, html)

    def test_every_report_format_has_a_clear_title_and_legacy_prose_still_renders(self):
        for report_format, title in (("general", "General report"), ("epb", "EPB draft"),
                                     ("cv", "CV draft"), ("award", "Award draft")):
            with self.subTest(report_format=report_format):
                data = evidence()
                preparation = build_preparation(data, report_format, None if report_format == "general" else [11])
                draft = narrative()
                if report_format != "general":
                    draft["preparation_id"] = preparation["preparation_id"]
                draft["sections"][0]["paragraphs"] = [[{"text": "An older saved narrative."}]]
                html = render_html(data, draft, preparation, draft_only=True)
                self.assertIn(f"<h1>{title}</h1>", html)
                self.assertIn("An older saved narrative.", html)

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
