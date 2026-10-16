"""Opt-in verified snapshots while the desktop application is open."""

import os
import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from saga import scheduled_backup

ONEDRIVE_VARIABLES = ('OneDriveCommercial', 'OneDriveConsumer', 'OneDrive')


def detect_onedrive():
    """Return an existing OneDrive root advertised by the local environment."""
    for name in ONEDRIVE_VARIABLES:
        value = os.environ.get(name)
        if value and Path(value).is_absolute() and Path(value).is_dir():
            return Path(value).resolve()
    return None


def _contains(directory, path):
    # normcase handles Windows case aliases; relative_to compares components,
    # avoiding false matches such as C:\\data and C:\\database.
    directory = Path(os.path.normcase(str(directory)))
    path = Path(os.path.normcase(str(path)))
    return path.is_relative_to(directory)


def validate_backup_destination(database, directory):
    """Validate without creating anything; allow a child of an existing root."""
    if directory is None or not Path(directory).is_absolute():
        raise ValueError('Choose an absolute backup folder')
    destination = Path(directory).resolve()
    source = Path(database).resolve()
    if _contains(destination, source):
        raise ValueError('The backup folder must not contain your live archive')
    for name in ONEDRIVE_VARIABLES:
        value = os.environ.get(name)
        if value and Path(value).is_absolute():
            root = Path(value).resolve()
            if _contains(root, destination) and not root.is_dir():
                raise ValueError('The OneDrive folder is unavailable. Reconnect it and retry')
    if destination.exists() and not destination.is_dir():
        raise ValueError('The backup destination must be a folder')
    if not destination.is_dir() and not destination.parent.is_dir():
        raise ValueError('The backup folder is unavailable. Reconnect it and retry')
    return destination


class _WorkerSignals(QObject):
    finished = Signal(int, object, str)


class _BackupWorker(QRunnable):
    def __init__(self, generation, source, destination, root):
        super().__init__()
        self.generation = generation
        self.source = source
        self.destination = destination
        self.root = root
        self.signals = _WorkerSignals()
        self.snapshot = None
        self.error = ''

    @Slot()
    def run(self):
        snapshot, error = None, ''
        try:
            if not self.root.is_dir():
                raise ValueError('The backup folder is unavailable. Reconnect it and retry')
            destination = validate_backup_destination(self.source, self.destination)
            snapshot, _ = scheduled_backup.run(self.source, destination, keep=30)
        except (OSError, ValueError, sqlite3.Error) as exc:
            error = str(exc)
        self.snapshot, self.error = snapshot, error
        self.signals.finished.emit(self.generation, snapshot, error)


class BackupController(QObject):
    """Serialize background snapshots and coalesce changes made during a run."""

    statusChanged = Signal(str)

    def __init__(self, settings_store, parent=None):
        super().__init__(parent)
        self.settings_store = settings_store
        self.status = 'Backups are off'
        self._generation = 0
        self._source = None
        self._destination = None
        self._root = None
        self._enabled = False
        self._pending = False
        self._closed = False
        self._worker = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(1000)
        self._debounce.timeout.connect(self.backup_now)
        self._periodic = QTimer(self)
        self._periodic.setInterval(15 * 60 * 1000)
        self._periodic.timeout.connect(self.backup_now)

    def _set_status(self, status):
        self.status = status
        self.statusChanged.emit(status)

    def configure(self, database):
        if self._closed:
            return
        self._generation += 1
        self._debounce.stop()
        self._periodic.stop()
        self._pending = False
        self._enabled = False
        self._source = Path(database).resolve() if database is not None else None
        self._destination = self._root = None
        if self._source is None:
            self._set_status('Backups are off')
            return
        try:
            settings = self.settings_store.backup_for(self._source)
            self._enabled = settings['enabled']
            if not self._enabled:
                self._set_status('Backups are off')
                return
            self._destination = Path(settings['directory'])
            # Retain the expected root even if disconnected so a later retry can
            # recover, without recreating a root removed during this session.
            self._root = (self._destination if self._destination.is_dir()
                          else self._destination.parent)
            self._periodic.start()
            self.backup_now()
        except (OSError, ValueError) as exc:
            self._set_status(f'Backup failed: {exc}')

    def request_backup(self):
        if self._enabled and not self._closed:
            self._debounce.start()

    def backup_now(self):
        if not self._enabled or self._closed or self._destination is None:
            return
        self._debounce.stop()
        if self._worker is not None:
            self._pending = True
            return
        self._pending = False
        self._set_status('Saving a backup on this computer…')
        worker = _BackupWorker(self._generation, self._source, self._destination, self._root)
        worker.signals.finished.connect(self._finished)
        self._worker = worker
        self._pool.start(worker)

    @Slot(int, object, str)
    def _finished(self, generation, snapshot, error):
        self._worker = None
        if self._closed:
            return
        if generation == self._generation:
            self._set_status(f'Backup failed: {error}' if error else
                             f'Backup saved on this computer: {snapshot}. '
                             'OneDrive handles cloud syncing separately.')
        if self._pending:
            self.backup_now()

    def shutdown(self):
        """Finish active and pending snapshots before Qt objects are destroyed."""
        if self._closed:
            return
        flush = self._enabled and (self._pending or self._debounce.isActive())
        self._closed = True
        self._pending = False
        self._debounce.stop()
        self._periodic.stop()
        self._pool.waitForDone()
        if flush:
            # A final save can arrive while another snapshot is running, or
            # immediately before close. Finish that last request on the same
            # worker pool after its predecessor, without depending on queued
            # Qt completion signals while the closing UI waits.
            worker = _BackupWorker(self._generation, self._source, self._destination, self._root)
            self._worker = worker
            self._pool.start(worker)
            self._pool.waitForDone()
        if self._worker is not None and self._worker.generation == self._generation:
            self._set_status(f'Backup failed: {self._worker.error}' if self._worker.error else
                             f'Backup saved on this computer: {self._worker.snapshot}. '
                             'OneDrive handles cloud syncing separately.')
        self._generation += 1
        self._worker = None
