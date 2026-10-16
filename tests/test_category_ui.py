"""Offscreen checks for category picking and management in entry forms."""

import os
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PYSIDE6_AVAILABLE = find_spec("PySide6") is not None

if PYSIDE6_AVAILABLE:
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QListWidget,
        QMessageBox,
        QPushButton,
    )

    from saga.application import ArchiveService, create_archive
    from saga.desktop.categories import CreateCategoryDialog, ManageCategoriesDialog
    from saga.desktop.task_entry import AddTaskWidget


class CategoryService:
    def __init__(self):
        self.options = [
            {"name": "work", "archived": False, "in_use": True, "counts_toward_review": True},
            {"name": "personal", "archived": False, "in_use": False, "counts_toward_review": True},
        ]
        self.export_error = None

    def references(self):
        names = [option["name"] for option in self.options if not option["archived"]]
        return {"categories": names, "roles": {"work": ["Planning"], "personal": []},
                "measures": ["items"]}

    def category_options(self):
        return [dict(option) for option in self.options]

    def create_category(self, name, counts_toward_review=True):
        if not name:
            raise ValueError("Name is required")
        self.options.append({"name": name, "archived": False, "in_use": False,
                             "counts_toward_review": counts_toward_review})
        return self.export_error

    def archive_category(self, name):
        self._option(name)["archived"] = True
        return self.export_error

    def delete_category(self, name):
        self.options = [option for option in self.options if option["name"] != name]
        return self.export_error

    def restore_category(self, name):
        self._option(name)["archived"] = False
        return self.export_error

    def _option(self, name):
        return next(option for option in self.options if option["name"] == name)


@unittest.skipUnless(PYSIDE6_AVAILABLE, "Optional PySide6 desktop dependency is not installed")
class CategoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = CategoryService()
        self.widget = AddTaskWidget(self.service)
        self.addCleanup(self.widget.close)

    def test_create_selects_category_and_preserves_both_drafts(self):
        task = self.widget.task
        completed = self.widget.completed
        self.assertIsInstance(task.category.button, QPushButton)
        task.title.setText("Draft task")
        task.role.setCurrentIndex(task.role.findData("Planning"))
        completed.outcome.setPlainText("Draft accomplishment")
        completed.measurement_status.setCurrentIndex(completed.measurement_status.findData("measured"))
        completed.quantity.setText("7")
        dialog = CreateCategoryDialog(self.service)
        dialog.name.setText("learning")
        dialog.reviews.setChecked(False)
        dialog.create()
        self.assertEqual(dialog.created_name, "learning")
        self.assertFalse(self.service._option("learning")["counts_toward_review"])
        task.category.categoriesChanged.emit()
        self.assertEqual(task.role.currentData(), "Planning")
        task.category.setCurrentIndex(task.category.findData("learning"))
        self.assertEqual(task.draft()["category"], "learning")
        self.assertEqual(task.title.text(), "Draft task")
        self.assertEqual(completed.outcome.toPlainText(), "Draft accomplishment")
        self.assertEqual(completed.quantity.text(), "7")
        self.assertEqual(completed.measurement_status.currentData(), "measured")
        self.assertTrue(task.has_unsaved_draft)
        dialog.close()

    def test_manage_changes_refresh_even_when_dialog_is_dismissed(self):
        picker = self.widget.task.category
        chooser = QDialog()
        choices = QListWidget()
        choices.addItems(["work", "personal"])

        def dismiss_after_delete(dialog):
            self.service.delete_category("personal")
            dialog.changed = True
            return QDialog.DialogCode.Rejected

        with patch.object(ManageCategoriesDialog, "exec", dismiss_after_delete):
            picker._manage_from_chooser(chooser, choices)
        self.assertEqual(picker.findData("personal"), -1)
        self.assertEqual(self.widget.completed.category.findData("personal"), -1)
        chooser.close()

    def test_hidden_preview_returns_to_edit_when_category_disappears(self):
        task = self.widget.task
        task.title.setText("Keep this draft")
        task._preview_draft = task.draft()
        task.form_group.hide()
        task.preview_group.show()
        self.service.archive_category("work")
        task.category.categoriesChanged.emit()
        self.assertIsNone(task._preview_draft)
        self.assertFalse(task.form_group.isHidden())
        self.assertTrue(task.preview_group.isHidden())
        self.assertEqual(task.title.text(), "Keep this draft")

    def test_cancelled_deletion_leaves_category_and_drafts(self):
        self.widget.task.title.setText("Keep this")
        dialog = ManageCategoriesDialog(self.service)
        dialog.list.setCurrentRow(1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            dialog._act()
        self.assertIn("personal", self.widget.task.category._names)
        self.assertEqual(self.widget.task.title.text(), "Keep this")
        self.assertFalse(dialog.changed)
        dialog.close()

    def test_delete_unused_and_archive_used_clear_disappeared_selection(self):
        task = self.widget.task
        completed = self.widget.completed
        task.category.setCurrentIndex(task.category.findData("personal"))
        task.title.setText("Keep title")
        completed.outcome.setPlainText("Keep outcome")
        dialog = ManageCategoriesDialog(self.service)
        dialog.list.setCurrentRow(1)
        self.assertEqual(dialog.action.text(), "Delete category")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._act()
        self.assertTrue(dialog.changed)
        task.category.categoriesChanged.emit()
        self.assertIsNone(task.category.currentData())
        self.assertIn("no longer available", task.status.text())
        self.assertEqual(task.title.text(), "Keep title")
        dialog.list.setCurrentRow(0)
        self.assertEqual(dialog.action.text(), "Archive category")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._act()
        task.category.categoriesChanged.emit()
        self.assertIsNone(completed.category.currentData())
        self.assertEqual(completed.outcome.toPlainText(), "Keep outcome")
        self.assertTrue(self.service._option("work")["archived"])
        dialog._act()  # Restore does not require a destructive-action confirmation.
        task.category.categoriesChanged.emit()
        self.assertIn("work", completed.category._names)
        dialog.close()

    def test_create_error_and_export_error_are_distinct(self):
        dialog = CreateCategoryDialog(self.service)
        dialog.create()
        self.assertIn("Could not create category", dialog.status.text())
        self.assertIsNone(dialog.created_name)
        self.service.export_error = "disk full"
        dialog.name.setText("learning")
        with patch.object(QMessageBox, "warning") as warning:
            dialog.create()
        self.assertEqual(dialog.created_name, "learning")
        self.assertIn("learning", [option["name"] for option in self.service.options])
        self.assertIn("Export failed", warning.call_args.args[2])
        dialog.close()

    def test_real_archive_create_save_archive_and_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = ArchiveService(create_archive(Path(temporary) / 'categories.db'))
            widget = AddTaskWidget(service)
            self.addCleanup(widget.close)
            create = CreateCategoryDialog(service)
            create.name.setText('My category')
            create.create()
            self.assertEqual(create.result(), QDialog.DialogCode.Accepted)
            widget.task.category.categoriesChanged.emit()
            task = widget.task
            task.category.setCurrentIndex(task.category.findData('My category'))
            task.title.setText('Task using my category')
            task.show_preview()
            task.save()
            self.assertTrue(task._committed)
            self.assertEqual(service.today()['undated'][0]['category'], 'My category')
            manage = ManageCategoriesDialog(service)
            manage.refresh('My category')
            self.assertEqual(manage.action.text(), 'Archive category')
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
                manage._act()
            widget.task.category.categoriesChanged.emit()
            self.assertEqual(widget.completed.category.findData('My category'), -1)
            self.assertEqual(service.today()['undated'][0]['category'], 'My category')
            self.assertEqual(manage.action.text(), 'Restore category')
            manage._act()
            widget.task.category.categoriesChanged.emit()
            self.assertGreaterEqual(widget.completed.category.findData('My category'), 0)
            manage.close()
            create.close()
            widget.close()


if __name__ == "__main__":
    unittest.main()
