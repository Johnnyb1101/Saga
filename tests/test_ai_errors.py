"""Safe display text for draft request failures."""

import unittest

from saga.ai_errors import ERROR_MESSAGES, safe_failure_message


class AIErrorTests(unittest.TestCase):
    def test_every_reason_has_distinct_fixed_text(self):
        self.assertEqual(set(ERROR_MESSAGES), {
            "authentication", "permission", "model", "quota", "rate_limit",
            "rejected", "service", "redirect", "connection", "dns", "tls",
            "timeout", "too_large", "unknown",
            "draft_incomplete", "refused", "response_shape", "invalid_json",
            "report_mismatch", "selection_mismatch", "literal_numbers", "unknown_fact",
            "missing_fact", "unresolved_placeholder", "unverified_number",
            "draft_structure", "draft_size", "invalid_evidence", "invalid_preparation", "invalid_model",
        })
        self.assertEqual(len(set(ERROR_MESSAGES.values())), len(ERROR_MESSAGES))
        for reason, message in ERROR_MESSAGES.items():
            self.assertEqual(safe_failure_message(reason), message)

    def test_untrusted_reason_is_not_displayed(self):
        self.assertEqual(safe_failure_message("sk-forged-secret from provider"),
                         ERROR_MESSAGES["unknown"])
        self.assertEqual(safe_failure_message({"provider": "sk-forged-secret"}),
                         ERROR_MESSAGES["unknown"])
