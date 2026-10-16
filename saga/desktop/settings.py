"""Local desktop settings, separate from the archive and CLI defaults."""

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

THEME_MODES = ('system', 'light', 'dark')
THEME_ACCENTS = ('teal', 'blue', 'magenta', 'purple')


def _validate_theme(theme):
    if not isinstance(theme, dict) or set(theme) != {'mode', 'accent'}:
        raise ValueError('Saved theme must contain mode and accent')
    if theme['mode'] not in THEME_MODES:
        raise ValueError('Theme mode must be system, light, or dark')
    if theme['accent'] not in THEME_ACCENTS:
        raise ValueError('Theme accent must be teal, blue, magenta, or purple')


def data_directory():
    # The application name is also fixed by the entry point. AppLocalDataLocation
    # avoids Windows roaming storage and does not depend on the working directory.
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.setApplicationName('Saga')
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))


def default_database():
    return data_directory() / 'data' / 'saga.db'


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else data_directory() / 'settings.json'

    def load(self):
        if not self.path.exists():
            return {'database': None, 'exports': {}}
        try:
            settings = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Cannot read desktop settings at {self.path}: {exc}") from exc
        if not isinstance(settings, dict):
            raise ValueError(f"Invalid desktop settings at {self.path}")  # noqa: TRY004 - malformed JSON data
        database, exports = settings.get('database'), settings.get('exports')
        if (database is not None and (not isinstance(database, str) or not Path(database).is_absolute())):
            raise ValueError('Saved archive path must be absolute')
        if not isinstance(exports, dict) or any(
                not isinstance(key, str) or not Path(key).is_absolute()
                or (value is not None and (not isinstance(value, str) or not Path(value).is_absolute()))
                for key, value in exports.items()):
            raise ValueError('Saved export paths must be absolute')
        result = {'database': database, 'exports': exports}
        if 'theme' in settings:
            _validate_theme(settings['theme'])
            result['theme'] = settings['theme']
        return result

    def select(self, database, export_dir):
        settings = self.load()
        database = str(Path(database).resolve())
        settings['database'] = database
        settings['exports'][database] = str(Path(export_dir).resolve()) if export_dir is not None else None
        self._save(settings)

    def theme(self):
        return self.load().get('theme', {'mode': 'system', 'accent': 'teal'})

    def set_theme(self, mode, accent):
        theme = {'mode': mode, 'accent': accent}
        _validate_theme(theme)
        settings = self.load()
        settings['theme'] = theme
        self._save(settings)

    def _save(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.settings-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(settings, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def export_for(self, database):
        value = self.load()['exports'].get(str(Path(database).resolve()))
        return Path(value) if value is not None else None
