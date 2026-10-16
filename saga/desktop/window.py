"""Local startup, Today tasks, and desktop task completion."""

import datetime as dt
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from saga.application import ArchiveService, create_archive
from saga.desktop.appearance import DESKTOP_STYLE, app_icon, heading
from saga.desktop.backup_settings import BackupSettings
from saga.desktop.backups import BackupController
from saga.desktop.completion import CompletionDialog
from saga.desktop.settings import SettingsStore, default_database
from saga.desktop.task_entry import AddTaskWidget
from saga.desktop.themes import ThemePicker, apply_theme


def _plain_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class TaskCard(QPushButton):
    """A keyboard-accessible task button sized to its wrapped child labels."""

    def sizeHint(self):
        return self.layout().sizeHint() if self.layout() else super().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize() if self.layout() else super().minimumSizeHint()


class TodaySection(QGroupBox):
    """Compact read-only cards that grow with their text."""

    taskActivated = Signal(int)

    def __init__(self, title, fields, parent=None, *, completable=True):
        super().__init__(title, parent)
        self.fields = fields
        self.completable = completable
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.card_layout = QVBoxLayout(self)
        self.card_layout.setSpacing(8)
        self.empty = _plain_label("Nothing here.")
        self.card_layout.addWidget(self.empty)
        self.cards = []
        self.setAccessibleName(title)

    def show_rows(self, rows):
        for card in self.cards:
            self.card_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []
        for row in rows:
            clickable = self.completable and row.get('id') is not None
            card = TaskCard() if clickable else QWidget()
            if clickable:
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                card.setToolTip('Open task to mark it complete')
                card.clicked.connect(lambda checked=False, task_id=row['id']: self.taskActivated.emit(task_id))
            card.setObjectName('taskCard')
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(14, 10, 14, 10)
            layout.setSpacing(4)
            title = str(row.get('title') or row.get('outcome') or 'Untitled task')
            card.title_label = _plain_label(title)
            card.title_label.setObjectName('cardTitle')
            heading(card.title_label, 1)
            card.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if clickable:
                card.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            details = []
            for label, key in self.fields:
                value = row.get(key)
                if key in ('title', 'outcome') or value is None or value == '':
                    continue
                if key in ('due_date', 'remind_on'):
                    try:
                        date = dt.date.fromisoformat(value)
                        value = f'{date.strftime("%b")} {date.day}, {date.year}'
                    except (TypeError, ValueError):
                        pass  # Preserve unexpected historical text rather than hide it.
                    details.append(f'{label} {value}')
                else:
                    details.append(f'{label}: {value}')
            card.details_label = _plain_label(' · '.join(details))
            card.details_label.setParent(card)
            card.details_label.setObjectName('cardDetails')
            card.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if clickable:
                card.details_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            card.setAccessibleName(title)
            card.setAccessibleDescription(card.details_label.text())
            layout.addWidget(card.title_label)
            if details:
                layout.addWidget(card.details_label)
            self.card_layout.addWidget(card)
            self.cards.append(card)
        self.empty.setVisible(not rows)


class MainWindow(QMainWindow):
    def __init__(self, settings_store=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saga")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(DESKTOP_STYLE)
        self.resize(940, 720)
        self.settings_store = settings_store or SettingsStore()
        self.theme = {'mode': 'system', 'accent': 'teal'}
        apply_theme(self, **self.theme)
        QApplication.instance().styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        self.service = None
        self.capture = None
        self.entry = None
        self.backup_controller = BackupController(self.settings_store, self)
        self.backup_controller.statusChanged.connect(self._backup_status_changed)
        self._saved_database_path = None
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self._build_setup()
        self._build_archive_page()
        self.pages.setCurrentWidget(self.setup_page)
        try:
            self.theme = self.settings_store.theme()
            apply_theme(self, **self.theme)
            selected = self.settings_store.load()["database"]
            if selected:
                self._saved_database_path = Path(selected)
                self._activate_archive(Path(selected), persist=False)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._show_setup_error(f"Could not open the saved archive: {exc}")

    def _build_setup(self):
        self.setup_page = QWidget()
        layout = QVBoxLayout(self.setup_page)
        title = _plain_label("Welcome to Saga")
        title.setObjectName("setupTitle")
        heading(title, 5)
        layout.addWidget(title)
        layout.addWidget(_plain_label(
            "Your records stay on this computer. Get started with your own local Saga."
        ))
        self.get_started_button = QPushButton("Get started")
        self.get_started_button.setProperty("primary", True)
        self.get_started_button.setMinimumHeight(44)
        self.get_started_button.clicked.connect(self.get_started)
        open_existing = QPushButton("Open existing Saga archive…")
        open_existing.setFlat(True)
        open_existing.clicked.connect(self._browse_existing)
        layout.addWidget(self.get_started_button)
        layout.addWidget(open_existing)
        settings_button = QPushButton('Settings')
        settings_button.clicked.connect(self.show_settings)
        layout.addWidget(settings_button)
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
        self.archive_label.setAccessibleName("Your Saga")
        top.addWidget(self.archive_label, 1)
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self.show_settings)
        top.addWidget(settings_button)
        layout.addLayout(top)
        self.backup_status = _plain_label()
        self.backup_status.setObjectName("backupStatus")
        self.backup_status.hide()
        layout.addWidget(self.backup_status)
        self.tabs = QTabWidget()
        self.today_page = QWidget()
        today_layout = QVBoxLayout(self.today_page)
        toolbar = QHBoxLayout()
        self.date_label = _plain_label()
        heading(self.date_label, 5)
        toolbar.addWidget(self.date_label, 1)
        refresh_button = QPushButton("Refresh Today")
        refresh_button.clicked.connect(self.refresh_today)
        toolbar.addWidget(refresh_button)
        today_layout.addLayout(toolbar)
        today_layout.addWidget(_plain_label('Click a task to view its details and mark it complete.'))
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
            "later": TodaySection("Later", (("Due", "due_date"), ("Task", "title"), ("Category", "category"), ("Role", "duty"))),
            "undated": TodaySection("No due date", (("Task", "title"), ("Category", "category"), ("Role", "duty"))),
            "followups": TodaySection("Due measurement follow-ups", (("Remind on", "remind_on"), ("Outcome", "outcome"), ("Category", "category"), ("Role", "duty")), completable=False),
        }
        for section in self.sections.values():
            section.taskActivated.connect(self._open_task)
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
        if self.entry is not None and not self.entry.confirm_discard():
            return
        path, _ = QFileDialog.getSaveFileName(self, "New Saga archive", str(default_database()), "SQLite database (*.db)")
        if path:
            self.create_new_archive(Path(path))

    def get_started(self):
        """Open the remembered archive, or create the local default once."""
        try:
            selected = self.settings_store.load()["database"]
            database = Path(selected) if selected else default_database()
            if database.exists():
                if selected:
                    self._activate_archive(database, persist=False)
                else:
                    self._activate_archive(
                        database, export_dir=database.parent / f"{database.stem}-exports")
            else:
                if selected:
                    raise ValueError("The saved archive is missing. Choose an existing archive in Settings.")
                self.create_new_archive(database)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._show_setup_error(f"Could not open your Saga: {exc}")

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
        if self.entry is not None and not self.entry.confirm_discard():
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
        self._saved_database_path = database
        self.backup_controller.configure(database)
        if self.entry is not None:
            index = self.tabs.indexOf(self.entry)
            self.tabs.removeTab(index)
            self.entry.deleteLater()
        self.entry = AddTaskWidget(service)
        self.capture = self.entry.completed
        # Reference lookup above exposes startup errors before changing the page.
        if not references["categories"]:
            for form in (self.entry.task, self.entry.completed):
                form.status.setText("This archive has no categories available for new entries.")
        self.entry.saved.connect(self.refresh_today)
        self.entry.saved.connect(self.backup_controller.request_backup)
        self.entry.task.category.categoriesChanged.connect(self.backup_controller.request_backup)
        self.entry.completed.category.categoriesChanged.connect(self.backup_controller.request_backup)
        self.tabs.addTab(self.entry, "Add Task")
        self.tabs.setCurrentWidget(self.today_page)
        self.archive_label.setText("Your Saga")
        self.setup_error.clear()
        self.pages.setCurrentWidget(self.archive_page)
        self.refresh_today()

    def _open_task(self, task_id):
        if self.service is None:
            return
        try:
            dialog = CompletionDialog(self.service, task_id, self)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.refresh_today()
            self.today_error.setText(f'Could not open this task: {exc}')
            return
        dialog.saved.connect(self._completion_saved)
        dialog.exec()
        dialog.deleteLater()

    def _completion_saved(self):
        self.refresh_today()
        if self.entry is not None:
            self.entry.task.refresh_categories()
            self.entry.completed.refresh_categories()
        self.backup_controller.request_backup()

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
        self.entry.service = self.service
        return True

    def _system_theme_changed(self, *_):
        if self.theme['mode'] == 'system':
            apply_theme(self, **self.theme)

    def _backup_status_changed(self, message):
        self.backup_status.setText(message if message != 'Backups are off' else '')
        self.backup_status.setVisible(message != 'Backups are off' and bool(message))

    def _save_theme(self, choice):
        try:
            self.settings_store.set_theme(**choice)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Theme settings', f'Could not save your theme: {exc}')
            return False
        self.theme = dict(choice)
        apply_theme(self, **self.theme)
        return True

    def _settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Saga settings")
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(ThemePicker(self.theme, self._save_theme, content))
        content_layout.addWidget(BackupSettings(self.settings_store,
                                               self.service.database if self.service else None,
                                               self.backup_controller, content))
        dialog.advanced_toggle = QToolButton(content)
        dialog.advanced_toggle.setText("Advanced")
        dialog.advanced_toggle.setCheckable(True)
        dialog.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        dialog.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced = QWidget(content)
        advanced.setVisible(False)
        dialog.advanced_toggle.toggled.connect(advanced.setVisible)
        dialog.advanced_toggle.toggled.connect(
            lambda checked: dialog.advanced_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        advanced_layout = QVBoxLayout(advanced)
        if self.service is not None:
            advanced_layout.addWidget(_plain_label(f"Archive: {self.service.database}"))
        export_label = _plain_label()
        advanced_layout.addWidget(export_label)

        def update_export_label():
            path = self.service.export_dir if self.service else None
            export_label.setText(f"Exports: {path}" if path else "Exports: disabled")

        update_export_label()

        def choose_export():
            if self.service is None:
                return
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

        def create_archive_elsewhere():
            dialog.accept()
            self._browse_new()

        choose_button = QPushButton("Choose export folder…")
        choose_button.clicked.connect(choose_export)
        choose_button.setEnabled(self.service is not None)
        disable_button = QPushButton("Disable exports")
        disable_button.clicked.connect(disable_export)
        disable_button.setEnabled(self.service is not None)
        change_button = QPushButton("Change archive…")
        change_button.clicked.connect(change_archive)
        create_button = QPushButton("Create archive in another location…")
        create_button.clicked.connect(create_archive_elsewhere)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        for button in (choose_button, disable_button, change_button, create_button):
            advanced_layout.addWidget(button)
        content_layout.addWidget(dialog.advanced_toggle)
        content_layout.addWidget(advanced)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        layout.addWidget(close_button)

        available = dialog.screen().availableGeometry()
        width = min(560, max(300, int(available.width() * 0.9)))
        height_limit = max(240, int(available.height() * 0.85))
        dialog.resize(width, min(460, height_limit))

        def fit_height():
            content_layout.invalidate()
            content_layout.activate()
            inner_width = scroll.viewport().width()
            content_height = content_layout.heightForWidth(inner_width)
            if content_height < 0:
                content_height = content_layout.sizeHint().height()
            chrome_height = layout.contentsMargins().top() + layout.contentsMargins().bottom()
            chrome_height += layout.spacing() + close_button.sizeHint().height() + 8
            dialog.resize(dialog.width(), min(height_limit, content_height + chrome_height))

        dialog.advanced_toggle.toggled.connect(lambda _: QTimer.singleShot(0, fit_height))
        QTimer.singleShot(0, fit_height)
        return dialog

    def show_settings(self):
        dialog = self._settings_dialog()
        dialog.exec()
        dialog.deleteLater()

    def closeEvent(self, event):
        if self.entry is not None and not self.entry.confirm_discard():
            event.ignore()
            return
        self.backup_controller.shutdown()
        event.accept()
