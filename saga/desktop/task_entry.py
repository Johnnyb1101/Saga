"""Previewed entry for open tasks and completed accomplishments."""

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
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from saga.application import schedule_description
from saga.desktop.categories import CategoryPicker
from saga.desktop.completion_details import CompletionDetails


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
        self.has_due_date = QCheckBox("Set a due date")
        self.due_date = QDateEdit(QDate.currentDate())
        self.due_date.setCalendarPopup(True)
        self.due_date.setDisplayFormat("yyyy-MM-dd")
        self.due_date.setAccessibleName("Due date")
        self.due_date.setEnabled(False)
        self.repeat_task = QCheckBox('Repeat this task')
        self.repeat_frequency = QComboBox()
        for label, value in (('Daily', 'daily'), ('Weekly', 'weekly'),
                             ('Every two weeks', 'biweekly'), ('Monthly', 'monthly'),
                             ('Twice a month', 'twice_monthly')):
            self.repeat_frequency.addItem(label, value)
        self.repeat_frequency.setAccessibleName('Repeat schedule')
        self.repeat_interval = QSpinBox()
        self.repeat_interval.setRange(1, 999)
        self.repeat_interval.setAccessibleName('Repeat every how many days, weeks, or months')
        self.repeat_weekday = QComboBox()
        self.repeat_weekday.addItems(['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                                     'Friday', 'Saturday', 'Sunday'])
        self.repeat_weekday.setCurrentIndex(QDate.currentDate().dayOfWeek() - 1)
        self.repeat_weekday.setAccessibleName('Day of the week')
        self.repeat_month_day = QSpinBox()
        self.repeat_month_day.setRange(1, 31)
        self.repeat_month_day.setValue(QDate.currentDate().day())
        self.repeat_month_day.setAccessibleName('Day of the month')
        self.repeat_second_day = QSpinBox()
        self.repeat_second_day.setRange(1, 31)
        self.repeat_second_day.setValue(15)
        self.repeat_second_day.setAccessibleName('Second day of the month')
        self.schedule_help = QLabel()
        self.schedule_help.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Task", self.title)
        form.addRow("Category", self.category)
        form.addRow(self.role_help)
        form.addRow("Role (optional)", self.role)
        form.addRow("Role name", self.new_role)
        form.addRow("Due date", self.has_due_date)
        form.addRow("Choose date", self.due_date)
        form.addRow(self.repeat_task)
        form.addRow('Repeat', self.repeat_frequency)
        form.addRow('Every (days, weeks, or months)', self.repeat_interval)
        form.addRow('Day of the week', self.repeat_weekday)
        form.addRow('Day of the month', self.repeat_month_day)
        form.addRow('Second day of the month', self.repeat_second_day)
        form.addRow(self.schedule_help)
        self.already_finished = QCheckBox("I’ve already finished this")
        self.already_finished.setAccessibleName("I’ve already finished this")
        form.addRow(self.already_finished)
        self.completion_details = CompletionDetails(service)
        form.addRow(self.completion_details)
        form.setRowVisible(self.completion_details, False)
        form.setRowVisible(self.repeat_frequency, False)
        form.setRowVisible(self.repeat_interval, False)
        for field in (self.repeat_weekday, self.repeat_month_day, self.repeat_second_day, self.schedule_help):
            form.setRowVisible(field, False)
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
        self.category.categoriesChanged.connect(self.refresh_categories)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.new_role.textChanged.connect(self._mark_dirty)
        self.has_due_date.toggled.connect(self._due_date_changed)
        self.due_date.dateChanged.connect(self._mark_dirty)
        self.repeat_task.toggled.connect(self._repeat_changed)
        self.repeat_frequency.currentIndexChanged.connect(self._schedule_changed)
        self.repeat_interval.valueChanged.connect(self._mark_dirty)
        self.repeat_weekday.currentIndexChanged.connect(self._mark_dirty)
        self.repeat_month_day.valueChanged.connect(self._mark_dirty)
        self.repeat_second_day.valueChanged.connect(self._mark_dirty)
        self.already_finished.toggled.connect(self._finished_changed)
        self.completion_details.changed.connect(self._mark_dirty)
        self.reload_references()
        self._dirty = False

    @property
    def has_unsaved_draft(self):
        return self._dirty and not self._committed

    def _mark_dirty(self, *_):
        self._dirty = True

    def _finished_changed(self, finished):
        self.form_layout.setRowVisible(self.completion_details, finished)
        self.form_layout.labelForField(self.title).setText("What did you finish?" if finished else "Task")
        self.title.setPlaceholderText("What did you finish?" if finished else "What needs to be done?")
        self.title.setAccessibleName("What did you finish?" if finished else "Task title")
        for field in (self.has_due_date, self.due_date, self.repeat_task, self.repeat_frequency,
                      self.repeat_interval, self.repeat_weekday, self.repeat_month_day,
                      self.repeat_second_day):
            field.setEnabled(False if finished else self._schedule_field_enabled(field))
        self.form_group.setTitle("Something finished" if finished else "Something to do")
        self.save_button.setText("Save accomplishment" if finished else "Save task")
        self._mark_dirty()

    def _schedule_field_enabled(self, field):
        if field is self.has_due_date:
            return not self.repeat_task.isChecked()
        if field is self.due_date:
            return self.has_due_date.isChecked()
        return True

    def reload_references(self):
        refs = self.service.references()
        was_dirty = self._dirty
        selected_category = self.category.currentData()
        selected_role = self.role.currentData()
        self._roles_by_category = refs["roles"]
        self.category.blockSignals(True)
        self.category.set_categories(refs["categories"])
        self.category.blockSignals(False)
        self._category_changed()
        missing_measure = self.completion_details.reload_references(refs["measures"])
        missing_category = selected_category is not None and self.category.currentData() is None
        missing_role = selected_role is not None and selected_role is not self.NEW and self.role.currentData() is None
        self._dirty = was_dirty or missing_category or missing_role or missing_measure
        if missing_category or missing_role or missing_measure:
            self._preview_draft = None
            if not self.preview_group.isHidden():
                self.edit_draft()
            missing = "category" if missing_category else "role" if missing_role else "measurement unit"
            self.status.setText(f"The selected {missing} is no longer available. Choose another to save this draft.")

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
        self.role.setCurrentIndex(role_index if role_index >= 0 else -1 if selected_role is not None else 0)
        self.role.blockSignals(False)
        self._role_changed()
        self._mark_dirty()

    def _role_changed(self, *_):
        self.form_layout.setRowVisible(self.new_role, self.role.currentData() is self.NEW)
        self._mark_dirty()

    def _due_date_changed(self, checked):
        if self.repeat_task.isChecked() and not checked:
            self.has_due_date.setChecked(True)
            return
        self.due_date.setEnabled(checked and not self.already_finished.isChecked())
        self._mark_dirty()

    def _repeat_changed(self, checked):
        self.form_layout.setRowVisible(self.repeat_frequency, checked)
        if checked:
            self.has_due_date.setChecked(True)
        self.has_due_date.setEnabled(not checked and not self.already_finished.isChecked())
        self._schedule_changed()

    def _schedule_changed(self, *_):
        checked = self.repeat_task.isChecked()
        frequency = self.repeat_frequency.currentData()
        weekly = frequency in ('weekly', 'biweekly')
        monthly = frequency in ('monthly', 'twice_monthly')
        self.form_layout.setRowVisible(self.repeat_interval, checked and frequency in ('daily', 'weekly', 'monthly'))
        self.form_layout.setRowVisible(self.repeat_weekday, checked and weekly)
        self.form_layout.setRowVisible(self.repeat_month_day, checked and monthly)
        self.form_layout.setRowVisible(self.repeat_second_day, checked and frequency == 'twice_monthly')
        self.form_layout.setRowVisible(self.schedule_help, checked)
        self.form_layout.labelForField(self.due_date).setText('Start on or after' if checked else 'Choose date')
        self.schedule_help.setText(
            'The first due date will be the first selected day on or after your start date.'
            + (' Short months use the last day. If both dates land on that day, only one task is created.'
               if frequency == 'twice_monthly' else
               ' Short months use the last day, then return to your chosen date.' if monthly else ''))
        self._mark_dirty()

    def draft(self):
        role = self.role.currentData()
        if self.already_finished.isChecked():
            title = self.title.text().strip()
            result = self.completion_details.result_details.toPlainText().strip()
            return {
                "category": self.category.currentData(),
                "outcome": title + ("\n\n" + result if result else ""),
                "duty": None if role is self.NEW else role,
                "new_duty": self.new_role.text().strip() if role is self.NEW else None,
                **self.completion_details.draft(),
            }
        draft = {
            "title": self.title.text(),
            "category": self.category.currentData(),
            "duty": None if role is self.NEW else role,
            "new_duty": self.new_role.text().strip() if role is self.NEW else None,
            "due_date": self.due_date.date().toString("yyyy-MM-dd") if self.has_due_date.isChecked() else None,
        }
        if self.repeat_task.isChecked():
            frequency = self.repeat_frequency.currentData()
            draft.update(repeat={'biweekly': 'weekly', 'twice_monthly': 'monthly'}.get(frequency, frequency),
                         interval=2 if frequency == 'biweekly' else 1 if frequency == 'twice_monthly'
                         else self.repeat_interval.value())
            if frequency in ('weekly', 'biweekly'):
                draft['weekday'] = self.repeat_weekday.currentIndex()
            elif frequency in ('monthly', 'twice_monthly'):
                draft['month_day'] = self.repeat_month_day.value()
                if frequency == 'twice_monthly':
                    draft['second_month_day'] = self.repeat_second_day.value()
        return draft

    def show_preview(self):
        if self._committed or self._saving:
            return
        draft = self.draft()
        finished = self.already_finished.isChecked()
        if finished and not self.title.text().strip():
            self.status.setText("Check the draft: What did you finish? must not be blank")
            return
        try:
            prepared = (self.service.preview_capture(draft) if finished else
                        self.service.preview_task(draft))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"Check the draft: {exc}")
            return
        self._preview_draft = draft
        self._preview_finished = finished
        values = prepared.get('values', draft)
        if finished:
            details = [
                ("Outcome", draft["outcome"]),
                ("Category", draft["category"]),
                ("Role", draft["new_duty"] or draft["duty"] or "None"),
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
        else:
            details = (
                ("Task", draft["title"].strip()),
                ("Category", draft["category"]),
                ("Role", draft["new_duty"] or draft["duty"] or "None"),
                ("First due" if draft.get('repeat') else "Due", values["due_date"] or "No due date"),
            )
        self.preview_text.setPlainText("\n".join(f"{label}: {value}" for label, value in details))
        if not finished and draft.get('repeat'):
            description = schedule_description(draft['repeat'], draft['interval'], values['due_date'],
                                               values.get('month_day'), values.get('second_month_day'))
            self.preview_text.appendPlainText(f'Repeat: {description}')
            self.preview_text.appendPlainText('Completing this task creates the next occurrence.')
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
            result = (self.service.save_capture(self._preview_draft) if self._preview_finished else
                      self.service.save_task(self._preview_draft))
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
            saved = (f"Saved accomplishment #{result.completion_id}" if self._preview_finished else
                     f"Saved task #{result.task_id}")
            self.status.setText(f"{saved}. Export failed: {result.export_error}. Retry export only.")
            self.retry_button.show()
        else:
            self.status.setText(f"Saved accomplishment #{result.completion_id}." if self._preview_finished else
                                f"Saved task #{result.task_id}.")
        self.saved.emit()

    def retry_export(self):
        if not self._committed:
            return
        try:
            self.service.retry_export()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f"{'Accomplishment' if self._preview_finished else 'Task'} is saved. Export retry failed: {exc}")
            return
        self.retry_button.hide()
        self.status.setText(f"{'Accomplishment' if self._preview_finished else 'Task'} is saved. Export completed.")

    def confirm_discard(self):
        if not self.has_unsaved_draft:
            return True
        answer = QMessageBox.question(
            self, "Discard draft?", "Discard this unsaved entry draft?",
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
        self.already_finished.setChecked(False)
        self.completion_details.reset()
        self.category.setCurrentIndex(0)
        self.role.setCurrentIndex(0)
        self.new_role.clear()
        self.repeat_task.setChecked(False)
        self.repeat_frequency.setCurrentIndex(0)
        self.repeat_interval.setValue(1)
        self.repeat_weekday.setCurrentIndex(QDate.currentDate().dayOfWeek() - 1)
        self.repeat_month_day.setValue(QDate.currentDate().day())
        self.repeat_second_day.setValue(15)
        self.has_due_date.setChecked(False)
        self.due_date.setDate(QDate.currentDate())
        self._finished_changed(False)
        self.status.clear()
        self._dirty = False
        self.title.setFocus()


class AddTaskWidget(QWidget):
    """Add future work or a finished accomplishment in one shared form."""

    saved = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._service = service
        self.task = TaskWidget(service)
        self.already_finished = self.task.already_finished
        self.task.saved.connect(self.saved)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.task)

        layout = QVBoxLayout(self)
        heading = QLabel("Add task")
        heading.setObjectName("addTaskTitle")
        layout.addWidget(heading)
        layout.addWidget(scroll, 1)

    @property
    def service(self):
        return self._service

    @service.setter
    def service(self, value):
        self._service = value
        self.task.service = value
        self.task.category.service = value
        self.task.completion_details.service = value

    @property
    def has_unsaved_draft(self):
        return self.task.has_unsaved_draft

    def confirm_discard(self):
        if not self.has_unsaved_draft:
            return True
        answer = QMessageBox.question(
            self, "Discard draft?", "Discard this unsaved entry draft?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard
