"""Theme preferences share desktop settings with archive choices."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from saga.desktop.settings import SettingsStore


@unittest.skipUnless(HAS_QT, 'Install requirements-desktop.txt for desktop tests')
class ThemeSettingsTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.store = SettingsStore(self.root / 'settings' / 'settings.json')

    def test_legacy_settings_use_defaults_without_adding_theme(self):
        self.assertEqual(self.store.theme(), {'mode': 'system', 'accent': 'teal'})
        self.assertEqual(self.store.load(), {'database': None, 'exports': {}})
        self.assertFalse(self.store.path.exists())

        self.store.select(self.root / 'archive.db', None)
        self.assertEqual(self.store.theme(), {'mode': 'system', 'accent': 'teal'})
        self.assertNotIn('theme', self.store.load())

    def test_theme_and_archive_choices_survive_either_update_order(self):
        first, second = self.root / 'first.db', self.root / 'second.db'
        export_dir = self.root / 'exports'
        self.store.select(first, export_dir)
        self.store.set_theme('dark', 'purple')
        self.store.select(second, None)
        reopened = SettingsStore(self.store.path)
        self.assertEqual(reopened.theme(), {'mode': 'dark', 'accent': 'purple'})
        self.assertEqual(reopened.export_for(first), export_dir.resolve())
        self.assertIsNone(reopened.export_for(second))
        self.assertEqual(reopened.load()['database'], str(second.resolve()))

        reopened.set_theme('light', 'blue')
        self.assertEqual(self.store.theme(), {'mode': 'light', 'accent': 'blue'})
        self.assertEqual(self.store.export_for(first), export_dir.resolve())

    def test_invalid_theme_fields_are_reported_and_never_overwritten(self):
        self.store.select(self.root / 'archive.db', None)
        valid = self.store.load()
        invalid = (
            None, [], {'mode': 'sepia', 'accent': 'teal'},
            {'mode': 'dark', 'accent': 'orange'},
            {'mode': 'dark'}, {'mode': 'dark', 'accent': 'teal', 'extra': True},
        )
        for theme in invalid:
            with self.subTest(theme=theme):
                content = json.dumps({**valid, 'theme': theme})
                self.store.path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.store.load()
                with self.assertRaises(ValueError):
                    self.store.set_theme('light', 'blue')
                with self.assertRaises(ValueError):
                    self.store.select(self.root / 'other.db', None)
                self.assertEqual(self.store.path.read_text(encoding='utf-8'), content)

    def test_invalid_arguments_do_not_modify_settings(self):
        self.store.set_theme('dark', 'magenta')
        previous = self.store.path.read_bytes()
        for mode, accent in (('sepia', 'teal'), ('dark', 'orange'), (None, 'blue')):
            with self.subTest(mode=mode, accent=accent), self.assertRaises(ValueError):
                self.store.set_theme(mode, accent)
            self.assertEqual(self.store.path.read_bytes(), previous)

    def test_failed_theme_replace_preserves_previous_settings_and_cleans_temporary(self):
        self.store.select(self.root / 'archive.db', self.root / 'exports')
        self.store.set_theme('dark', 'magenta')
        previous = self.store.path.read_bytes()
        with (patch('saga.desktop.settings.os.replace', side_effect=PermissionError('blocked')),
              self.assertRaisesRegex(PermissionError, 'blocked')):
            self.store.set_theme('light', 'blue')
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(list(self.store.path.parent.glob('.settings-*.tmp')), [])
