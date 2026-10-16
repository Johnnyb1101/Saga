"""Dialog for recording the outcome of an existing task."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from saga.desktop.capture import CaptureWidget
from saga.desktop.due_date import DueDateDialog
from saga.desktop.stop_repeating import StopRepeatingDialog


class CompletionDialog(QDialog):
    """Keep the task's original context fixed while its outcome is reviewed."""

    saved = Signal()

    def __init__(self, service, task_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mark task complete")
        self._discard_approved = False
        self.task = dict(service.task_details(task_id))
        self.form = CaptureWidget(service, tracked_task=self.task)
        self.form.cancel_button.hide()
        self.form.saved.connect(self.saved.emit)
        layout = QVBoxLayout(self)
        self.change_due_button = QPushButton('Change due date')
        self.change_due_button.clicked.connect(self.change_due_date)
        self.form.saved.connect(lambda: self.change_due_button.setEnabled(False))
        layout.addWidget(self.change_due_button)
        self.series = service.recurrence_details(task_id) if self.task.get('recurrence_id') is not None else None
        self.stop_repeat_button = QPushButton('Stop repeating permanently')
        self.stop_repeat_button.setVisible(self.series is not None and self.series['status'] == 'active')
        self.stop_repeat_button.clicked.connect(self.stop_repeating)
        self.form.saved.connect(lambda: self.stop_repeat_button.setEnabled(False))
        layout.addWidget(self.stop_repeat_button)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.form)
        layout.addWidget(self.scroll)
        self.back_button = QPushButton('Back to Today')
        self.back_button.clicked.connect(self.reject)
        layout.addWidget(self.back_button)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMaximumSize(max(1, screen.width() - 40), max(1, screen.height() - 40))
        self.resize(min(720, self.maximumWidth()), min(720, self.maximumHeight()))

    def change_due_date(self):
        if not self.form.confirm_discard():
            return
        dialog = DueDateDialog(self.form.service, self.task, self)
        dialog.exec()
        if dialog.result_saved:
            self.saved.emit()
            self.accept()
        dialog.deleteLater()

    def stop_repeating(self):
        if self.series is None or not self.form.confirm_discard():
            return
        dialog = StopRepeatingDialog(self.form.service, self.task, self.series, self)
        dialog.exec()
        if dialog.result_saved:
            self.saved.emit()
            self.accept()
        dialog.deleteLater()

    def reject(self):
        if not self.form.confirm_discard():
            return
        self._discard_approved = True
        try:
            super().reject()
        finally:
            self._discard_approved = False

    def closeEvent(self, event):
        if self._discard_approved or self.form.confirm_discard():
            event.accept()
        else:
            event.ignore()
