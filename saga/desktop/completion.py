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
