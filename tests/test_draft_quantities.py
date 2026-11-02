"""Independent coverage for the conservative local quantity repair boundary."""

import copy
import unittest

from saga.draft_quantities import repair_quantities
from saga.preparation import build_preparation


def evidence(value=40, unit="hours"):
    return {
        "schema_version": 1, "report_id": "report-test", "verification": {"passed": True},
        "records": [{"id": 11, "measure": unit, "quantity": value,
                     "task_title": "Saved action", "outcome": "Saved result",
                     "category": "Operations", "duty": "Coordinator", "private_note": "SECRET"}],
        "measurements": [{"measure": unit, "total": value, "occasions": 1,
                          "fact_id": "hours-total"}],
        "facts": {
            "hours-total": {"value": value, "display": str(value), "measure": unit,
                            "dimension": "total", "group": None, "source_record_ids": [11]},
            "count": {"value": 4, "display": "4", "measure": None, "dimension": "summary",
                      "group": "completions", "source_record_ids": [11, 12, 13, 14]},
        },
    }


def narrative(text, heading="Results"):
    return {"schema_version": 1, "report_id": "report-test", "sections": [
        {"heading": heading, "paragraphs": [[{"text": text}]]}]}


def paragraph(draft):
    return draft["sections"][0]["paragraphs"][0]


class QuantityRepairTests(unittest.TestCase):
    def repair(self, text, value=40, unit="hours", *, data=None, heading="Results"):
        data = evidence(value, unit) if data is None else data
        return repair_quantities(narrative(text, heading), data, None, set(data["facts"]))

    def assert_rejected(self, text, value=40, unit="hours", **kwargs):
        with self.assertRaisesRegex(ValueError, "^unverified quantity$"):
            self.repair(text, value, unit, **kwargs)

    def test_exact_general_replaces_only_numeral(self):
        self.assertEqual(paragraph(self.repair("Recorded forty hours.")), [
            {"text": "Recorded "}, {"fact": "hours-total"}, {"text": " hours."}])

    def test_ordinary_connecting_words_are_not_treated_as_quantities(self):
        for text in ("One review of accomplishments is pending.",
                     "Provided assistance to people assisted total.",
                     "Volunteer contributions amounted to volunteer hours total hours."):
            with self.subTest(text=text):
                data = evidence()
                draft = narrative(text)
                self.assertEqual(repair_quantities(draft, data, None, set(data["facts"])), draft)

    def test_operator_before_number_does_not_become_an_exact_quantity(self):
        for text in ("Recorded up through forty hours.", "Recorded up to forty hours.",
                     "Recorded times forty hours.", "Recorded plus forty hours.",
                     "Recorded percent forty hours."):
            with self.subTest(text=text):
                self.assert_rejected(text)

    def test_compound_unit_and_number(self):
        self.assertEqual(paragraph(self.repair("Helped one hundred and eighty people assisted.",
                                              180, "people assisted")), [
            {"text": "Helped "}, {"fact": "hours-total"}, {"text": " people assisted."}])

    def test_completion_aliases(self):
        for unit in ("accomplishments", "completions", "accomplishment", "completion"):
            with self.subTest(unit=unit):
                self.assertEqual(paragraph(self.repair("Recorded four " + unit)), [
                    {"text": "Recorded "}, {"fact": "count"}, {"text": " " + unit}])

    def test_zero_is_observed_not_absent(self):
        self.assertEqual(paragraph(self.repair("zero hours", 0)), [
            {"fact": "hours-total"}, {"text": " hours"}])
        data = evidence(0)
        data["measurements"][0]["occasions"] = 0
        data["facts"]["hours-total"]["source_record_ids"] = []
        self.assert_rejected("zero hours", data=data)

    def test_exact_integer_grammar(self):
        for words, value in (("twenty-one", 21), ("one hundred eighty", 180),
                             ("nine hundred and ninety nine", 999),
                             ("one thousand and five", 1005),
                             ("one million two hundred thousand thirty", 1200030),
                             ("negative forty", -40), ("minus two", -2)):
            with self.subTest(words=words):
                self.assertEqual(paragraph(self.repair(words + " hours", value))[0],
                                 {"fact": "hours-total"})

    def test_exact_decimal_grammar(self):
        for words, value in (("two point five", 2.5), ("zero point zero five", .05),
                             ("negative zero point five", -.5),
                             ("minus two point five zero", -2.5)):
            with self.subTest(words=words):
                self.assertEqual(paragraph(self.repair(words + " hours", value))[0],
                                 {"fact": "hours-total"})

    def test_whole_value_conflicts_never_repair_suffix(self):
        for words, value in (("one hundred and eighty", 80), ("two point five", 5),
                             ("negative forty", 40), ("one thousand five", 5),
                             ("one and a half", 1), ("one hundred hundred five", 5),
                             ("one billion five", 5), ("two and five", 5),
                             ("one point twenty five", 25), ("zero hundred five", 5),
                             ("forty", 41)):
            with self.subTest(words=words):
                self.assert_rejected(words + " hours", value)

    def test_exact_decimal_no_rounding(self):
        self.assert_rejected("two point five hours", 2.500000000000001)

    def test_additive_or_range_number_tail_cannot_be_repaired_as_integer(self):
        for text in ("two hours and a half", "two hours and one half", "two hours plus half",
                     "two hours to five", "one to two hours", "half of two hours",
                     "one hundred & two hours", "one plus two hours"):
            with self.subTest(text=text):
                self.assert_rejected(text, 2)

    def test_unsupported_fraction_is_rejected(self):
        self.assert_rejected("one and a half hours", 1.5)
        self.assert_rejected("half hours", .5)

    def test_units_are_exact_not_qualifier_guesses(self):
        self.assert_rejected("forty volunteer hours")
        self.assert_rejected("twenty first hours", 20)
        self.assertEqual(paragraph(self.repair("forty volunteer hours", unit="volunteer hours")),
                         [{"fact": "hours-total"}, {"text": " volunteer hours"}])

    def test_longest_unit_wins(self):
        data = evidence(40, "people assisted")
        data["facts"]["people-short"] = dict(data["facts"]["hours-total"], measure="people", value=5)
        data["measurements"].append({"fact_id": "people-short", "measure": "people",
                                     "total": 5, "occasions": 1})
        self.assertEqual(paragraph(self.repair("forty people assisted", data=data)), [
            {"fact": "hours-total"}, {"text": " people assisted"}])
        self.assert_rejected("five people assisted", data=data)

    def test_case_and_whitespace_preserved(self):
        self.assertEqual(paragraph(self.repair("FORTY  volunteer\thours!", unit="volunteer hours")),
                         [{"fact": "hours-total"}, {"text": "  volunteer\thours!"}])

    def test_unit_case_must_match_the_verified_measure(self):
        self.assert_rejected("forty mW", unit="MW")
        self.assert_rejected("forty MW", unit="mW")
        self.assertEqual(paragraph(self.repair("forty MW", unit="MW")),
                         [{"fact": "hours-total"}, {"text": " MW"}])

    def test_selected_unit_case_is_not_rewritten_to_a_different_measure(self):
        data = evidence(unit="MW")
        prep = build_preparation(data, "award", [11])
        with self.assertRaisesRegex(ValueError, "^unverified quantity$"):
            repair_quantities(narrative("forty mW"), data, prep, set(prep["facts"]))

    def test_ambiguous_canonical_totals_rejected_even_if_one_value_matches(self):
        for second in (40, 5):
            data = evidence()
            data["facts"]["other"] = dict(data["facts"]["hours-total"], value=second)
            data["measurements"].append({"fact_id": "other", "measure": "hours",
                                         "total": second, "occasions": 1})
            self.assert_rejected("forty hours", data=data)

    def test_subtotals_and_unlisted_totals_are_not_candidates(self):
        for dimension in ("category", "role", "month", "total"):
            data = evidence()
            data["measurements"] = []
            data["facts"]["hours-total"]["dimension"] = dimension
            self.assert_rejected("forty hours", data=data)

    def test_canonical_identity_must_match(self):
        for field, changed in (("measure", "widgets"), ("value", 41),
                               ("source_record_ids", []), ("dimension", "category")):
            data = evidence()
            data["facts"]["hours-total"][field] = changed
            self.assert_rejected("forty hours", data=data)

    def test_explicit_category_role_and_time_scope_is_rejected(self):
        for text in ("Operations had forty hours.", "Coordinator recorded forty hours.",
                     "In January, forty hours.", "forty hours per month.",
                     "This category recorded forty hours.", "forty hours in Operations.",
                     "Each record had forty hours."):
            self.assert_rejected(text)

    def test_work_category_cannot_borrow_report_total(self):
        data = evidence()
        data["records"][0]["category"] = "Work"
        self.assert_rejected("Work had forty hours.", data=data)

    def test_specific_record_or_task_cannot_borrow_total(self):
        for text in ("forty hours from the selected record.", "forty hours for the first task.",
                     "forty hours for this project.", "forty hours on Monday."):
            with self.subTest(text=text):
                self.assert_rejected(text)

    def test_scoped_headings_apply_to_paragraph(self):
        self.assert_rejected("forty hours", heading="Operations")
        self.assert_rejected("forty hours", heading="January")

    def test_summary_other_meanings_not_borrowed(self):
        self.assert_rejected("four flagged accomplishments")
        self.assert_rejected("Eligible accomplishments included four accomplishments")

    def test_approximate_claims_not_converted_to_exact(self):
        for prefix in ("about", "roughly", "at least", "more than", "almost", "under"):
            self.assert_rejected(prefix + " forty hours")

    def test_bounds_and_per_item_suffixes_are_not_totals(self):
        for text in ("forty hours or more", "forty hours or less", "forty hours apiece",
                     "forty hours or fewer", "up to forty hours", "~forty hours", "forty hours+"):
            with self.subTest(text=text):
                self.assert_rejected(text)

    def test_unit_ratio_and_indefinite_additive_tail_rejected(self):
        for text in ("forty hours/person", "forty hours and a minute"):
            self.assert_rejected(text)
        self.assertEqual(paragraph(self.repair("forty hours/person", unit="hours/person")),
                         [{"fact": "hours-total"}, {"text": " hours/person"}])

    def test_heading_never_gets_fact_segments(self):
        self.assert_rejected("A useful result", heading="forty hours")

    def test_existing_facts_and_adjacent_text_splits(self):
        data = evidence()
        draft = narrative("Recorded one hundred ")
        data["facts"]["hours-total"]["value"] = 180
        data["measurements"][0]["total"] = 180
        paragraph(draft).extend([{"text": "and eighty ho"}, {"text": "urs."}, {"fact": "count"}])
        result = repair_quantities(draft, data, None, set(data["facts"]))
        self.assertEqual(paragraph(result), [{"text": "Recorded "}, {"fact": "hours-total"},
                                             {"text": " hours."}, {"fact": "count"}])

    def test_fact_interrupts_number_sequence(self):
        data = evidence()
        draft = narrative("forty ")
        paragraph(draft).extend([{"fact": "count"}, {"text": "hours"}])
        self.assertEqual(repair_quantities(draft, data, None, set(data["facts"])), draft)

    def test_unchanged_adjacent_text_keeps_original_valid_segment_lengths(self):
        data = evidence()
        draft = narrative("a" * 1990)
        paragraph(draft).extend([{"text": "b" * 20}, {"fact": "count"}])
        result = repair_quantities(draft, data, None, set(data["facts"]))
        self.assertEqual(result, draft)

    def test_mixed_paragraph_preserves_untouched_adjacent_run(self):
        data = evidence()
        draft = narrative("a" * 1990)
        paragraph(draft).extend([{"text": "b" * 20}, {"fact": "count"}, {"text": "forty hours"}])
        result = repair_quantities(draft, data, None, set(data["facts"]))
        self.assertEqual(paragraph(result)[:3], paragraph(draft)[:3])
        self.assertEqual(paragraph(result)[3:], [{"fact": "hours-total"}, {"text": " hours"}])

    def test_repaired_run_keeps_text_segments_within_limit(self):
        data = evidence()
        draft = narrative("a" * 1990)
        paragraph(draft).append({"text": "b" * 20 + " forty hours"})
        result = repair_quantities(draft, data, None, set(data["facts"]))
        self.assertTrue(all(len(segment["text"]) <= 2000 for segment in paragraph(result)
                            if "text" in segment))
        self.assertEqual("".join(segment.get("text", "<fact>") for segment in paragraph(result)),
                         "a" * 1990 + "b" * 20 + " <fact> hours")

    def test_nonquantity_phrases_are_unchanged(self):
        for text in ("Provided one-on-one coaching and first aid.",
                     "One-on-one hours supported coaching.", "A useful result."):
            self.assertEqual(self.repair(text), narrative(text))

    def test_allowlist_cannot_be_expanded(self):
        with self.assertRaisesRegex(ValueError, "^unverified quantity$"):
            repair_quantities(narrative("forty hours"), evidence(), None, {"count"})

    def test_selected_fact_consumes_unit_and_keeps_surrounding_text(self):
        data = evidence(2.5)
        prep = build_preparation(data, "award", [11])
        draft = narrative("Contributed two point five hours to the effort.")
        result = repair_quantities(draft, data, prep, set(prep["facts"]))
        self.assertEqual(paragraph(result), [{"text": "Contributed "},
                                             {"fact": "record-11-quantity"},
                                             {"text": " to the effort."}])

    def test_selected_never_borrows_general_summary_or_total(self):
        data = evidence()
        prep = build_preparation(data, "cv", [11])
        for text in ("four accomplishments", "five hours"):
            with self.assertRaisesRegex(ValueError, "^unverified quantity$"):
                repair_quantities(narrative(text), data, prep, set(data["facts"]) | set(prep["facts"]))

    def test_selected_same_unit_ambiguous_even_with_distinct_values(self):
        for value in (40, 5):
            data = evidence()
            data["records"].append(dict(data["records"][0], id=12, quantity=value))
            prep = build_preparation(data, "award", [11, 12])
            with self.assertRaisesRegex(ValueError, "^unverified quantity$"):
                repair_quantities(narrative("forty hours"), data, prep, set(prep["facts"]))

    def test_selected_source_identity_is_required(self):
        data = evidence()
        prep = build_preparation(data, "epb", [11])
        prep["facts"]["record-11-quantity"]["source_record_ids"] = [99]
        with self.assertRaisesRegex(ValueError, "^unverified quantity$"):
            repair_quantities(narrative("forty hours"), data, prep, set(prep["facts"]))

    def test_selected_fact_cannot_claim_report_wide_scope(self):
        data = evidence()
        prep = build_preparation(data, "epb", [11])
        for text in ("forty hours overall", "forty hours in total", "forty hours across all work",
                     "forty hours across the report"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "^unverified quantity$"):
                repair_quantities(narrative(text), data, prep, set(prep["facts"]))

    def test_no_mutation_and_no_source_text_leakage(self):
        data = evidence()
        prep = build_preparation(data, "epb", [11])
        draft = narrative("forty hours")
        originals = copy.deepcopy((data, prep, draft))
        result = repair_quantities(draft, data, prep, set(prep["facts"]))
        self.assertEqual((data, prep, draft), originals)
        self.assertNotIn("SECRET", repr(result))
        self.assertNotIn("Saved action", repr(result))
        result["sections"][0]["heading"] = "Changed"
        self.assertEqual((data, prep, draft), originals)
        with self.assertRaisesRegex(ValueError, "^unverified quantity$") as failure:
            repair_quantities(narrative("SECRET fifty hours"), data, prep, set(prep["facts"]))
        self.assertNotIn("SECRET", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
