ALTER TABLE completions ADD COLUMN measurement_status TEXT NOT NULL DEFAULT 'unspecified'
    CHECK (measurement_status IN ('measured', 'not_applicable', 'unknown', 'unspecified'));

-- Only new metadata is backfilled; original evidence stays byte-for-byte unchanged.
DROP TRIGGER completions_no_update;
UPDATE completions SET measurement_status='measured' WHERE quantity IS NOT NULL;
CREATE TRIGGER completions_no_update BEFORE UPDATE ON completions
BEGIN SELECT RAISE(ABORT, 'completion archive is append-only; use correct'); END;

CREATE TABLE measurement_followups (
    id INTEGER PRIMARY KEY,
    completion_id INTEGER NOT NULL UNIQUE REFERENCES completions(id),
    remind_on TEXT NOT NULL CHECK (date(remind_on) IS remind_on),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
) STRICT;
PRAGMA user_version = 5;
