"""Explicit confirmation before cancelling a recurring task and its schedule."""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout


class StopRepeatingDialog(QDialog):
    def __init__(self, service, task, series, parent=None):
        super().__init__(parent)
        self.service = service
        self.task = task
        self.series = series
        self.result_saved = False
        self.setWindowTitle('Stop repeating permanently')
        layout = QVBoxLayout(self)
        title = QLabel(task['title'])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        explanation = QLabel('This cancels the current task and stops all future occurrences. '
                             'It will not count as completed. Previous accomplishments stay saved.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.stop_button = QPushButton('Stop repeating permanently')
        self.stop_button.clicked.connect(self.save)
        layout.addWidget(self.stop_button)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.retry_button = QPushButton('Retry export')
        self.retry_button.hide()
        self.retry_button.clicked.connect(self.retry_export)
        layout.addWidget(self.retry_button)
        self.back_button = QPushButton('Keep task')
        self.back_button.clicked.connect(self.reject)
        self.back_button.setDefault(True)
        layout.addWidget(self.back_button)

    def save(self):
        if self.result_saved:
            return
        try:
            result = self.service.stop_repeating(self.task, self.series)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        self.result_saved = True
        self.stop_button.setEnabled(False)
        self.back_button.setText('Back to Today')
        self.status.setText('Stopped repeating.' if result.export_error is None
                            else f'Stopped repeating. Export failed: {result.export_error}')
        self.retry_button.setVisible(result.export_error is not None)

    def retry_export(self):
        try:
            self.service.retry_export()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(f'Stopped repeating. Export failed: {exc}')
            return
        self.status.setText('Stopped repeating. Export updated.')
        self.retry_button.hide()
