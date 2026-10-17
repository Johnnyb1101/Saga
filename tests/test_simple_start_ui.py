"""Simple local startup and optional backup settings in the desktop UI."""

import os
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None

if HAS_QT:
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

    from saga import db
    from saga.desktop.backup_settings import BackupSettings
    from saga.desktop.settings import SettingsStore
    from saga.desktop.window import MainWindow


@unittest.skipUnless(HAS_QT, "Optional PySide6 desktop dependency is not installed")
class SimpleStartUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "local" / "saga.db"
        self.store = SettingsStore(self.root / "settings.json")
        self.default_patch = patch("saga.desktop.window.default_database", return_value=self.database)
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)
        self.window = MainWindow(self.store)
        self.addCleanup(self._close)

    def _close(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.window.close()

    def test_get_started_creates_local_archive_once_and_hides_path(self):
        self.assertEqual(self.window.get_started_button.text(), "Get started")
        self.assertIs(self.window.pages.currentWidget(), self.window.setup_page)
        self.window.get_started_button.click()
        self.assertTrue(self.database.exists())
        self.assertEqual(self.window.service.database, self.database.resolve())
        self.assertEqual(self.store.load()["database"], str(self.database.resolve()))
        self.assertEqual(self.window.archive_label.text(), "Your Saga")
        with patch("saga.desktop.window.create_archive") as create:
            self.window.get_started()
            create.assert_not_called()

    def test_existing_default_opens_without_recreating(self):
        db.init_db(self.database)
        with patch("saga.desktop.window.create_archive") as create:
            self.window.get_started()
            create.assert_not_called()
        self.assertEqual(self.window.service.database, self.database.resolve())

    def test_invalid_default_and_missing_saved_archive_are_preserved(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_text("not a Saga archive", encoding="utf-8")
        original = self.database.read_bytes()
        self.window.get_started()
        self.assertEqual(self.database.read_bytes(), original)
        self.assertIsNone(self.window.service)
        self.assertIn("Could not open", self.window.setup_error.text())

        missing = self.root / "missing.db"
        self.store.select(missing, None)
        self.window.get_started()
        self.assertFalse(missing.exists())
        self.assertEqual(self.store.load()["database"], str(missing.resolve()))

    def test_settings_failure_retry_reuses_created_archive(self):
        with patch.object(self.store, "select", side_effect=OSError("settings unavailable")):
            self.window.get_started()
        self.assertTrue(self.database.exists())
        self.assertIsNone(self.window.service)
        self.assertIn("settings unavailable", self.window.setup_error.text())
        with patch("saga.desktop.window.create_archive") as create:
            self.window.get_started()
            create.assert_not_called()
        self.assertEqual(self.store.load()["database"], str(self.database.resolve()))
        self.assertEqual(self.store.export_for(self.database),
                         (self.database.parent / "saga-exports").resolve())

    def test_advanced_starts_collapsed_even_without_archive(self):
        dialog = self.window._settings_dialog()
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.advanced_toggle.isChecked())
        create = next(button for button in dialog.findChildren(QPushButton)
                      if button.text().startswith("Create archive in another"))
        self.assertFalse(create.isVisibleTo(dialog))
        dialog.advanced_toggle.click()
        self.assertTrue(create.isVisibleTo(dialog))

    def test_cancel_folder_selection_does_not_enable_backups(self):
        self.window.get_started()
        dialog = self.window._settings_dialog()
        self.addCleanup(dialog.deleteLater)
        controls = dialog.findChild(BackupSettings)
        with patch.object(QFileDialog, "getExistingDirectory", return_value=""):
            controls.choose_button.click()
        self.assertFalse(controls.checkbox.isChecked())
        self.assertFalse(self.store.backup_for(self.database)["enabled"])

    def test_advanced_new_archive_keeps_drafts_when_discard_is_cancelled(self):
        self.window.get_started()
        self.window.entry.task.title.setText('Keep this draft')
        original = self.window.service.database
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel), \
                patch.object(QFileDialog, 'getSaveFileName') as choose:
            self.window._browse_new()
        choose.assert_not_called()
        self.assertEqual(self.window.service.database, original)
        self.assertEqual(self.window.entry.task.title.text(), 'Keep this draft')

    def test_choose_folder_then_opt_in_and_report_background_error(self):
        self.window.get_started()
        destination = self.root / "OneDrive"
        destination.mkdir()
        with patch("saga.desktop.backup_settings.detect_onedrive", return_value=None):
            dialog = self.window._settings_dialog()
        self.addCleanup(dialog.deleteLater)
        controls = dialog.findChild(BackupSettings)
        controls.checkbox.click()
        self.assertFalse(controls.checkbox.isChecked())
        self.assertFalse(self.store.backup_for(self.database)["enabled"])
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)):
            controls.choose_button.click()
        self.assertFalse(self.store.backup_for(self.database)["enabled"])
        with patch.object(self.window.backup_controller, "configure") as configure:
            controls.checkbox.click()
            configure.assert_called_once_with(self.database.resolve())
        self.assertTrue(controls.backup_now_button.isEnabled())
        self.assertEqual(self.store.backup_for(self.database),
                         {"enabled": True, "directory": str(destination.resolve())})
        self.window.backup_controller.statusChanged.emit("Backup failed: offline")
        self.assertEqual(self.window.backup_status.text(), "Backup failed: offline")
        self.assertFalse(self.window.backup_status.isHidden())

    def test_failed_backup_write_rolls_back_toggle_and_preserves_drafts(self):
        self.window.get_started()
        self.window.entry.task.title.setText("Unsaved task")
        self.window.entry.task.completion_details.outcome.setPlainText("Unsaved accomplishment")
        destination = self.root / "OneDrive" / "SagaBackups"
        destination.mkdir(parents=True)
        with patch("saga.desktop.backup_settings.detect_onedrive", return_value=destination.parent):
            dialog = self.window._settings_dialog()
        self.addCleanup(dialog.deleteLater)
        controls = dialog.findChild(BackupSettings)
        with patch.object(self.store, "set_backup", side_effect=OSError("settings unavailable")):
            controls.checkbox.click()
        self.assertFalse(controls.checkbox.isChecked())
        self.assertFalse(self.store.backup_for(self.database)["enabled"])
        self.assertIn("settings unavailable", controls.status.text())
        self.assertEqual(self.window.entry.task.title.text(), "Unsaved task")
        self.assertEqual(self.window.entry.task.completion_details.outcome.toPlainText(), "Unsaved accomplishment")


if __name__ == "__main__":
    unittest.main()
