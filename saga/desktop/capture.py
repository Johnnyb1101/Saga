"""Manual standalone accomplishment capture, with an explicit review step."""

import sqlite3

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from saga.desktop.categories import CategoryPicker


class CaptureWidget(QWidget):
    """Keep draft values in widgets until the user confirms a preview."""

    saved = Signal()
    NEW = object()

    def __init__(self, service, parent=None, *, tracked_task=None):
        super().__init__(parent)
        self.service = service
        self.tracked_task = dict(tracked_task) if tracked_task is not None else None
        self._dirty = False
        self._committed = False
        self._saving = False
        self._preview_draft = None

        self.category = CategoryPicker(service)
        self.category.setAccessibleName("Category")
        self.role = QComboBox()
        self.role.setAccessibleName("Role (optional)")
        role_explanation = "What role was this part of? For example: parent, team lead, or volunteer."
        self.role.setAccessibleDescription(role_explanation)
        self.role_help = QLabel(role_explanation)
        self.role_help.setTextFormat(Qt.TextFormat.PlainText)
        self.role_help.setWordWrap(True)
        self.new_role = QLineEdit()
        self.new_role.setPlaceholderText("e.g. Team lead")
        self.new_role.setAccessibleName("Role name")
        self.outcome = QPlainTextEdit()
        self.outcome.setPlaceholderText("What happened and why did it matter?")
        self.outcome.setAccessibleName("Outcome")
        self.outcome.setTabChangesFocus(True)
        self.outcome.setMinimumHeight(110)
        self.use_today = QCheckBox("Use today and the time of save")
        self.use_today.setChecked(True)
        self.completed_date = QDateEdit(QDate.currentDate())
        self.completed_date.setCalendarPopup(True)
        self.completed_date.setDisplayFormat("yyyy-MM-dd")
        self.completed_date.setAccessibleName("Completion date")
        self.completed_date.setEnabled(False)
        self.measurement_status = QComboBox()
        self.measurement_status.setAccessibleName("Can you put a number to this?")
        self.measurement_status.addItem("Choose an option…", None)
        self.measurement_status.addItem("Yes", "measured")
        self.measurement_status.addItem("No number needed", "not_applicable")
        self.measurement_status.addItem("I’ll add a number later", "unknown")
        self.measure = QComboBox()
        self.measure.setAccessibleName("What are you counting?")
        self.new_measure = QLineEdit()
        self.new_measure.setPlaceholderText("e.g. applications")
        self.new_measure.setAccessibleName("What are you counting? Type a name")
        self.quantity = QLineEdit()
        self.quantity.setPlaceholderText("e.g. 3")
        self.quantity.setAccessibleName("Amount")
        self.remind_on = QDateEdit(QDate.currentDate())
        self.remind_on.setCalendarPopup(True)
        self.remind_on.setDisplayFormat("yyyy-MM-dd")
        self.remind_on.setAccessibleName("When should Saga remind you?")
        self.flagged = QCheckBox("Flag for review")

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        if tracked_task is None:
            form.addRow("Category", self.category)
            role_label = QLabel("Role (optional)")
            role_label.setBuddy(self.role)
            form.addRow(role_label)
            form.addRow(self.role_help)
            form.addRow(self.role)
            form.addRow("Role name", self.new_role)
        else:
            self.task_context = QLabel(self._tracked_context_text())
            self.task_context.setTextFormat(Qt.TextFormat.PlainText)
            self.task_context.setWordWrap(True)
            self.task_context.setAccessibleName("Task details")
            form.addRow(self.task_context)
        form.addRow("Outcome", self.outcome)
        form.addRow("Completion date", self.use_today)
        form.addRow("Choose date", self.completed_date)
        self.measurement_help = QLabel("For example: 3 applications submitted, 2 hours volunteered, or 5 people helped.")
        self.measurement_help.setTextFormat(Qt.TextFormat.PlainText)
        self.measurement_help.setWordWrap(True)
        measurement_label = QLabel("Can you put a number to this?")
        measurement_label.setBuddy(self.measurement_status)
        form.addRow(measurement_label)
        form.addRow(self.measurement_help)
        form.addRow(self.measurement_status)
        form.addRow("What are you counting?", self.measure)
        form.addRow("Type it here", self.new_measure)
        form.addRow("Amount", self.quantity)
        form.addRow("When should Saga remind you?", self.remind_on)
        form.addRow("Review", self.flagged)
        self.form_layout = form
        self.form_group = QGroupBox("Completed work" if tracked_task is None else "Complete task")
        self.form_group.setLayout(form)

        self.preview_button = QPushButton("Preview")
        self.preview_button.setProperty('primary', True)
        self.preview_button.clicked.connect(self.show_preview)
        self.cancel_button = QPushButton("Cancel" if tracked_task is not None else "Cancel draft")
        self.cancel_button.clicked.connect(self.cancel_draft)
        form_actions = QHBoxLayout()
        form_actions.addWidget(self.preview_button)
        form_actions.addWidget(self.cancel_button)

        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setAccessibleName("Accomplishment preview")
        self.preview_group = QGroupBox("Review before saving")
        preview_layout = QVBoxLayout(self.preview_group)
        preview_layout.addWidget(self.preview_text)
        preview_actions = QHBoxLayout()
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self.edit_draft)
        self.save_button = QPushButton("Mark complete" if tracked_task is not None else "Save accomplishment")
        self.save_button.setProperty('primary', True)
        self.save_button.clicked.connect(self.save)
        self.retry_button = QPushButton("Retry export")
        self.retry_button.clicked.connect(self.retry_export)
        self.retry_button.hide()
        self.new_button = QPushButton("New accomplishment")
        self.new_button.clicked.connect(self.reset_form)
        self.new_button.hide()
        preview_actions.addWidget(self.edit_button)
        preview_actions.addWidget(self.save_button)
        preview_actions.addWidget(self.retry_button)
        if tracked_task is None:
            preview_actions.addWidget(self.new_button)
        preview_layout.addLayout(preview_actions)
        self.preview_group.hide()

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Capture status")
        layout = QVBoxLayout(self)
        layout.addWidget(self.form_group)
        layout.addLayout(form_actions)
        layout.addWidget(self.preview_group)
        layout.addWidget(self.status)

        self.category.currentIndexChanged.connect(self._category_changed)
        self.category.categoriesChanged.connect(self.refresh_categories)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.measure.currentIndexChanged.connect(self._measure_changed)
        self.measurement_status.currentIndexChanged.connect(self._measurement_changed)
        self.use_today.toggled.connect(self._today_changed)
        for field in (self.new_role, self.new_measure, self.quantity):
            field.textChanged.connect(self._mark_dirty)
        self.outcome.textChanged.connect(self._mark_dirty)
        for field in (self.completed_date, self.remind_on):
            field.dateChanged.connect(self._mark_dirty)
        self.flagged.toggled.connect(self._mark_dirty)
        self.reload_references()
        self._measurement_changed()
        self._dirty = False

    def _tracked_context_text(self):
        task = self.tracked_task
        return "\n".join((
            f"Task: {task['title']}",
            f"Category: {task['category']}",
            f"Role: {task.get('duty') or 'None'}",
            f"Due: {task.get('due_date') or 'No due date'}",
        ))

    @property
    def has_unsaved_draft(self):
        return self._dirty and not self._committed

    def _mark_dirty(self, *_):
        self._dirty = True

    def reload_references(self):
        refs = self.service.references()
        was_dirty = self._dirty
        selected_category = self.category.currentData()
        selected_measure = self.measure.currentData()
        self._roles_by_category = refs["roles"]
        self.category.blockSignals(True)
        self.category.set_categories(refs["categories"])
        self.category.blockSignals(False)
        self._category_changed()
        self.measure.blockSignals(True)
        self.measure.clear()
        for name in refs["measures"]:
            self.measure.addItem(name, name)
        self.measure.addItem("Add something else…", self.NEW)
        measure_index = self.measure.findData(selected_measure)
        self.measure.setCurrentIndex(max(measure_index, 0))
        self.measure.blockSignals(False)
        self._measure_changed()
        self._dirty = was_dirty or (selected_category is not None and self.category.currentData() is None)
        if selected_category is not None and self.category.currentData() is None:
            self._preview_draft = None
            if not self.preview_group.isHidden():
                self.edit_draft()
            self.status.setText("The selected category is no longer available. Choose another category to save this draft.")

    def refresh_categories(self):
        try:
            self.reload_references()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Category was saved, but the list could not refresh: {exc}")

    def _category_changed(self, *_):
        selected_role = self.role.currentData()
        self.role.blockSignals(True)
        self.role.clear()
        self.role.addItem("No role — leave blank if it doesn’t apply", None)
        for name in self._roles_by_category.get(self.category.currentData(), []):
            self.role.addItem(name, name)
        self.role.addItem("Create a role…", self.NEW)
        role_index = self.role.findData(selected_role)
        self.role.setCurrentIndex(max(role_index, 0))
        self.role.blockSignals(False)
        self._role_changed()
        self._mark_dirty()

    def _role_changed(self, *_):
        if self.tracked_task is None:
            self.form_layout.setRowVisible(self.new_role, self.role.currentData() is self.NEW)
        self._mark_dirty()

    def _measure_changed(self, *_):
        self.form_layout.setRowVisible(
            self.new_measure,
            self.measure.currentData() is self.NEW and self.measurement_status.currentData() == "measured",
        )
        self._mark_dirty()

    def _measurement_changed(self, *_):
        status = self.measurement_status.currentData()
        measured = status == "measured"
        self.form_layout.setRowVisible(self.measure, measured)
        self.form_layout.setRowVisible(self.quantity, measured)
        self.form_layout.setRowVisible(self.new_measure, measured and self.measure.currentData() is self.NEW)
        self.form_layout.setRowVisible(self.remind_on, status == "unknown")
        self._mark_dirty()

    def _today_changed(self, checked):
        self.completed_date.setEnabled(not checked)
        self._mark_dirty()

    def draft(self):
        role = self.role.currentData()
        measure = self.measure.currentData()
        status = self.measurement_status.currentData()
        category = self.tracked_task["category"] if self.tracked_task is not None else self.category.currentData()
        duty = self.tracked_task.get("duty") if self.tracked_task is not None else (None if role is self.NEW else role)
        return {
            "category": category,
            "outcome": self.outcome.toPlainText(),
            "completed_date": None if self.use_today.isChecked() else self.completed_date.date().toString("yyyy-MM-dd"),
            "duty": duty,
            "new_duty": self.new_role.text().strip() if self.tracked_task is None and role is self.NEW else None,
            "measurement_status": status,
            "measure": measure if status == "measured" and measure is not self.NEW else None,
            "new_measure": self.new_measure.text().strip() if status == "measured" and measure is self.NEW else None,
            "quantity": self.quantity.text().strip() if status == "measured" else None,
            "remind_on": self.remind_on.date().toString("yyyy-MM-dd") if status == "unknown" else None,
            "flagged": self.flagged.isChecked(),
        }

    def show_preview(self):
        draft = self.draft()
        try:
            if self.tracked_task is None:
                self.service.preview_capture(draft)
            else:
                self.service.preview_completion(self.tracked_task, draft)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Check the draft: {exc}")
            return
        self._preview_draft = draft
        details = [
            *([("Task", self.tracked_task["title"])] if self.tracked_task is not None else []),
            ("Category", draft["category"]),
            ("Role", draft["new_duty"] or draft["duty"] or "None"),
            ("Outcome", draft["outcome"].strip()),
            ("Completed", draft["completed_date"] or "Today, at time of save"),
            ("Can you put a number to this?", {
                "measured": "Yes", "not_applicable": "No number needed",
                "unknown": "I’ll add a number later",
            }[draft["measurement_status"]]),
        ]
        if draft["measurement_status"] == "measured":
            details.extend((("What are you counting?", draft["new_measure"] or draft["measure"]),
                            ("Amount", draft["quantity"])))
        if draft["remind_on"]:
            details.append(("When should Saga remind you?", draft["remind_on"]))
        details.append(("Flagged for review", "Yes" if draft["flagged"] else "No"))
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
        self.outcome.setFocus()

    def save(self):
        if self._committed or self._saving or self._preview_draft is None:
            return
        self._saving = True
        self.save_button.setEnabled(False)
        try:
            if self.tracked_task is None:
                result = self.service.save_capture(self._preview_draft)
            else:
                result = self.service.save_completion(self.tracked_task, self._preview_draft)
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
        if self.tracked_task is None:
            self.new_button.show()
        if result.export_error:
            saved = "Task marked complete" if self.tracked_task is not None else f"Saved accomplishment #{result.completion_id}"
            self.status.setText(f"{saved}. Export failed: {result.export_error}. Retry export only.")
            self.retry_button.show()
        else:
            self.status.setText("Task marked complete." if self.tracked_task is not None else
                                f"Saved accomplishment #{result.completion_id}.")
        self.saved.emit()

    def retry_export(self):
        if not self._committed:
            return
        try:
            self.service.retry_export()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Accomplishment is saved. Export retry failed: {exc}")
            return
        self.retry_button.hide()
        self.status.setText("Task is complete. Export completed." if self.tracked_task is not None else
                            "Accomplishment is saved. Export completed.")

    def confirm_discard(self):
        if not self.has_unsaved_draft:
            return True
        answer = QMessageBox.question(
            self, "Discard draft?", "Discard this unsaved accomplishment draft?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def cancel_draft(self):
        if self.tracked_task is not None:
            self.window().close()
        elif self.confirm_discard():
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
        self.category.setCurrentIndex(0)
        self.role.setCurrentIndex(0)
        self.new_role.clear()
        self.outcome.clear()
        self.use_today.setChecked(True)
        self.completed_date.setDate(QDate.currentDate())
        self.measurement_status.setCurrentIndex(0)
        self.measure.setCurrentIndex(0)
        self.new_measure.clear()
        self.quantity.clear()
        self.remind_on.setDate(QDate.currentDate())
        self.flagged.setChecked(False)
        self.status.clear()
        self._dirty = False
        self.outcome.setFocus()
