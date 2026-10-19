"""Selected-record preparation stays bound to Saga's verified evidence."""

import copy
import unittest

from saga.preparation import build_preparation, preparation_guide
from saga.report_html import render_html, validate_narrative


def evidence():
    return {
        "schema_version": 1, "report_id": "fixed-report", "verification": {"passed": True},
        "scope": {"since": None, "until": None, "category": None, "duty": None},
        "summary": {"completions": 3}, "facts": {}, "measurements": [],
        "records": [
            {"id": 11, "completed_at": "2024-01-01", "category": "<ops>", "duty": "Lead",
             "task_title": "Installed <widgets>", "outcome": "Saved 99 units <script>alert(1)</script>",
             "measure": "widgets", "quantity": 2.5},
            {"id": 12, "completed_at": "2024-02-01", "category": "<ops>", "duty": "Lead",
             "task_title": None, "outcome": "Resolved issue", "measure": None, "quantity": None},
            {"id": 13, "completed_at": "2024-03-01", "category": "Training", "duty": None,
             "task_title": "Mentor", "outcome": "Held session", "measure": "hours", "quantity": 1},
        ],
    }


class PreparationTests(unittest.TestCase):
    def test_selection_requires_known_unique_integer_ids_in_epb_and_cv(self):
        data = evidence()
        for report_format in ("epb", "cv"):
            for selected in (None, [], [11, 11], [99], [True], [11.0], ["11"], (11,)):
                with self.subTest(format=report_format, ids=selected), self.assertRaises(ValueError):
                    build_preparation(data, report_format, selected)
        for report_format in ("EPB", "bad", "", None):
            with self.subTest(format=report_format), self.assertRaises(ValueError):
                build_preparation(data, report_format, [11])
        with self.assertRaises(ValueError):
            build_preparation(data, "general", [11])

    def test_general_default_has_no_selection_and_render_is_compatible(self):
        preparation = build_preparation(evidence())
        self.assertEqual(preparation["report_format"], "general")
        self.assertEqual(preparation["selected_records"], [])
        self.assertEqual(preparation["facts"], {})
        self.assertEqual(render_html(evidence(), preparation=preparation), render_html(evidence()))

    def test_selected_measurements_have_stable_record_provenance_and_units(self):
        first = build_preparation(evidence(), "cv", [13, 11, 12])
        second = build_preparation(evidence(), "cv", [13, 11, 12])
        self.assertEqual(first, second)
        self.assertEqual(first["preparation_id"], second["preparation_id"])
        self.assertEqual(first["selected_ids"], [13, 11, 12])
        self.assertEqual(list(first["facts"]), ["record-13-quantity", "record-11-quantity"])
        self.assertEqual(first["facts"]["record-11-quantity"]["display"], "2.5 widgets")
        self.assertEqual(first["facts"]["record-11-quantity"]["source_record_ids"], [11])
        self.assertEqual(first["selected_records"][2]["measurement"], None)
        self.assertEqual(first["selected_records"][2]["action"]["status"], "missing")
        self.assertEqual(first["selected_records"][0]["impact"]["status"], "missing")
        self.assertEqual(first["selected_records"][1]["result"]["text"],
                         "Saved 99 units <script>alert(1)</script>")

    def test_epb_escapes_saved_text_and_marks_missing_impact_and_full_range(self):
        data = evidence()
        preparation = build_preparation(data, "epb", [11, 12])
        html = render_html(data, preparation=preparation)
        self.assertIn("EPB preparation — draft for review", html)
        self.assertIn("Action — saved task title", html)
        self.assertIn("Result — saved outcome", html)
        self.assertIn("Missing — add supported impact", html)
        self.assertIn("Verified summary — full filtered range", html)
        self.assertIn("Saved wording is unverified source text", html)
        self.assertIn("Measured results — full filtered range", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn('href="preparation.json" title="Fact ID: record-11-quantity"', html)
        self.assertIn("2.5 widgets", html)
        self.assertIn("No measured quantity saved", html)

    def test_cv_groups_by_saved_role_and_category(self):
        data = evidence()
        html = render_html(data, preparation=build_preparation(data, "cv", [11, 13, 12]))
        self.assertIn("CV preparation — draft for review", html)
        self.assertIn("Saved role: Lead · Category: &lt;ops&gt;", html)
        self.assertIn("Saved role: Unassigned · Category: Training", html)
        self.assertIn("Record 11", html)
        self.assertIn("Record 12", html)
        self.assertIn("Record 13", html)
        self.assertIn("1 hours", html)

    def test_modified_preparation_or_facts_are_rejected(self):
        data = evidence()
        original = build_preparation(data, "epb", [11])
        for change in (lambda p: p.update(total=900),
                       lambda p: p["facts"]["record-11-quantity"].update(value=900),
                       lambda p: p["selected_records"][0]["source_record"].update(quantity=900),
                       lambda p: p.update(report_id="other")):
            changed = copy.deepcopy(original)
            change(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                render_html(data, preparation=changed)

    def test_narrative_can_reference_only_selected_record_facts(self):
        data = evidence()
        prep = build_preparation(data, "epb", [11])
        narrative = {"schema_version": 1, "report_id": "fixed-report",
                     "preparation_id": prep["preparation_id"], "sections": [
            {"heading": "Selected measurement", "paragraphs": [[
                {"text": "Saga recorded "}, {"fact": "record-11-quantity"}]]}]}
        validate_narrative(narrative, data, prep)
        self.assertIn('href="preparation.json"', render_html(data, narrative, prep))
        with self.assertRaises(ValueError):
            validate_narrative(narrative, data)
        other = build_preparation(data, "cv", [11])
        with self.assertRaises(ValueError):
            validate_narrative(narrative, data, other)
        other = build_preparation(data, "epb", [11, 12])
        with self.assertRaises(ValueError):
            validate_narrative(narrative, data, other)
        narrative["sections"][0]["paragraphs"][0][1]["fact"] = "record-13-quantity"
        with self.assertRaises(ValueError):
            validate_narrative(narrative, data, prep)

    def test_guide_has_format_specific_constraints(self):
        self.assertIn("action, result, and impact", preparation_guide("epb"))
        self.assertIn("Do not invent employers", preparation_guide("cv"))


if __name__ == "__main__":
    unittest.main()
