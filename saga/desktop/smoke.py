"""Disposable end-to-end check for the packaged desktop executable."""

import json
import platform
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QStandardPaths, qVersion

from saga import db, export, scheduled_backup
from saga.application import ArchiveService, create_archive
from saga.desktop.settings import SettingsStore
from saga.desktop.window import MainWindow


def run_smoke(app, report_path):
    """Exercise bundled resources, archive operations, and Qt without real data."""
    QStandardPaths.setTestModeEnabled(True)
    report = {
        "success": False,
        "frozen": bool(getattr(sys, "frozen", False)),
        "runtime": {"python": platform.python_version(), "qt": qVersion(),
                    "platform": platform.platform()},
        "checks": {},
    }
    step = "initialization"
    window = None
    try:
        with tempfile.TemporaryDirectory(prefix="saga-smoke-") as temporary:
            root = Path(temporary)
            step = "migration_resources"
            migrations = sorted(db.MIGRATIONS_PATH.glob("*.sql"))
            numbers = [int(path.name.split("_", 1)[0]) for path in migrations]
            if numbers != list(range(1, db.SCHEMA_VERSION + 1)) or any(
                    not path.read_text(encoding="utf-8").strip() for path in migrations):
                raise RuntimeError("Bundled SQL migrations are missing or empty")
            report["checks"][step] = True

            step = "schema_and_archive"
            database = create_archive(root / "archive.db")
            service = ArchiveService(database, root / "exports")
            service.validate_archive()
            with closing(db.connect(database)) as con:
                if db.schema_version(con) != db.SCHEMA_VERSION:
                    raise RuntimeError("New archive has an unexpected schema version")
            report["checks"][step] = True

            step = "capture_and_export"
            result = service.save_capture({
                "category": "work", "outcome": "Invented smoke check outcome",
                "completed_date": None, "duty": None, "new_duty": None,
                "measurement_status": "unknown", "measure": None,
                "new_measure": None, "quantity": None,
                "remind_on": service.today()["date"], "flagged": False,
            })
            if result.export_error or result.completion_id < 1:
                raise RuntimeError(f"Capture or export failed: {result.export_error}")
            if not (root / "exports" / "manifest.json").is_file() or export.is_stale(root / "exports"):
                raise RuntimeError("The exported snapshot is missing or stale")
            if len(service.today()["followups"]) != 1:
                raise RuntimeError("Saved capture was not visible in Today")
            if service.create_category('Smoke category') is not None:
                raise RuntimeError('Category creation export failed')
            task_result = service.save_task({
                'title': 'Invented undated task', 'category': 'Smoke category', 'due_date': None,
            })
            if task_result.export_error or len(service.today()['undated']) != 1:
                raise RuntimeError('Open task save or export failed')
            if service.archive_category('Smoke category') is not None:
                raise RuntimeError('Category archive export failed')
            if ('Smoke category' in service.references()['categories']
                    or len(service.today()['undated']) != 1):
                raise RuntimeError('Archiving a category changed existing tasks or left it selectable')
            if service.create_category('Unused smoke category') is not None:
                raise RuntimeError('Unused category creation export failed')
            if service.delete_category('Unused smoke category') is not None:
                raise RuntimeError('Unused category deletion export failed')
            if any(row['name'] == 'Unused smoke category' for row in service.category_options()):
                raise RuntimeError('Unused category was not deleted')
            snapshot, _ = scheduled_backup.run(database, root / 'backups', keep=30)
            with closing(db.connect(snapshot)) as restored:
                if (len(restored.execute('SELECT id FROM tasks').fetchall()) != 1
                        or len(restored.execute('SELECT id FROM completions').fetchall()) != 1):
                    raise RuntimeError('Verified backup did not preserve the disposable archive')
            if not database.is_file():
                raise RuntimeError('Backing up moved the working archive')
            report["checks"][step] = True

            step = "settings_roundtrip"
            store = SettingsStore(root / "settings" / "settings.json")
            store.select(database, root / "exports")
            store.set_theme('dark', 'magenta')
            store.set_backup(database, False, root / 'backups')
            reopened = SettingsStore(store.path)
            if (reopened.load()["database"] != str(database.resolve())
                    or reopened.export_for(database) != (root / "exports").resolve()
                    or reopened.backup_for(database) != {
                        'enabled': False, 'directory': str((root / 'backups').resolve())}):
                raise RuntimeError("Selected archive and export folder did not persist")
            report["checks"][step] = True

            step = "main_window"
            window = MainWindow(reopened)
            window.show()
            app.processEvents()
            if (window.service is None or window.service.database != database.resolve()
                    or window.capture is None
                    or window.theme != {'mode': 'dark', 'accent': 'magenta'}
                    or window.windowIcon().isNull()
                    or window.tabs.tabText(window.tabs.indexOf(window.entry)) != 'Add Task'
                    or len(window.sections['undated'].cards) != 1
                    or len(window.sections["followups"].cards) != 1):
                raise RuntimeError("Main window did not load the disposable archive")
            if not window.close():
                raise RuntimeError("Main window refused to close")
            app.processEvents()
            window = None
            report["checks"][step] = True
        report["success"] = True
    except Exception as exc:  # noqa: BLE001 - every smoke failure must produce a JSON report
        report["checks"][step] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if window is not None:
            window.close()
            app.processEvents()

    try:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Could not write smoke report at {report_path}: {exc}", file=sys.stderr)
        return 1
    return 0 if report["success"] else 1
