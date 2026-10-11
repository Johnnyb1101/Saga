"""Smoke command checks a disposable archive and reports failures."""

import json
import os
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtWidgets import QApplication

    from saga.desktop import smoke


@unittest.skipUnless(HAS_QT, "Optional PySide6 desktop dependency is not installed")
class DesktopSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / "smoke.json"

    def test_success_checks_all_paths_and_writes_report(self):
        self.assertEqual(smoke.run_smoke(self.app, self.report), 0)
        result = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(result["success"])
        self.assertEqual(result["checks"], {
            "migration_resources": True, "schema_and_archive": True,
            "capture_and_export": True, "settings_roundtrip": True,
            "main_window": True,
        })
        self.assertIsInstance(result["frozen"], bool)
        self.assertTrue(result["runtime"]["python"])
        self.assertTrue(result["runtime"]["qt"])

    def test_failure_returns_nonzero_with_failing_step(self):
        with patch.object(smoke, "create_archive", side_effect=OSError("injected archive failure")):
            self.assertEqual(smoke.run_smoke(self.app, self.report), 1)
        result = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertFalse(result["success"])
        self.assertTrue(result["checks"]["migration_resources"])
        self.assertFalse(result["checks"]["schema_and_archive"])
        self.assertIn("injected archive failure", result["error"])


if __name__ == "__main__":
    unittest.main()
