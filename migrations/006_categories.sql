ALTER TABLE categories ADD COLUMN name_key TEXT NOT NULL DEFAULT '';
UPDATE categories SET name_key=role_name_key(name);
CREATE UNIQUE INDEX categories_name_key ON categories(name_key);
ALTER TABLE categories ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'archived'));
PRAGMA user_version = 6;
