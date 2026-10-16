"""Offscreen checks for the optional Qt desktop interaction."""

import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYSIDE6_AVAILABLE = find_spec("PySide6") is not None

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga import db, writes
    from saga.desktop.settings import SettingsStore
    from saga.desktop.window import MainWindow


@unittest.skipUnless(PYSIDE6_AVAILABLE, "Optional PySide6 desktop dependency is not installed")
class DesktopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "data" / "saga.db"
        self.store = SettingsStore(self.root / "settings.json")
        self.window = MainWindow(self.store)
        self.addCleanup(self._close_window)

    def _close_window(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.window.close()

    def test_first_launch_create_and_reopen_selected_archive(self):
        self.assertIs(self.window.pages.currentWidget(), self.window.setup_page)
        self.window.create_new_archive(self.database)
        self.assertIs(self.window.pages.currentWidget(), self.window.archive_page)
        self.assertTrue(self.database.exists())
        self.assertEqual(self.store.load()["database"], str(self.database.resolve()))
        self.assertEqual(self.store.export_for(self.database), (self.database.parent / "saga-exports").resolve())
        reopened = MainWindow(self.store)
        self.addCleanup(reopened.close)
        self.assertIs(reopened.pages.currentWidget(), reopened.archive_page)
        self.assertEqual(reopened.service.database, self.database.resolve())

    def test_invalid_archive_does_not_replace_selected_archive(self):
        self.window.open_archive(self.database)
        self.assertIn("Could not open", self.window.setup_error.text())
        self.assertIsNone(self.store.load()["database"])
        db.init_db(self.database)
        self.window.open_archive(self.database)
        self.assertEqual(self.window.service.database, self.database.resolve())
        self.assertIsNone(self.store.export_for(self.database))
        with patch.object(QMessageBox, "warning"):
            self.window.open_archive(self.root / "missing.db")
        self.assertEqual(self.window.service.database, self.database.resolve())
        self.assertEqual(self.store.load()["database"], str(self.database.resolve()))

    def test_today_sections_render_invented_rows_as_plain_text(self):
        db.init_db(self.database)
        with closing(db.connect(self.database)) as con:
            writes.add_task(con, "<b>Invented overdue</b>", "work", due_date="2020-01-01")
        self.window.open_archive(self.database)
        self.assertEqual(len(self.window.sections["overdue"].cards), 1)
        card = self.window.sections["overdue"].cards[0]
        self.assertEqual(card.title_label.text(), "<b>Invented overdue</b>")
        self.assertEqual(card.title_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertTrue(self.window.sections["due_today"].empty.isVisibleTo(self.window.today_page))
        self.assertEqual(self.window.sections["overdue"].accessibleName(), "Overdue")
        self.assertEqual(card.accessibleName(), "<b>Invented overdue</b>")

    def test_cards_preserve_details_and_replace_rows_on_refresh(self):
        section = self.window.sections['overdue']
        section.show_rows([{'title': 'Review', 'due_date': '2026-10-01',
                            'category': 'work', 'duty': None}])
        card = section.cards[0]
        self.assertEqual(card.details_label.text(), 'Due Oct 1, 2026 · Category: work')
        self.assertEqual(card.accessibleDescription(), card.details_label.text())
        self.assertEqual(card.details_label.textFormat(), Qt.TextFormat.PlainText)
        section.show_rows([{'title': 'Replacement', 'category': 'personal', 'duty': 'Home'}])
        self.assertEqual(len(section.cards), 1)
        self.assertEqual(section.cards[0].title_label.text(), 'Replacement')
        self.assertIn('Role: Home', section.cards[0].details_label.text())
        self.assertEqual(section.card_layout.count(), 2)
        section.show_rows([])
        self.assertEqual(section.cards, [])
        self.assertFalse(section.empty.isHidden())
        self.assertEqual(section.card_layout.count(), 1)

    def test_followup_cards_show_outcome_and_reminder(self):
        section = self.window.sections['followups']
        section.show_rows([{'outcome': '<b>Awaiting results</b>', 'remind_on': '2026-10-16',
                            'category': 'work', 'duty': 'Operations'}])
        card = section.cards[0]
        self.assertEqual(card.title_label.text(), '<b>Awaiting results</b>')
        self.assertEqual(card.title_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(card.details_label.text(),
                         'Remind on Oct 16, 2026 · Category: work · Role: Operations')

    def test_preview_cancel_and_save_once_after_edit(self):
        self.window.create_new_archive(self.database)
        capture = self.window.capture
        capture.outcome.setPlainText("<b>Invented outcome</b>")
        capture.role.setCurrentIndex(capture.role.count() - 1)
        capture.new_role.setText("Invented role")
        capture.measurement_status.setCurrentIndex(capture.measurement_status.findData("measured"))
        capture.measure.setCurrentIndex(capture.measure.count() - 1)
        capture.new_measure.setText("invented units")
        capture.quantity.setText("0")
        self.assertTrue(capture.form_layout.labelForField(capture.new_role).isVisibleTo(capture.form_group))
        self.assertTrue(capture.form_layout.labelForField(capture.new_measure).isVisibleTo(capture.form_group))
        QTest.mouseClick(capture.preview_button, Qt.MouseButton.LeftButton)
        self.assertFalse(capture.form_group.isVisibleTo(capture))
        self.assertFalse(capture.preview_button.isVisibleTo(capture))
        self.assertTrue(capture.preview_group.isVisibleTo(capture))
        self.assertIn("<b>Invented outcome</b>", capture.preview_text.toPlainText())
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM completions").fetchone()[0], 0)
        QTest.mouseClick(capture.edit_button, Qt.MouseButton.LeftButton)
        self.assertTrue(capture.form_group.isVisibleTo(capture))
        self.assertTrue(capture.preview_button.isVisibleTo(capture))
        capture.outcome.setPlainText("Updated invented outcome")
        QTest.mouseClick(capture.preview_button, Qt.MouseButton.LeftButton)
        with patch("saga.application.export.write_all", side_effect=OSError("export unavailable")):
            QTest.mouseClick(capture.save_button, Qt.MouseButton.LeftButton)
        self.assertIn("Export failed", capture.status.text())
        self.assertFalse(capture.save_button.isEnabled())
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM completions").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT outcome FROM completions").fetchone()[0], "Updated invented outcome")
            self.assertEqual(con.execute("SELECT count(*) FROM duties WHERE name='Invented role'").fetchone()[0], 1)
        with patch("saga.application.export.write_all") as export:
            QTest.mouseClick(capture.retry_button, Qt.MouseButton.LeftButton)
            export.assert_called_once()
        self.assertIn("Export completed", capture.status.text())
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM completions").fetchone()[0], 1)

    def test_failed_preview_keeps_draft_and_close_requires_discard(self):
        self.window.create_new_archive(self.database)
        capture = self.window.capture
        capture.outcome.setPlainText("  ")
        capture.show_preview()
        self.assertIn("Check the draft", capture.status.text())
        self.assertTrue(capture.has_unsaved_draft)
        self.window.show()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(self.window.close())
        self.assertEqual(capture.outcome.toPlainText(), "  ")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.assertTrue(self.window.close())
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM completions").fetchone()[0], 0)

    def test_measurement_choice_is_explicit_and_new_references_refresh(self):
        self.window.create_new_archive(self.database)
        self.window._set_export(None)
        capture = self.window.capture
        capture.outcome.setPlainText("Invented completion")
        capture.show_preview()
        self.assertIn("Choose Yes, No number needed, or I'll add a number later", capture.status.text())
        self.assertFalse(capture.preview_group.isVisibleTo(capture))
        capture.role.setCurrentIndex(capture.role.count() - 1)
        capture.new_role.setText("New invented role")
        capture.measurement_status.setCurrentIndex(capture.measurement_status.findData("measured"))
        capture.measure.setCurrentIndex(capture.measure.count() - 1)
        capture.new_measure.setText("new invented units")
        capture.quantity.setText("3")
        capture.show_preview()
        capture.save()
        self.assertTrue(capture._committed)
        capture.reset_form()
        self.assertGreaterEqual(capture.role.findData("New invented role"), 0)
        self.assertGreaterEqual(capture.measure.findData("new invented units"), 0)
        self.assertIsNone(capture.measurement_status.currentData())

    def test_existing_reference_named_like_old_new_marker_is_not_registration(self):
        db.init_db(self.database)
        with closing(db.connect(self.database)) as con:
            writes.add_duty(con, "__new__", "work")
            writes.add_measure(con, "__new__")
        self.window.open_archive(self.database)
        capture = self.window.capture
        capture.outcome.setPlainText("Invented result")
        capture.category.setCurrentIndex(capture.category.findData("work"))
        capture.role.setCurrentIndex(capture.role.findData("__new__"))
        capture.measurement_status.setCurrentIndex(capture.measurement_status.findData("measured"))
        capture.measure.setCurrentIndex(capture.measure.findData("__new__"))
        capture.quantity.setText("2")
        self.assertEqual(capture.draft()["duty"], "__new__")
        self.assertIsNone(capture.draft()["new_duty"])
        self.assertEqual(capture.draft()["measure"], "__new__")
        self.assertIsNone(capture.draft()["new_measure"])
        capture.show_preview()
        self.assertTrue(capture.preview_group.isVisibleTo(capture))
        capture.save()
        with closing(db.connect(self.database)) as con:
            row = con.execute("SELECT duty, measure FROM completions").fetchone()
            self.assertEqual(tuple(row), ("__new__", "__new__"))

    def test_new_archives_in_one_folder_get_distinct_export_folders(self):
        first = self.root / "first.db"
        second = self.root / "second.db"
        self.window.create_new_archive(first)
        self.window.create_new_archive(second)
        self.assertEqual(self.store.export_for(first), (self.root / "first-exports").resolve())
        self.assertEqual(self.store.export_for(second), (self.root / "second-exports").resolve())

    def test_nested_save_action_cannot_commit_twice(self):
        db.init_db(self.database)
        self.window.open_archive(self.database)
        capture = self.window.capture
        capture.outcome.setPlainText("Invented nested-save result")
        capture.measurement_status.setCurrentIndex(capture.measurement_status.findData("not_applicable"))
        capture.show_preview()
        original_save = capture.service.save_capture

        def save_with_nested_action(draft):
            capture.save()
            return original_save(draft)

        with patch.object(capture.service, "save_capture", side_effect=save_with_nested_action) as save:
            capture.save()
            save.assert_called_once()
        with closing(db.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM completions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
