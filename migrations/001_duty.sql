-- 001 — duties
--
-- Adds the duty dimension. Existing rows get duty NULL: unlabelled, not
-- lost. ALTER TABLE can only append, so column order here matches what
-- schema.sql produces for a database created fresh.

CREATE TABLE duties (
    name TEXT PRIMARY KEY
) STRICT;

ALTER TABLE tasks       ADD COLUMN duty TEXT REFERENCES duties(name) ON UPDATE CASCADE;
ALTER TABLE completions ADD COLUMN duty TEXT REFERENCES duties(name) ON UPDATE CASCADE;

PRAGMA user_version = 1;