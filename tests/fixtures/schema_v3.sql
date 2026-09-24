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
-- measures
-- Named units of countable work. A measure is registered once, then
-- quantities recorded against it in completions sum cleanly.
-- Deliberately not seeded: measure names describe real duties.
-- ---------------------------------------------------------------------
CREATE TABLE measures (
    name TEXT PRIMARY KEY
) STRICT;

-- ---------------------------------------------------------------------
-- duties
-- The standing roles that work belongs to. A completion's duty decides
-- which performance statement it supports. Category is a different axis
-- answering a different question, and the two are not interchangeable.
-- Deliberately not seeded: duty names describe one operator's roles.
-- ---------------------------------------------------------------------
CREATE TABLE duties (
    name TEXT PRIMARY KEY
) STRICT;

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
-- recurrences
-- A fixed schedule and template, with at most one open task instance.
-- ---------------------------------------------------------------------
CREATE TABLE recurrences (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL REFERENCES categories(name) ON UPDATE CASCADE,
    project_id INTEGER REFERENCES projects(id),
    duty TEXT REFERENCES duties(name) ON UPDATE CASCADE,
    frequency TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly')),
    interval INTEGER NOT NULL CHECK (interval > 0),
    anchor_date TEXT NOT NULL CHECK (date(anchor_date) IS anchor_date),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'stopped')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
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
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    duty       TEXT    REFERENCES duties(name) ON UPDATE CASCADE,
    recurrence_id INTEGER REFERENCES recurrences(id),
    occurrence_index INTEGER CHECK (
        (recurrence_id IS NULL AND occurrence_index IS NULL)
        OR (recurrence_id IS NOT NULL AND occurrence_index IS NOT NULL AND occurrence_index >= 0))
) STRICT;

CREATE UNIQUE INDEX tasks_one_open_occurrence ON tasks(recurrence_id)
    WHERE recurrence_id IS NOT NULL AND status = 'open';
CREATE UNIQUE INDEX tasks_unique_occurrence ON tasks(recurrence_id, occurrence_index)
    WHERE recurrence_id IS NOT NULL;

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
    measure      TEXT    REFERENCES measures(name) ON UPDATE CASCADE,
    quantity     REAL,
    flagged      INTEGER NOT NULL DEFAULT 0
                             CHECK (flagged IN (0, 1)),
    duty         TEXT    REFERENCES duties(name) ON UPDATE CASCADE,
    task_title   TEXT,
    due_date     TEXT    CHECK (date(due_date) IS due_date),
    project_name TEXT,
    review_counting INTEGER NOT NULL DEFAULT 0 CHECK (review_counting IN (0, 1)),
    snapshot_source TEXT NOT NULL DEFAULT 'captured'
                         CHECK (snapshot_source IN ('captured', 'backfilled')),
    recorded_at  TEXT CHECK (datetime(recorded_at) IS recorded_at),
    supersedes_id INTEGER REFERENCES completions(id),
    correction_reason TEXT CHECK (
        (supersedes_id IS NULL AND correction_reason IS NULL)
        OR (supersedes_id IS NOT NULL AND correction_reason IS NOT NULL
            AND length(trim(correction_reason)) > 0)),

    CHECK ((measure IS NULL) = (quantity IS NULL))
) STRICT;

CREATE UNIQUE INDEX completions_one_successor ON completions(supersedes_id);

CREATE VIEW current_completions AS
SELECT c.* FROM completions c
WHERE NOT EXISTS (SELECT 1 FROM completions next WHERE next.supersedes_id = c.id);

CREATE TRIGGER completions_no_update BEFORE UPDATE ON completions
BEGIN SELECT RAISE(ABORT, 'completion archive is append-only; use correct'); END;

CREATE TRIGGER completions_no_delete BEFORE DELETE ON completions
BEGIN SELECT RAISE(ABORT, 'completion archive is append-only'); END;

-- Prevent INSERT OR REPLACE from bypassing the update/delete guards.
CREATE TRIGGER completions_validate_insert BEFORE INSERT ON completions
BEGIN
    SELECT RAISE(ABORT, 'cannot replace an archived completion')
    WHERE EXISTS (SELECT 1 FROM completions WHERE id = NEW.id);
    SELECT RAISE(ABORT, 'correct the latest revision of this completion')
    WHERE NEW.supersedes_id IS NOT NULL AND (
        NOT EXISTS (SELECT 1 FROM completions WHERE id = NEW.supersedes_id)
        OR EXISTS (SELECT 1 FROM completions WHERE supersedes_id = NEW.supersedes_id));
    SELECT RAISE(ABORT, 'a correction must keep the original task link')
    WHERE NEW.supersedes_id IS NOT NULL AND NEW.task_id IS NOT
        (SELECT task_id FROM completions WHERE id = NEW.supersedes_id);
END;

PRAGMA user_version = 3;
