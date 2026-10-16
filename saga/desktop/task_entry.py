"""Previewed entry for open tasks and completed accomplishments."""

import sqlite3

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from saga.desktop.capture import CaptureWidget


class TaskWidget(QWidget):
    """Keep an open task draft until the reviewed values are saved."""

    saved = Signal()
    NEW = object()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._dirty = False
        self._committed = False
        self._saving = False
        self._preview_draft = None

        self.title = QLineEdit()
        self.title.setPlaceholderText("What needs to be done?")
        self.title.setAccessibleName("Task title")
        self.category = QComboBox()
        self.category.setAccessibleName("Category")
        self.role = QComboBox()
        self.role.setAccessibleName("Role or responsibility")
        self.new_role = QLineEdit()
        self.new_role.setPlaceholderText("New role or responsibility")
        self.new_role.setAccessibleName("New role or responsibility")
        self.has_due_date = QCheckBox("Set a due date")
        self.due_date = QDateEdit(QDate.currentDate())
        self.due_date.setCalendarPopup(True)
        self.due_date.setDisplayFormat("yyyy-MM-dd")
        self.due_date.setAccessibleName("Due date")
        self.due_date.setEnabled(False)

        form = QFormLayout()
        form.addRow("Task", self.title)
        form.addRow("Category", self.category)
        form.addRow("Role or responsibility", self.role)
        form.addRow("New role", self.new_role)
        form.addRow("Due date", self.has_due_date)
        form.addRow("Choose date", self.due_date)
        self.form_layout = form
        self.form_group = QGroupBox("Something to do")
        self.form_group.setLayout(form)

        self.preview_button = QPushButton("Preview")
        self.preview_button.setProperty('primary', True)
        self.preview_button.clicked.connect(self.show_preview)
        self.cancel_button = QPushButton("Cancel draft")
        self.cancel_button.clicked.connect(self.cancel_draft)
        form_actions = QHBoxLayout()
        form_actions.addWidget(self.preview_button)
        form_actions.addWidget(self.cancel_button)

        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setAccessibleName("Task preview")
        self.preview_group = QGroupBox("Review before saving")
        preview_layout = QVBoxLayout(self.preview_group)
        preview_layout.addWidget(self.preview_text)
        preview_actions = QHBoxLayout()
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self.edit_draft)
        self.save_button = QPushButton("Save task")
        self.save_button.setProperty('primary', True)
        self.save_button.clicked.connect(self.save)
        self.retry_button = QPushButton("Retry export")
        self.retry_button.clicked.connect(self.retry_export)
        self.retry_button.hide()
        self.new_button = QPushButton("New task")
        self.new_button.clicked.connect(self.reset_form)
        self.new_button.hide()
        for button in (self.edit_button, self.save_button, self.retry_button, self.new_button):
            preview_actions.addWidget(button)
        preview_layout.addLayout(preview_actions)
        self.preview_group.hide()

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Task entry status")
        layout = QVBoxLayout(self)
        layout.addWidget(self.form_group)
        layout.addLayout(form_actions)
        layout.addWidget(self.preview_group)
        layout.addWidget(self.status)
        layout.addStretch()

        self.title.textChanged.connect(self._mark_dirty)
        self.category.currentIndexChanged.connect(self._category_changed)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.new_role.textChanged.connect(self._mark_dirty)
        self.has_due_date.toggled.connect(self._due_date_changed)
        self.due_date.dateChanged.connect(self._mark_dirty)
        self.reload_references()
        self._dirty = False

    @property
    def has_unsaved_draft(self):
        return self._dirty and not self._committed

    def _mark_dirty(self, *_):
        self._dirty = True

    def reload_references(self):
        refs = self.service.references()
        self._roles_by_category = refs["roles"]
        self.category.blockSignals(True)
        self.category.clear()
        for name in refs["categories"]:
            self.category.addItem(name, name)
        self.category.blockSignals(False)
        self._category_changed()

    def _category_changed(self, *_):
        self.role.blockSignals(True)
        self.role.clear()
        self.role.addItem("No role assigned", None)
        for name in self._roles_by_category.get(self.category.currentData(), []):
            self.role.addItem(name, name)
        self.role.addItem("Add a new role…", self.NEW)
        self.role.blockSignals(False)
        self._role_changed()
        self._mark_dirty()

    def _role_changed(self, *_):
        self.form_layout.setRowVisible(self.new_role, self.role.currentData() is self.NEW)
        self._mark_dirty()

    def _due_date_changed(self, checked):
        self.due_date.setEnabled(checked)
        self._mark_dirty()

    def draft(self):
        role = self.role.currentData()
        return {
            "title": self.title.text(),
            "category": self.category.currentData(),
            "duty": None if role is self.NEW else role,
            "new_duty": self.new_role.text().strip() if role is self.NEW else None,
            "due_date": self.due_date.date().toString("yyyy-MM-dd") if self.has_due_date.isChecked() else None,
        }

    def show_preview(self):
        draft = self.draft()
        try:
            self.service.preview_task(draft)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Check the draft: {exc}")
            return
        self._preview_draft = draft
        details = (
            ("Task", draft["title"].strip()),
            ("Category", draft["category"]),
            ("Role", draft["new_duty"] or draft["duty"] or "None"),
            ("Due", draft["due_date"] or "No due date"),
        )
        self.preview_text.setPlainText("\n".join(f"{label}: {value}" for label, value in details))
        self.form_group.setEnabled(False)
        self.form_group.hide()
        self.preview_button.hide()
        self.preview_group.show()
        self.status.setText("Review these values, then save or edit.")
        self.save_button.setFocus()

    def edit_draft(self):
        if self._committed:
            return
        self._preview_draft = None
        self.preview_group.hide()
        self.form_group.show()
        self.form_group.setEnabled(True)
        self.preview_button.show()
        self.status.clear()
        self.title.setFocus()

    def save(self):
        if self._committed or self._saving or self._preview_draft is None:
            return
        self._saving = True
        self.save_button.setEnabled(False)
        try:
            result = self.service.save_task(self._preview_draft)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Could not save: {exc}. Your draft is still here.")
            self.save_button.setEnabled(True)
            return
        finally:
            self._saving = False
        self._committed = True
        self._dirty = False
        self.edit_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.new_button.show()
        if result.export_error:
            self.status.setText(f"Saved task #{result.task_id}. Export failed: {result.export_error}. Retry export only.")
            self.retry_button.show()
        else:
            self.status.setText(f"Saved task #{result.task_id}.")
        self.saved.emit()

    def retry_export(self):
        if not self._committed:
            return
        try:
            self.service.retry_export()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Task is saved. Export retry failed: {exc}")
            return
        self.retry_button.hide()
        self.status.setText("Task is saved. Export completed.")

    def confirm_discard(self):
        if not self.has_unsaved_draft:
            return True
        answer = QMessageBox.question(
            self, "Discard draft?", "Discard this unsaved task draft?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def cancel_draft(self):
        if self.confirm_discard():
            self.reset_form()

    def reset_form(self):
        try:
            self.reload_references()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Could not start a new draft: {exc}. The current view is still available.")
            return
        self._committed = False
        self._preview_draft = None
        self.form_group.show()
        self.form_group.setEnabled(True)
        self.preview_button.show()
        self.save_button.setEnabled(True)
        self.edit_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.preview_group.hide()
        self.retry_button.hide()
        self.new_button.hide()
        self.title.clear()
        self.category.setCurrentIndex(0)
        self.role.setCurrentIndex(0)
        self.new_role.clear()
        self.has_due_date.setChecked(False)
        self.due_date.setDate(QDate.currentDate())
        self.status.clear()
        self._dirty = False
        self.title.setFocus()


class AddTaskWidget(QWidget):
    """Choose future work or a completed accomplishment without losing either draft."""

    saved = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._service = service
        self.task = TaskWidget(service)
        self.completed = CaptureWidget(service)

        self.todo_button = QPushButton("Something to do")
        self.todo_button.setCheckable(True)
        self.done_button = QPushButton("Already done")
        self.done_button.setCheckable(True)
        choices = QButtonGroup(self)
        choices.setExclusive(True)
        choices.addButton(self.todo_button)
        choices.addButton(self.done_button)
        self.todo_button.setChecked(True)

        self.stack = QStackedWidget()
        for child in (self.task, self.completed):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(child)
            self.stack.addWidget(scroll)
        self.todo_button.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.done_button.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        self.task.saved.connect(lambda: self._entry_saved(self.completed))
        self.completed.saved.connect(lambda: self._entry_saved(self.task))

        layout = QVBoxLayout(self)
        heading = QLabel("Add task")
        heading.setObjectName("addTaskTitle")
        layout.addWidget(heading)
        choice_row = QHBoxLayout()
        choice_row.addWidget(self.todo_button)
        choice_row.addWidget(self.done_button)
        layout.addLayout(choice_row)
        layout.addWidget(self.stack, 1)

    def _entry_saved(self, other):
        # Share newly registered roles with an untouched form. Never replace a
        # draft or a saved result that may still need its export retried.
        if not other.has_unsaved_draft and not other._committed:
            other.reset_form()
        self.saved.emit()

    @property
    def service(self):
        return self._service

    @service.setter
    def service(self, value):
        self._service = value
        self.task.service = value
        self.completed.service = value

    @property
    def has_unsaved_draft(self):
        return self.task.has_unsaved_draft or self.completed.has_unsaved_draft

    def confirm_discard(self):
        if not self.has_unsaved_draft:
            return True
        answer = QMessageBox.question(
            self, "Discard drafts?", "Discard all unsaved task and accomplishment drafts?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard
