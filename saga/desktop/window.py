"""First-launch archive selection and the read-only Today desktop view."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from saga.application import ArchiveService, create_archive
from saga.desktop.capture import CaptureWidget
from saga.desktop.settings import SettingsStore, default_database


def _plain_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class TodaySection(QGroupBox):
    """A read-only table with an explicit empty state."""

    def __init__(self, title, fields, parent=None):
        super().__init__(title, parent)
        self.fields = fields
        layout = QVBoxLayout(self)
        self.empty = _plain_label("Nothing here.")
        self.table = QTableWidget(0, len(fields))
        self.table.setHorizontalHeaderLabels([label for label, _ in fields])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName(title)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.empty)
        layout.addWidget(self.table)
        self.table.hide()

    def show_rows(self, rows):
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, (_, key) in enumerate(self.fields):
                value = row.get(key)
                item = QTableWidgetItem("" if value is None else str(value))
                self.table.setItem(row_index, col_index, item)
        self.empty.setVisible(not rows)
        self.table.setVisible(bool(rows))


class MainWindow(QMainWindow):
    def __init__(self, settings_store=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saga")
        self.resize(940, 720)
        self.settings_store = settings_store or SettingsStore()
        self.service = None
        self.capture = None
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self._build_setup()
        self._build_archive_page()
        self.pages.setCurrentWidget(self.setup_page)
        try:
            selected = self.settings_store.load()["database"]
            if selected:
                self._activate_archive(Path(selected), persist=False)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._show_setup_error(f"Could not open the saved archive: {exc}")

    def _build_setup(self):
        self.setup_page = QWidget()
        layout = QVBoxLayout(self.setup_page)
        title = _plain_label("Choose your Saga archive")
        title.setObjectName("setupTitle")
        layout.addWidget(title)
        layout.addWidget(_plain_label(
            "Create a local archive or open an existing Saga database. "
            "Opening an archive does not move or copy it."
        ))
        self.new_path = QLineEdit(str(default_database()))
        self.new_path.setAccessibleName("New archive location")
        path_row = QHBoxLayout()
        path_row.addWidget(self.new_path)
        browse = QPushButton("Choose location…")
        browse.clicked.connect(self._browse_new)
        path_row.addWidget(browse)
        layout.addLayout(path_row)
        create = QPushButton("Create new archive")
        create.clicked.connect(self._create_selected)
        open_existing = QPushButton("Open existing archive…")
        open_existing.clicked.connect(self._browse_existing)
        layout.addWidget(create)
        layout.addWidget(open_existing)
        self.setup_error = _plain_label()
        self.setup_error.setObjectName("setupError")
        layout.addWidget(self.setup_error)
        layout.addStretch()
        self.pages.addWidget(self.setup_page)

    def _build_archive_page(self):
        self.archive_page = QWidget()
        layout = QVBoxLayout(self.archive_page)
        top = QHBoxLayout()
        self.archive_label = _plain_label()
        self.archive_label.setAccessibleName("Selected archive")
        top.addWidget(self.archive_label, 1)
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self.show_settings)
        top.addWidget(settings_button)
        layout.addLayout(top)
        self.tabs = QTabWidget()
        self.today_page = QWidget()
        today_layout = QVBoxLayout(self.today_page)
        toolbar = QHBoxLayout()
        self.date_label = _plain_label()
        toolbar.addWidget(self.date_label, 1)
        refresh_button = QPushButton("Refresh Today")
        refresh_button.clicked.connect(self.refresh_today)
        toolbar.addWidget(refresh_button)
        today_layout.addLayout(toolbar)
        self.today_error = _plain_label()
        self.today_error.setObjectName("todayError")
        today_layout.addWidget(self.today_error)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        self.sections = {
            "overdue": TodaySection("Overdue", (("Due", "due_date"), ("Task", "title"), ("Category", "category"), ("Role", "duty"))),
            "due_today": TodaySection("Due today", (("Task", "title"), ("Category", "category"), ("Role", "duty"))),
            "due_soon": TodaySection("Upcoming", (("Due", "due_date"), ("Task", "title"), ("Category", "category"), ("Role", "duty"))),
            "followups": TodaySection("Due measurement follow-ups", (("Remind on", "remind_on"), ("Outcome", "outcome"), ("Category", "category"), ("Role", "duty"))),
        }
        for section in self.sections.values():
            body_layout.addWidget(section)
        body_layout.addStretch()
        scroll.setWidget(body)
        today_layout.addWidget(scroll)
        self.tabs.addTab(self.today_page, "Today")
        layout.addWidget(self.tabs)
        self.pages.addWidget(self.archive_page)

    def _show_setup_error(self, message):
        self.setup_error.setText(message)
        self.pages.setCurrentWidget(self.setup_page)

    def _browse_new(self):
        path, _ = QFileDialog.getSaveFileName(self, "New Saga archive", self.new_path.text(), "SQLite database (*.db)")
        if path:
            self.new_path.setText(path)

    def _create_selected(self):
        path = self.new_path.text().strip()
        if not path:
            self._show_setup_error("Choose a location for the new archive.")
            return
        self.create_new_archive(Path(path))

    def create_new_archive(self, path):
        """Create at an explicit path and enable exports in a sibling folder."""
        try:
            database = create_archive(path)
            self._activate_archive(database, export_dir=database.parent / f"{database.stem}-exports")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._show_setup_error(f"Could not create the archive: {exc}")

    def _browse_existing(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Saga archive", "", "SQLite database (*.db);;All files (*)")
        if path:
            self.open_archive(Path(path))

    def open_archive(self, path):
        """Validate before remembering the selection; exports remain opt-in."""
        if self.capture is not None and not self.capture.confirm_discard():
            return
        try:
            self._activate_archive(path, export_dir=self.settings_store.export_for(path))
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = f"Could not open the archive: {exc}"
            if self.service is None:
                self._show_setup_error(message)
            else:
                QMessageBox.warning(self, "Open archive", message)

    def _activate_archive(self, path, *, export_dir=None, persist=True):
        database = Path(path).resolve()
        if not persist:
            export_dir = self.settings_store.export_for(database)
        service = ArchiveService(database, export_dir)
        service.validate_archive()
        references = service.references()
        if persist:
            self.settings_store.select(database, export_dir)
        self.service = service
        if self.capture is not None:
            index = self.tabs.indexOf(self.capture)
            self.tabs.removeTab(index)
            self.capture.deleteLater()
        self.capture = CaptureWidget(service)
        # Reference lookup above exposes startup errors before changing the page.
        if not references["categories"]:
            self.capture.status.setText("This archive has no categories available for capture.")
        self.capture.saved.connect(self.refresh_today)
        self.tabs.addTab(self.capture, "Capture")
        self.tabs.setCurrentWidget(self.today_page)
        self.archive_label.setText(f"Archive: {database}")
        self.setup_error.clear()
        self.pages.setCurrentWidget(self.archive_page)
        self.refresh_today()

    def refresh_today(self):
        if self.service is None:
            return
        try:
            snapshot = self.service.today()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.today_error.setText(f"Could not refresh Today: {exc}")
            return
        self.today_error.clear()
        self.date_label.setText(f"Today · {snapshot['date']}")
        for name, section in self.sections.items():
            section.show_rows(snapshot[name])

    def _set_export(self, directory):
        if self.service is None:
            return False
        try:
            self.settings_store.select(self.service.database, directory)
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Export settings", f"Could not save export settings: {exc}")
            return False
        self.service = ArchiveService(self.service.database, directory)
        self.capture.service = self.service
        return True

    def show_settings(self):
        if self.service is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Saga settings")
        layout = QVBoxLayout(dialog)
        layout.addWidget(_plain_label(f"Archive: {self.service.database}"))
        export_label = _plain_label()
        layout.addWidget(export_label)

        def update_export_label():
            path = self.service.export_dir
            export_label.setText(f"Exports: {path}" if path else "Exports: disabled")

        update_export_label()

        def choose_export():
            current = str(self.service.export_dir or self.service.database.parent)
            chosen = QFileDialog.getExistingDirectory(dialog, "Choose export folder", current)
            if chosen and self._set_export(Path(chosen)):
                update_export_label()

        def disable_export():
            if self._set_export(None):
                update_export_label()

        def change_archive():
            dialog.accept()
            self._browse_existing()

        choose_button = QPushButton("Choose export folder…")
        choose_button.clicked.connect(choose_export)
        disable_button = QPushButton("Disable exports")
        disable_button.clicked.connect(disable_export)
        change_button = QPushButton("Change archive…")
        change_button.clicked.connect(change_archive)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        for button in (choose_button, disable_button, change_button, close_button):
            layout.addWidget(button)
        dialog.exec()

    def closeEvent(self, event):
        if self.capture is not None and not self.capture.confirm_discard():
            event.ignore()
            return
        event.accept()
