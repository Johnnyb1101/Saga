-- Existing series remain unbounded; only newly supplied end dates limit them.
ALTER TABLE recurrences ADD COLUMN end_date TEXT
    CHECK (end_date IS NULL OR
        (length(end_date) = 10 AND end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
         AND end_date >= '0001-01-01' AND date(end_date, '+0 days') IS end_date
         AND end_date >= anchor_date));

PRAGMA user_version = 8;
