-- NULL keeps every existing schedule anchored exactly as before.
ALTER TABLE recurrences ADD COLUMN month_day INTEGER
    CHECK (month_day IS NULL OR (frequency = 'monthly' AND month_day BETWEEN 1 AND 31));
ALTER TABLE recurrences ADD COLUMN second_month_day INTEGER
    CHECK (second_month_day IS NULL OR
        (frequency = 'monthly' AND month_day IS NOT NULL AND second_month_day BETWEEN 1 AND 31
         AND second_month_day > month_day));

PRAGMA user_version = 7;
