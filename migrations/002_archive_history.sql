-- Capture the context available at migration time, not invented historical values.
ALTER TABLE completions ADD COLUMN task_title TEXT;
ALTER TABLE completions ADD COLUMN due_date TEXT CHECK (date(due_date) IS due_date);
ALTER TABLE completions ADD COLUMN project_name TEXT;
ALTER TABLE completions ADD COLUMN review_counting INTEGER NOT NULL DEFAULT 0
    CHECK (review_counting IN (0, 1));
ALTER TABLE completions ADD COLUMN snapshot_source TEXT NOT NULL DEFAULT 'captured'
    CHECK (snapshot_source IN ('captured', 'backfilled'));
ALTER TABLE completions ADD COLUMN recorded_at TEXT CHECK (datetime(recorded_at) IS recorded_at);
ALTER TABLE completions ADD COLUMN supersedes_id INTEGER REFERENCES completions(id);
ALTER TABLE completions ADD COLUMN correction_reason TEXT CHECK (
    (supersedes_id IS NULL AND correction_reason IS NULL)
    OR (supersedes_id IS NOT NULL AND correction_reason IS NOT NULL
        AND length(trim(correction_reason)) > 0));
CREATE UNIQUE INDEX completions_one_successor ON completions(supersedes_id);

UPDATE completions SET
    task_title = (SELECT title FROM tasks WHERE id = completions.task_id),
    due_date = (SELECT due_date FROM tasks WHERE id = completions.task_id),
    project_name = (SELECT p.name FROM tasks t JOIN projects p ON p.id = t.project_id
                    WHERE t.id = completions.task_id),
    review_counting = (SELECT counts_toward_review FROM categories WHERE name = completions.category),
    snapshot_source = 'backfilled';

CREATE VIEW current_completions AS
SELECT c.* FROM completions c
WHERE NOT EXISTS (SELECT 1 FROM completions next WHERE next.supersedes_id = c.id);

CREATE TRIGGER completions_no_update BEFORE UPDATE ON completions
BEGIN SELECT RAISE(ABORT, 'completion archive is append-only; use correct'); END;

CREATE TRIGGER completions_no_delete BEFORE DELETE ON completions
BEGIN SELECT RAISE(ABORT, 'completion archive is append-only'); END;

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

PRAGMA user_version = 2;
