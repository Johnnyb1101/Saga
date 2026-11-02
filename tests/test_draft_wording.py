"""Narrow wording cleanup for validated new AI drafts."""

import copy
import unittest

from saga.draft_wording import clean_draft_wording


def evidence(unit="volunteer hours"):
    return {"facts": {
        "hours-total": {"display": "40", "measure": unit, "dimension": "total"},
        "count": {"display": "4", "measure": None, "dimension": "summary"},
    }}


def narrative(*segments, heading="Results"):
    return {"schema_version": 1, "report_id": "report-test", "sections": [
        {"heading": heading, "paragraphs": [list(segments)]}]}


def paragraph(draft):
    return draft["sections"][0]["paragraphs"][0]


class DraftWordingTests(unittest.TestCase):
    def test_exact_general_measure_and_preserves_inputs(self):
        data = evidence()
        draft = narrative({"text": "Recorded "}, {"fact": "hours-total"},
                          {"text": " of volunteer hours."})
        original = copy.deepcopy((draft, data))

        result = clean_draft_wording(draft, data)

        self.assertEqual(paragraph(result), [
            {"text": "Recorded "}, {"fact": "hours-total"},
            {"text": " volunteer hours."}])
        self.assertEqual((draft, data), original)
        self.assertIsNot(result, draft)
        self.assertIsNot(result["sections"], draft["sections"])

    def test_zero_and_negative_fact_values_are_not_reinterpreted(self):
        for value in ("0", "-4"):
            with self.subTest(value=value):
                data = evidence()
                data["facts"]["hours-total"]["display"] = value
                draft = narrative({"fact": "hours-total"},
                                  {"text": " of volunteer hours."})
                self.assertEqual(paragraph(clean_draft_wording(draft, data)), [
                    {"fact": "hours-total"}, {"text": " volunteer hours."}])

    def test_different_or_partial_unit_is_untouched(self):
        for text in (" of volunteer hour.", " of volunteer hours/person.",
                     " of volunteer hours-per-week.", " of volunteer hours2.",
                     " of volunteer hours.txt", " of volunteer hours:person",
                     " of volunteer hours.person", " of volunteer hours .txt",
                     " of volunteer hours saved.", " of Volunteer hours.",
                     " of volunteer Hours.", " of other volunteer hours.",
                     " volunteer hours.", " of volunteer hours afterward."):
            with self.subTest(text=text):
                draft = narrative({"fact": "hours-total"}, {"text": text})
                self.assertEqual(clean_draft_wording(draft, evidence()), draft)

    def test_measure_case_is_exact(self):
        data = evidence("MW")
        draft = narrative({"fact": "hours-total"}, {"text": " of mW."})
        self.assertEqual(clean_draft_wording(draft, data), draft)
        paragraph(draft)[1]["text"] = " OF MW."
        self.assertEqual(paragraph(clean_draft_wording(draft, data))[1],
                         {"text": " MW."})

    def test_selected_and_nonmeasured_facts_are_untouched(self):
        draft = narrative({"fact": "hours-total"}, {"text": " of volunteer hours."})
        for report_format in ("epb", "cv", "award"):
            with self.subTest(report_format=report_format):
                self.assertEqual(clean_draft_wording(
                    draft, evidence(), {"report_format": report_format}), draft)
        for fact_id in ("count", "unmeasured"):
            with self.subTest(fact_id=fact_id):
                data = evidence()
                data["facts"]["unmeasured"] = {
                    "display": "—", "measure": None, "dimension": "total"}
                unmeasured = narrative({"fact": fact_id},
                                       {"text": " of volunteer hours."})
                self.assertEqual(clean_draft_wording(unmeasured, data), unmeasured)

    def test_quoted_prose_heading_and_no_fact_are_untouched(self):
        for prefix in ('"Recorded ', "'Recorded ", "“Recorded ", "‘Recorded ",
                       "He'd said 'Recorded ", "He’d said ‘Recorded "):
            with self.subTest(prefix=prefix):
                draft = narrative({"text": prefix}, {"fact": "hours-total"},
                                  {"text": " of volunteer hours.”"},
                                  heading="Of volunteer hours")
                self.assertEqual(clean_draft_wording(draft, evidence()), draft)
        draft = narrative({"text": "40 of volunteer hours."},
                          heading="Of volunteer hours")
        self.assertEqual(clean_draft_wording(draft, evidence()), draft)

    def test_split_adjacent_segments_keep_boundaries_and_fact_order(self):
        draft = narrative({"fact": "count"}, {"text": " then "},
                          {"fact": "hours-total"}, {"text": " "},
                          {"text": "o"}, {"text": "f"},
                          {"text": " volunteer"}, {"text": " hours."},
                          {"fact": "count"})
        result = clean_draft_wording(draft, evidence())
        self.assertEqual(paragraph(result), [
            {"fact": "count"}, {"text": " then "},
            {"fact": "hours-total"}, {"text": " "},
            {"text": "volunteer"}, {"text": " hours."},
            {"fact": "count"}])

    def test_segment_limits_are_not_exceeded(self):
        draft = narrative({"fact": "hours-total"},
                          {"text": " of volunteer hours."},
                          *({"text": "x" * 2000} for _ in range(48)))
        result = clean_draft_wording(draft, evidence())
        self.assertEqual(len(paragraph(result)), 50)
        self.assertTrue(all(len(segment["text"]) <= 2000
                            for segment in paragraph(result) if "text" in segment))


if __name__ == "__main__":
    unittest.main()
