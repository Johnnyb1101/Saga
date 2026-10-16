"""Preview and save a date change for one open task."""

import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class DueDateDialog(QDialog):
    def __init__(self, service, task, parent=None):
        super().__init__(parent)
        self.service = service
        self.task = dict(task)
        self.result_saved = False
        self._preview = None
        self.setWindowTitle('Change due date')
        layout = QVBoxLayout(self)
        title = QLabel(task['title'])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        self.has_date = QCheckBox('Set a due date')
        self.has_date.setChecked(task['due_date'] is not None)
        self.date = QDateEdit(QDate.fromString(task['due_date'], 'yyyy-MM-dd')
                              if task['due_date'] else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        self.date.setAccessibleName('New due date')
        self.date.setEnabled(self.has_date.isChecked())
        layout.addWidget(self.has_date)
        layout.addWidget(self.date)
        if task.get('recurrence_id') is not None:
            explanation = QLabel('This changes only this occurrence. Future dates keep the original schedule.')
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.preview_button = QPushButton('Preview')
        self.preview_button.clicked.connect(self.show_preview)
        layout.addWidget(self.preview_button)
        self.save_button = QPushButton('Save due date')
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.retry_button = QPushButton('Retry export')
        self.retry_button.hide()
        self.retry_button.clicked.connect(self.retry_export)
        layout.addWidget(self.retry_button)
        self.back_button = QPushButton('Cancel')
        self.back_button.clicked.connect(self.reject)
        layout.addWidget(self.back_button)
        self.has_date.toggled.connect(self.date.setEnabled)
        self.has_date.toggled.connect(self.invalidate_preview)
        self.date.dateChanged.connect(self.invalidate_preview)

    def invalidate_preview(self, *_):
        self._preview = None
        self.preview.clear()
        self.save_button.setEnabled(False)

    def show_preview(self):
        value = self.date.date().toString('yyyy-MM-dd') if self.has_date.isChecked() else None
        try:
            self.service.preview_due_date(self.task, value)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        self._preview = (value,)
        self.preview.setText(f"Before: {self.task['due_date'] or 'No due date'}\nAfter: {value or 'No due date'}")
        self.status.clear()
        self.save_button.setEnabled(True)

    def save(self):
        if self._preview is None or self.result_saved:
            return
        try:
            result = self.service.save_due_date(self.task, self._preview[0])
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            self.invalidate_preview()
            return
        self.result_saved = True
        for widget in (self.date, self.has_date, self.preview_button, self.save_button):
            widget.setEnabled(False)
        self.back_button.setText('Back to Today')
        self.status.setText('Due date saved.' if result.export_error is None
                            else f'Due date saved. Export failed: {result.export_error}')
        self.retry_button.setVisible(result.export_error is not None)

    def retry_export(self):
        try:
            self.service.retry_export()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f'Due date saved. Export failed: {exc}')
            return
        self.status.setText('Due date saved. Export updated.')
        self.retry_button.hide()
