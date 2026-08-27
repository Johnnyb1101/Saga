"""Database connection and initialisation.

Every connection to the Saga database is opened through this module so that
foreign key enforcement is turned on in exactly one place. SQLite leaves it
off by default and the setting is per-connection, not stored in the file.
"""

import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / "data" / "saga.db"
SCHEMA_PATH = HERE / "schema.sql"


def connect(db_path=DB_PATH):
    """Open a connection to an existing database."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No database at {db_path}. Run 'python main.py init' first."
        )
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
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