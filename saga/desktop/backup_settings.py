"""Optional OneDrive backup controls for the desktop settings dialog."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from saga.desktop.backups import detect_onedrive, validate_backup_destination


def _label(message=""):
    label = QLabel(message)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class BackupSettings(QWidget):
    """Persist a backup choice only after its destination has been checked."""

    def __init__(self, settings_store, database, controller, parent=None):
        super().__init__(parent)
        self.settings_store = settings_store
        self.database = Path(database).resolve() if database else None
        self.controller = controller
        self.onedrive_root = detect_onedrive()
        self.directory = None
        self._enabled = False

        layout = QVBoxLayout(self)
        self.checkbox = QCheckBox("Back up to OneDrive")
        layout.addWidget(self.checkbox)
        layout.addWidget(_label("Your working records stay on this computer. Saga makes verified backups "
                                 "after changes while it is open, and keeps the latest 30 copies. "
                                 "OneDrive handles uploading them; Saga cannot confirm upload."))
        self.location = _label()
        layout.addWidget(self.location)
        self.choose_button = QPushButton("Choose OneDrive folder…")
        self.choose_button.clicked.connect(self.choose_folder)
        layout.addWidget(self.choose_button)
        self.backup_now_button = QPushButton("Back up now")
        self.backup_now_button.clicked.connect(self.controller.backup_now)
        layout.addWidget(self.backup_now_button)
        self.status = _label(self.controller.status)
        self.status.setObjectName("backupSettingsStatus")
        layout.addWidget(self.status)
        self.controller.statusChanged.connect(self.status.setText)

        if self.database is None:
            self.location.setText("Start or open your Saga before setting up backups.")
            self.checkbox.setEnabled(False)
            self.choose_button.setEnabled(False)
            self.backup_now_button.setEnabled(False)
            return
        try:
            saved = self.settings_store.backup_for(self.database)
            self._enabled = bool(saved['enabled'])
            if saved['directory']:
                self.directory = Path(saved['directory'])
        except (ValueError, OSError) as exc:
            self.status.setText(f"Could not load backup settings: {exc}")
        if self.directory is None and self.onedrive_root is not None:
            self.directory = self.onedrive_root / "SagaBackups"
        self.checkbox.setChecked(self._enabled)
        self.backup_now_button.setEnabled(self._enabled)
        self._show_location()
        self.checkbox.toggled.connect(self._toggle)

    def _show_location(self):
        if self.directory is None:
            self.location.setText("Choose an existing OneDrive folder to enable backups.")
        else:
            self.location.setText(f"Backup folder: {self.directory}")

    def _restore_toggle(self):
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(self._enabled)
        self.checkbox.blockSignals(False)

    def _save(self, enabled, directory):
        try:
            destination = validate_backup_destination(self.database, directory) if enabled else directory
            self.settings_store.set_backup(self.database, enabled, destination)
        except (ValueError, OSError) as exc:
            self.status.setText(f"Could not save backup settings: {exc}")
            self._restore_toggle()
            return False
        self._enabled = enabled
        self.backup_now_button.setEnabled(enabled)
        self.directory = Path(destination) if destination else None
        self._show_location()
        self.controller.configure(self.database)
        return True

    def _toggle(self, enabled):
        if enabled and self.directory is None:
            self.status.setText("Choose an existing OneDrive folder before enabling backups.")
            self._restore_toggle()
            return
        self._save(enabled, self.directory)

    def choose_folder(self):
        start = str(self.directory or self.onedrive_root or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose OneDrive backup folder", start)
        if not chosen:
            return
        destination = Path(chosen)
        if self._enabled:
            self._save(True, destination)
        else:
            try:
                destination = validate_backup_destination(self.database, destination)
            except (ValueError, OSError) as exc:
                self.status.setText(f"Could not use backup folder: {exc}")
                return
            self.directory = destination
            self._show_location()
