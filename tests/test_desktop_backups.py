"""Desktop backups are opt-in, asynchronous, and separate from the live archive."""

import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None

if HAS_QT:
    from PySide6.QtWidgets import QApplication

    from saga import db, scheduled_backup, writes
    from saga.desktop.backups import (
        BackupController,
        detect_onedrive,
        validate_backup_destination,
    )
    from saga.desktop.settings import SettingsStore


@unittest.skipUnless(HAS_QT, 'Install requirements-desktop.txt for desktop tests')
class DesktopBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.source = self.root / 'live' / 'saga.db'
        self.output = self.root / 'copies'
        self.store = SettingsStore(self.root / 'settings.json')
        self.controller = BackupController(self.store)
        self.addCleanup(self.controller.shutdown)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), self.controller.status)

    def enable(self):
        db.init_db(self.source)
        self.store.set_backup(self.source, True, self.output)
        self.controller.configure(self.source)

    def test_default_off_and_legacy_settings_keep_exact_shape(self):
        self.assertEqual(self.store.load(), {'database': None, 'exports': {}})
        self.assertEqual(self.store.backup_for(self.source), {'enabled': False, 'directory': None})
        self.controller.configure(self.source)
        self.controller.request_backup()
        self.controller.backup_now()
        self.assertEqual(self.controller.status, 'Backups are off')
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.source.exists())
        self.assertFalse(self.output.exists())

    def test_settings_preserve_theme_exports_and_per_archive_choices(self):
        other = self.root / 'other.db'
        self.store.select(self.source, self.root / 'exports')
        self.store.set_theme('dark', 'blue')
        self.store.set_backup(self.source, True, self.output)
        self.store.set_backup(other, False)
        self.store.select(other, None)
        reopened = SettingsStore(self.store.path)
        self.assertEqual(reopened.theme(), {'mode': 'dark', 'accent': 'blue'})
        self.assertEqual(reopened.export_for(self.source), (self.root / 'exports').resolve())
        self.assertEqual(reopened.backup_for(self.source),
                         {'enabled': True, 'directory': str(self.output.resolve())})
        self.assertEqual(reopened.backup_for(other), {'enabled': False, 'directory': None})
        self.assertFalse(self.output.exists())

    def test_malformed_backup_settings_are_not_overwritten(self):
        invalid = [[], {'relative.db': {'enabled': False, 'directory': None}}]
        invalid.extend({str(self.source): value} for value in (
            {}, {'enabled': 1, 'directory': str(self.output)},
            {'enabled': True, 'directory': None}, {'enabled': False, 'directory': 'relative'},
            {'enabled': False, 'directory': None, 'extra': 1}))
        for backups in invalid:
            with self.subTest(backups=backups):
                content = json.dumps({'database': None, 'exports': {}, 'backups': backups})
                self.store.path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.store.load()
                with self.assertRaises(ValueError):
                    self.store.set_backup(self.source, False)
                self.assertEqual(self.store.path.read_text(encoding='utf-8'), content)

    def test_invalid_setter_and_failed_atomic_write_preserve_settings(self):
        self.store.set_backup(self.source, True, self.output)
        previous = self.store.path.read_bytes()
        for enabled, directory in ((1, self.output), (True, None), (True, 'relative')):
            with self.subTest(enabled=enabled, directory=directory), self.assertRaises(ValueError):
                self.store.set_backup(self.source, enabled, directory)
        with (patch('saga.desktop.settings.os.replace', side_effect=PermissionError('blocked')),
              self.assertRaises(PermissionError)):
            self.store.set_backup(self.source, False)
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob('.settings-*.tmp')), [])

    def test_destination_validation_protects_live_archive_and_path_components(self):
        self.source.parent.mkdir()
        for directory in (self.source, self.source.parent, self.root):
            with self.subTest(directory=directory), self.assertRaisesRegex(ValueError, 'live archive'):
                validate_backup_destination(self.source, directory)
        child = self.source.parent / 'backups'
        self.assertEqual(validate_backup_destination(self.source, child), child.resolve())
        self.assertFalse(child.exists())
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate_backup_destination(self.source, self.root / 'missing' / 'copies')
        if os.name == 'nt':
            with self.assertRaisesRegex(ValueError, 'live archive'):
                validate_backup_destination(self.source, str(self.source.parent).upper())

    def test_detect_onedrive_requires_existing_environment_root(self):
        cloud = self.root / 'OneDrive'
        with patch.dict(os.environ, {'OneDriveCommercial': str(cloud),
                                     'OneDriveConsumer': '', 'OneDrive': ''}):
            self.assertIsNone(detect_onedrive())
            with self.assertRaisesRegex(ValueError, 'OneDrive folder is unavailable'):
                validate_backup_destination(self.source, cloud)
            cloud.mkdir()
            self.assertEqual(detect_onedrive(), cloud.resolve())
            self.assertEqual(validate_backup_destination(self.source, cloud / 'Saga'),
                             (cloud / 'Saga').resolve())

    def test_snapshot_can_restore_and_source_is_unchanged(self):
        db.init_db(self.source)
        with closing(db.connect(self.source)) as con:
            writes.add_task(con, 'Invented backup task', 'work')
            expected = list(con.iterdump())
        before = self.source.read_bytes()
        self.store.set_backup(self.source, True, self.output)
        self.controller.configure(self.source)
        self.wait_until(lambda: self.controller.status.startswith('Backup saved'))
        snapshots = list(self.output.glob('saga-scheduled-*/snapshot-*.db'))
        self.assertEqual(len(snapshots), 1)
        scheduled_backup.verify_restore(snapshots[0])
        with closing(db.connect(snapshots[0])) as con:
            self.assertEqual(list(con.iterdump()), expected)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIn('on this computer', self.controller.status)
        self.assertIn('cloud syncing separately', self.controller.status)
        self.assertTrue(self.controller._periodic.isActive())

    def test_failure_is_visible_and_manual_retry_recovers(self):
        with patch('saga.desktop.backups.scheduled_backup.run', side_effect=PermissionError('unavailable')):
            self.enable()
            self.wait_until(lambda: self.controller.status.startswith('Backup failed'))
        self.assertIn('unavailable', self.controller.status)
        self.controller.backup_now()
        self.wait_until(lambda: self.controller.status.startswith('Backup saved'))

    def test_disconnected_existing_root_is_not_recreated(self):
        self.output.mkdir()
        with patch('saga.desktop.backups.scheduled_backup.run', return_value=(self.output / 'copy.db', [])):
            self.enable()
            self.wait_until(lambda: self.controller.status.startswith('Backup saved'))
        self.output.rmdir()
        self.controller.backup_now()
        self.wait_until(lambda: self.controller.status.startswith('Backup failed'))
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        self.controller.request_backup()
        self.wait_until(lambda: self.controller.status.startswith('Backup saved'))

    def test_disabling_stops_timers_and_future_snapshots(self):
        self.enable()
        self.wait_until(lambda: self.controller.status.startswith('Backup saved'))
        before = list(self.output.rglob('snapshot-*.db'))
        self.controller.request_backup()
        self.store.set_backup(self.source, False, self.output)
        self.controller.configure(self.source)
        self.controller.backup_now()
        self.assertFalse(self.controller._periodic.isActive())
        self.assertFalse(self.controller._debounce.isActive())
        self.assertEqual(self.controller.status, 'Backups are off')
        self.assertEqual(list(self.output.rglob('snapshot-*.db')), before)

    def test_busy_worker_coalesces_and_old_archive_completion_is_ignored(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        main_thread = threading.get_ident()

        def backup(source, output, keep):
            calls.append((source, threading.get_ident(), keep))
            started.set()
            if not release.wait(5):
                raise ValueError('Test release timed out')
            return output / 'copy.db', []

        other = self.root / 'other.db'
        with patch('saga.desktop.backups.scheduled_backup.run', side_effect=backup):
            self.enable()
            self.wait_until(started.is_set)
            self.assertNotEqual(calls[0][1], main_thread)
            self.controller.backup_now()
            self.controller.backup_now()
            self.store.set_backup(other, True, self.output)
            self.controller.configure(other)
            statuses = []
            self.controller.statusChanged.connect(statuses.append)
            release.set()
            self.wait_until(lambda: len(calls) == 2 and self.controller._worker is None)
            self.assertEqual([call[0] for call in calls], [self.source.resolve(), other.resolve()])
            self.assertEqual([call[2] for call in calls], [30, 30])
            self.assertEqual(len([status for status in statuses if status.startswith('Backup saved')]), 1)

    def test_shutdown_finishes_worker_and_prevents_new_work(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def backup(source, output, keep):
            started.set()
            if not release.wait(5):
                raise ValueError('Test release timed out')
            return output / 'copy.db', []

        with patch('saga.desktop.backups.scheduled_backup.run', side_effect=backup) as run:
            self.enable()
            self.wait_until(started.is_set)
            release.set()
            self.controller.shutdown()
            self.app.processEvents()
            self.controller.request_backup()
            self.controller.backup_now()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(self.controller._pool.activeThreadCount(), 0)
            self.assertFalse(self.controller._periodic.isActive())

    def test_shutdown_flushes_debounced_save_into_readable_snapshot(self):
        self.enable()
        self.wait_until(lambda: self.controller.status.startswith('Backup saved'))
        with closing(db.connect(self.source)) as con:
            writes.add_task(con, 'Saved immediately before close', 'work')
            expected = list(con.iterdump())
        self.controller.request_backup()
        self.controller.shutdown()
        manifests = list(self.output.glob('saga-scheduled-*/manifest.json'))
        names = json.loads(manifests[0].read_text(encoding='utf-8'))['snapshots']
        self.assertEqual(len(names), 2)
        with closing(db.connect(manifests[0].parent / names[-1])) as con:
            self.assertEqual(list(con.iterdump()), expected)

    def test_shutdown_serializes_last_change_after_active_snapshot(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def backup(source, output, keep):
            calls.append(source)
            started.set()
            if not release.wait(5):
                raise ValueError('Test release timed out')
            return output / 'copy.db', []

        with patch('saga.desktop.backups.scheduled_backup.run', side_effect=backup):
            self.enable()
            self.wait_until(started.is_set)
            self.controller.request_backup()
            release.set()
            self.controller.shutdown()
            self.app.processEvents()
            self.assertEqual(calls, [self.source.resolve(), self.source.resolve()])
            self.assertEqual(self.controller._pool.activeThreadCount(), 0)


if __name__ == '__main__':
    unittest.main()
