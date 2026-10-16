"""Evidence fields for an accomplishment entered on the Add Task form."""

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)


class CompletionDetails(QWidget):
    changed = Signal()
    NEW = object()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.result_details = QPlainTextEdit()
        self.result_details.setPlaceholderText("Optional details about what happened and why it mattered")
        self.result_details.setAccessibleName("Result details (optional)")
        self.result_details.setTabChangesFocus(True)
        self.result_details.setMinimumHeight(90)
        self.outcome = self.result_details
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

        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Result details (optional)", self.result_details)
        form.addRow("Completion date", self.use_today)
        form.addRow("Choose date", self.completed_date)
        help_text = QLabel("For example: 3 applications submitted, 2 hours volunteered, or 5 people helped.")
        help_text.setWordWrap(True)
        form.addRow(help_text)
        form.addRow("Can you put a number to this?", self.measurement_status)
        form.addRow("What are you counting?", self.measure)
        form.addRow("Type it here", self.new_measure)
        form.addRow("Amount", self.quantity)
        form.addRow("When should Saga remind you?", self.remind_on)
        form.addRow(self.flagged)
        self.form_layout = form

        self.result_details.textChanged.connect(self.changed)
        self.use_today.toggled.connect(self._today_changed)
        self.completed_date.dateChanged.connect(self.changed)
        self.measurement_status.currentIndexChanged.connect(self._measurement_changed)
        self.measure.currentIndexChanged.connect(self._measure_changed)
        self.new_measure.textChanged.connect(self.changed)
        self.quantity.textChanged.connect(self.changed)
        self.remind_on.dateChanged.connect(self.changed)
        self.flagged.toggled.connect(self.changed)
        self._measurement_changed()

    def reload_references(self, measures):
        selected = self.measure.currentData()
        self.measure.blockSignals(True)
        self.measure.clear()
        for name in measures:
            self.measure.addItem(name, name)
        self.measure.addItem("Add something else…", self.NEW)
        index = self.measure.findData(selected)
        self.measure.setCurrentIndex(index if index >= 0 else -1 if selected is not None else 0)
        self.measure.blockSignals(False)
        self._measure_changed()
        return selected is not None and self.measure.currentData() is None

    def _today_changed(self, checked):
        self.completed_date.setEnabled(not checked)
        self.changed.emit()

    def _measure_changed(self, *_):
        self.form_layout.setRowVisible(
            self.new_measure,
            self.measurement_status.currentData() == "measured" and self.measure.currentData() is self.NEW,
        )
        self.changed.emit()

    def _measurement_changed(self, *_):
        status = self.measurement_status.currentData()
        measured = status == "measured"
        self.form_layout.setRowVisible(self.measure, measured)
        self.form_layout.setRowVisible(self.quantity, measured)
        self.form_layout.setRowVisible(self.new_measure, measured and self.measure.currentData() is self.NEW)
        self.form_layout.setRowVisible(self.remind_on, status == "unknown")
        self.changed.emit()

    def draft(self):
        status = self.measurement_status.currentData()
        measure = self.measure.currentData()
        return {
            "completed_date": None if self.use_today.isChecked() else self.completed_date.date().toString("yyyy-MM-dd"),
            "measurement_status": status,
            "measure": measure if status == "measured" and measure is not self.NEW else None,
            "new_measure": self.new_measure.text().strip() if status == "measured" and measure is self.NEW else None,
            "quantity": self.quantity.text().strip() if status == "measured" else None,
            "remind_on": self.remind_on.date().toString("yyyy-MM-dd") if status == "unknown" else None,
            "flagged": self.flagged.isChecked(),
        }

    def reset(self):
        self.result_details.clear()
        self.use_today.setChecked(True)
        self.completed_date.setDate(QDate.currentDate())
        self.measurement_status.setCurrentIndex(0)
        self.measure.setCurrentIndex(0)
        self.new_measure.clear()
        self.quantity.clear()
        self.remind_on.setDate(QDate.currentDate())
        self.flagged.setChecked(False)
