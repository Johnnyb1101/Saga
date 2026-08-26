-- Saga — database schema
--
-- Every table is STRICT. SQLite does not enforce declared column types by
-- default; STRICT makes it do so. Requires SQLite 3.37 or later.
--
-- Tables are declared in dependency order. SQLite does not verify a foreign
-- key target when the table is created; it resolves it on first insert.
-- Parents come first so that failure never happens.


-- ---------------------------------------------------------------------
-- categories
-- Reference data. Small, fixed, and required before any task can exist.
-- ---------------------------------------------------------------------
CREATE TABLE categories (
    name                 TEXT    PRIMARY KEY,
    counts_toward_review INTEGER NOT NULL DEFAULT 1
                             CHECK (counts_toward_review IN (0, 1))
) STRICT;

INSERT INTO categories (name, counts_toward_review) VALUES
    ('work',     1),
    ('certs',    1),
    ('school',   1),
    ('personal', 1),
    ('home',     0);


-- ---------------------------------------------------------------------
-- projects
-- Multi-week efforts with a deadline. Tasks may belong to one.
-- ---------------------------------------------------------------------
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT,
    start_date  TEXT    CHECK (date(start_date) IS start_date),
    deadline    TEXT    CHECK (date(deadline) IS deadline),
    status      TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'on_hold', 'done', 'cancelled')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
) STRICT;


-- ---------------------------------------------------------------------
-- tasks
-- The working list. A task belongs to exactly one category and
-- optionally to one project.
-- ---------------------------------------------------------------------
CREATE TABLE tasks (
    id         INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL,
    category   TEXT    NOT NULL
                           REFERENCES categories(name) ON UPDATE CASCADE,
    project_id INTEGER REFERENCES projects(id),
    due_date   TEXT    CHECK (date(due_date) IS due_date),
    status     TEXT    NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open', 'done', 'cancelled')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
) STRICT;


-- ---------------------------------------------------------------------
-- completions
-- The permanent archive. Append-only: never edited, never deleted.
-- A completion may stand alone -- work gets done that was never a task.
-- ---------------------------------------------------------------------
CREATE TABLE completions (
    id           INTEGER PRIMARY KEY,
    task_id      INTEGER REFERENCES tasks(id),
    category     TEXT    NOT NULL
                             REFERENCES categories(name) ON UPDATE CASCADE,
    completed_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                             CHECK (datetime(completed_at) IS completed_at),
    outcome      TEXT,
    metric       TEXT,
    flagged      INTEGER NOT NULL DEFAULT 0
                             CHECK (flagged IN (0, 1))
) STRICT;