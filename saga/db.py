"""Database connection and initialisation.

Every connection to the Saga database is opened through this module so that
foreign key enforcement is turned on in exactly one place. SQLite leaves it
off by default and the setting is per-connection, not stored in the file.
"""

import datetime as dt
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / "data" / "saga.db"
SCHEMA_PATH = HERE / "schema.sql"


SCHEMA_VERSION = 1


def _open(db_path):
    """Open with foreign keys on, and no version check.

    A migration needs a connection to a database that is, by definition,
    at the wrong version. Nothing else should use this.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def schema_version(con):
    """The migration number this database has been brought up to."""
    return con.execute("PRAGMA user_version").fetchone()[0]


def connect(db_path=DB_PATH):
    """Open a connection to an existing database at the expected version."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No database at {db_path}. Run 'python main.py init' first."
        )
    con = _open(db_path)
    version = schema_version(con)
    if version < SCHEMA_VERSION:
        raise ValueError(
            f"{db_path} is at schema version {version}, this code expects "
            f"{SCHEMA_VERSION}. Run 'python main.py migrate' to bring it forward."
        )
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"{db_path} is at schema version {version}, which is newer than "
            f"this code ({SCHEMA_VERSION}). Update the code; do not migrate."
        )
    return con


def init_db(db_path=DB_PATH):
    """Create a new database from schema.sql. Refuses to touch an existing one."""
    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(
            f"Database already exists at {db_path}. Delete it by hand if you "
            f"really mean to start over."
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return db_path


MIGRATIONS_PATH = ROOT / "migrations"


def backup(db_path, out_dir):
    """Create and verify a unique snapshot, without modifying the source."""
    db_path = Path(db_path).resolve()
    out_dir = Path(out_dir)
    # Read-only mode also refuses a missing source instead of creating one.
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as source:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        with tempfile.NamedTemporaryFile(
            prefix=f"{db_path.stem}-{stamp}-", suffix=".db",
            dir=out_dir, delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
        verified = False
        try:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
                checks = destination.execute("PRAGMA integrity_check").fetchall()
                if checks != [("ok",)]:
                    raise ValueError(f"Backup integrity check failed: {checks}")
            verified = True
        finally:
            # Never leave a failed snapshot looking like a usable backup.
            if not verified:
                temporary.unlink()
    return temporary


def _backup(con, db_path):
    """Copy the database beside itself before anything is altered."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db_path.with_name(f"{db_path.stem}-{stamp}.bak")
    dest = sqlite3.connect(target)
    with dest:
        con.backup(dest)
    dest.close()
    return target


def migrate(db_path=DB_PATH):
    """Apply every migration this database has not had. Returns what was applied."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No database at {db_path}. Run 'python main.py init' first."
        )

    con = _open(db_path)
    pending = [p for p in sorted(MIGRATIONS_PATH.glob("*.sql"))
               if int(p.name.split("_")[0]) > schema_version(con)]
    if not pending:
        return []

    backup = _backup(con, db_path)
    applied = [f"backed up to {backup.name}"]

    for path in pending:
        number = int(path.name.split("_")[0])
        con.executescript(path.read_text())
        con.commit()
        if schema_version(con) != number:
            raise ValueError(
                f"{path.name} left the database at version {schema_version(con)}, "
                f"expected {number}. Check its PRAGMA user_version line."
            )
        applied.append(f"applied {path.name}")
    return applied
