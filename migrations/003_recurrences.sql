-- Existing tasks remain one-off tasks; no archive records are changed.
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

ALTER TABLE tasks ADD COLUMN recurrence_id INTEGER REFERENCES recurrences(id);
ALTER TABLE tasks ADD COLUMN occurrence_index INTEGER CHECK (
    (recurrence_id IS NULL AND occurrence_index IS NULL)
    OR (recurrence_id IS NOT NULL AND occurrence_index IS NOT NULL AND occurrence_index >= 0));

CREATE UNIQUE INDEX tasks_one_open_occurrence ON tasks(recurrence_id)
    WHERE recurrence_id IS NOT NULL AND status = 'open';
CREATE UNIQUE INDEX tasks_unique_occurrence ON tasks(recurrence_id, occurrence_index)
    WHERE recurrence_id IS NOT NULL;

PRAGMA user_version = 3;
