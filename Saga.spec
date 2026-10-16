"""Build a Windows folder containing Saga and its own Python/Qt runtime."""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
analysis = Analysis(
    [str(root / 'desktop.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'saga' / 'schema.sql'), 'saga'),
           (str(root / 'migrations' / '*.sql'), 'migrations'),
           (str(root / 'saga' / 'desktop' / 'assets' / 'saga-calendar.ico'), 'saga/desktop/assets'),
           (str(root / 'LICENSE'), '.')]
          + copy_metadata('PySide6')
          + copy_metadata('PySide6_Essentials')
          + copy_metadata('shiboken6'),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name='Saga',
    icon=str(root / 'saga' / 'desktop' / 'assets' / 'saga-calendar.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas,
                 strip=False, upx=False, name='Saga')
