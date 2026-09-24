"""Verified scheduled snapshots with opt-in, source-specific retention."""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing, contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from saga import analytics, db, reads

NAME = re.compile(r"snapshot-[0-9a-f]{32}\.db")


def _regular(path):
    return path.is_file() and not path.is_symlink()


def verify_restore(snapshot):
    """Exercise a disposable local restore, never the working database."""
    with tempfile.TemporaryDirectory(prefix="saga-restore-") as directory:
        restored = Path(directory) / "restored.db"
        shutil.copyfile(snapshot, restored)
        # Own the connection before version validation, including on failure.
        with closing(sqlite3.connect(restored)) as con:
            con.row_factory = sqlite3.Row
            version = db.schema_version(con)
            if version != db.SCHEMA_VERSION:
                raise ValueError(f"Backup schema is {version}; expected {db.SCHEMA_VERSION}. "
                                 "Upgrade the working database/code before scheduling backups.")
            if [tuple(row) for row in con.execute("PRAGMA integrity_check")] != [("ok",)]:
                raise ValueError("Restored backup failed integrity_check")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Restored backup failed foreign_key_check")
            reads.open_tasks(con)
            reads.completion_list(con)
            analytics.volume(con)


def _save_manifest(path, source, snapshots):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix="manifest-", suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump({"version": 1, "source": str(source), "snapshots": snapshots}, f)
            f.flush()
            os.fsync(f.fileno())
        except (OSError, ValueError):
            f.close()
            temporary.unlink()
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(directory):
    path = directory / "run.lock"
    try:
        handle = path.open("x")
    except FileExistsError:
        raise ValueError(f"Backup already running or stale lock: {path}. "
                         "Remove only after confirming no backup is running.") from None
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def run(source, output, keep=None):
    """Return the new snapshot and pruned names. Untracked files are never pruned."""
    if keep is not None and (type(keep) is not int or keep < 1):
        raise ValueError("keep must be a positive integer")
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    identity = hashlib.sha256(os.path.normcase(str(source)).encode()).hexdigest()[:24]
    directory = output / f"saga-scheduled-{identity}"
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("Managed backup directory must not be a link or junction")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "manifest.json"
    with _lock(directory):
        if manifest.exists() or manifest.is_symlink():
            if not _regular(manifest):
                raise ValueError("Backup manifest must be a regular file")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1
                    or data.get("source") != str(source)):
                raise ValueError("Backup manifest does not match this source")
            snapshots = data.get("snapshots")
            if (not isinstance(snapshots, list)
                    or any(not isinstance(name, str) or not NAME.fullmatch(name)
                           for name in snapshots)
                    or len(set(snapshots)) != len(snapshots)):
                raise ValueError("Invalid snapshot list in backup manifest")
        else:
            # A missing manifest in a populated directory is not a new collection.
            if any(p.name != "run.lock" for p in directory.iterdir()):
                raise ValueError("Managed backup directory contains files but no manifest")
            snapshots = []
            _save_manifest(manifest, source, snapshots)
        for name in snapshots:
            path = directory / name
            if path.is_symlink() or (path.exists() and not _regular(path)):
                raise ValueError(f"Managed snapshot must be a regular file: {path}")
        snapshot = db.backup(source, directory)
        try:
            verify_restore(snapshot)
        except (OSError, sqlite3.Error, ValueError):
            snapshot.unlink()
            raise
        published = directory / f"snapshot-{uuid.uuid4().hex}.db"
        snapshot.rename(published)
        snapshots = [name for name in snapshots if (directory / name).exists()]
        snapshots.append(published.name)
        # Register the verified copy before any deletion. Failure leaves an orphan,
        # which future retention will not adopt or delete.
        _save_manifest(manifest, source, snapshots)
        removed = []
        if keep is not None:
            for name in snapshots[:-keep]:
                path = directory / name
                if not _regular(path):
                    raise ValueError(f"Refusing to prune a nonregular snapshot: {path}")
                path.unlink()
                removed.append(name)
            _save_manifest(manifest, source, snapshots[-keep:])
        return published, removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DB_PATH)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep", type=int, help="opt in to keeping N successful snapshots")
    parser.add_argument("--log", type=Path, default=db.ROOT / "scheduled-backup.log")
    args = parser.parse_args(argv)
    logger = logging.getLogger("saga.scheduled_backup")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        handler = RotatingFileHandler(args.log, maxBytes=1_000_000, backupCount=2,
                                      encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"Cannot open backup log: {exc}\n")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    try:
        snapshot, removed = run(args.db, args.out, args.keep)
        message = f"Verified backup and restore: {snapshot}; pruned {len(removed)}"
        logger.info(message)
        print(message)
        return 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        message = f"Scheduled backup failed (retention may be incomplete): {exc}"
        logger.error(message)
        print(message, file=sys.stderr)
        return 1
    finally:
        logger.removeHandler(handler)
        handler.close()
