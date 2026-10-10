"""Optional desktop settings persist explicit local archive/export choices."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from saga.desktop.settings import SettingsStore, default_database


@unittest.skipUnless(HAS_QT, 'Install requirements-desktop.txt for desktop tests')
class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.store = SettingsStore(self.root / 'settings' / 'settings.json')

    def test_new_settings_do_not_create_files_and_default_is_local(self):
        self.assertEqual(self.store.load(), {'database': None, 'exports': {}})
        self.assertFalse(self.store.path.exists())
        with patch('saga.desktop.settings.data_directory', return_value=self.root):
            self.assertEqual(default_database(), self.root / 'data' / 'saga.db')

    def test_selections_remember_separate_export_choices_and_disable(self):
        first, second = self.root / 'first.db', self.root / 'second.db'
        exports = self.root / 'exports'
        self.store.select(first, exports)
        self.store.select(second, None)
        reopened = SettingsStore(self.store.path)
        self.assertEqual(reopened.load()['database'], str(second))
        self.assertEqual(reopened.export_for(first), exports)
        self.assertIsNone(reopened.export_for(second))
        reopened.select(first, None)
        self.assertIsNone(reopened.export_for(first))
        self.assertFalse(first.exists())
        self.assertFalse(exports.exists())

    def test_failed_replace_preserves_previous_selection_and_cleans_temporary(self):
        self.store.select(self.root / 'first.db', None)
        previous = self.store.path.read_bytes()
        with (patch('saga.desktop.settings.os.replace', side_effect=PermissionError('blocked')),
              self.assertRaisesRegex(PermissionError, 'blocked')):
            self.store.select(self.root / 'second.db', None)
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(list(self.store.path.parent.glob('.settings-*.tmp')), [])

    def test_corrupt_settings_are_reported_and_never_replaced(self):
        self.store.path.parent.mkdir()
        for content in ('{broken', '[]', json.dumps({'database': 'relative.db', 'exports': {}}),
                        json.dumps({'database': None, 'exports': {'relative.db': None}}),
                        json.dumps({'database': None, 'exports': {str(self.root / 'a.db'): 'relative'}})):
            with self.subTest(content=content):
                self.store.path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.store.load()
                with self.assertRaises(ValueError):
                    self.store.select(self.root / 'new.db', None)
                self.assertEqual(self.store.path.read_text(encoding='utf-8'), content)
