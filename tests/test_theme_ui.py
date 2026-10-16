"""Theme choices apply immediately while preserving drafts and saved state."""
import os
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QMessageBox

    from saga.desktop.settings import SettingsStore
    from saga.desktop.themes import ACCENTS, ThemePicker, apply_theme
    from saga.desktop.window import MainWindow


@unittest.skipUnless(HAS_QT, 'Optional PySide6 dependency not installed')
class ThemeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = SettingsStore(self.root / 'settings.json')
        self.window = MainWindow(self.store)
        self.addCleanup(self.window.deleteLater)

    def test_live_picker_reopens_and_keeps_draft(self):
        self.window.create_new_archive(self.root / 'test.db')
        self.window.entry.task.title.setText('Invented unsaved task')
        dialog = self.window._settings_dialog()
        self.addCleanup(dialog.deleteLater)
        picker = dialog.findChild(ThemePicker)
        picker.mode.setCurrentIndex(picker.mode.findData('dark'))
        picker.buttons['magenta'].click()
        self.assertEqual(self.store.theme(), {'mode': 'dark', 'accent': 'magenta'})
        self.assertEqual(self.window.entry.task.title.text(), 'Invented unsaved task')
        self.assertEqual(self.window.palette().color(QPalette.ColorRole.Highlight).name(), ACCENTS['magenta'][1])
        reopened = MainWindow(self.store)
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened.theme, self.window.theme)

    def test_failed_write_keeps_picker_and_colors(self):
        dialog = self.window._settings_dialog()
        self.addCleanup(dialog.deleteLater)
        picker = dialog.findChild(ThemePicker)
        before = self.window.styleSheet()
        with patch.object(self.store, 'set_theme', side_effect=OSError('blocked')), patch.object(QMessageBox, 'warning'):
            picker.buttons['purple'].click()
            picker.mode.setCurrentIndex(picker.mode.findData('dark'))
        self.assertEqual(picker.current, {'mode': 'system', 'accent': 'teal'})
        self.assertTrue(picker.buttons['teal'].isChecked())
        self.assertEqual(picker.mode.currentData(), 'system')
        self.assertEqual(self.window.styleSheet(), before)

    def test_system_follows_reported_os_scheme(self):
        with patch('saga.desktop.themes.QApplication') as application:
            hints = application.instance.return_value.styleHints.return_value
            hints.colorScheme.return_value = Qt.ColorScheme.Dark
            self.window._system_theme_changed()
            self.assertEqual(self.window.palette().color(QPalette.ColorRole.Window).name(), '#202126')
            hints.colorScheme.return_value = Qt.ColorScheme.Light
            self.window._system_theme_changed()
            self.assertEqual(self.window.palette().color(QPalette.ColorRole.Window).name(), '#f3f4f7')

    def test_accent_text_contrast_for_all_explicit_themes(self):
        def luminance(color):
            values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                      for v in (color.redF(), color.greenF(), color.blueF())]
            return sum(v * weight for v, weight in zip(values, (0.2126, 0.7152, 0.0722)))
        for mode in ('light', 'dark'):
            for accent in ACCENTS:
                with self.subTest(mode=mode, accent=accent):
                    apply_theme(self.window, mode, accent)
                    palette = self.window.palette()
                    levels = sorted(luminance(palette.color(role)) for role in
                                    (QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText))
                    self.assertGreaterEqual((levels[1] + 0.05) / (levels[0] + 0.05), 4.5)
