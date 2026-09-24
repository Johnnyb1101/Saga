-- Keep original duty names and every archived row intact.
CREATE TABLE category_roles (
    category TEXT NOT NULL REFERENCES categories(name),
    duty TEXT NOT NULL REFERENCES duties(name),
    name_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired', 'needs_review')),
    PRIMARY KEY (category, duty)
) STRICT;

CREATE UNIQUE INDEX category_roles_active_name
ON category_roles(category, name_key) WHERE status='active';

WITH observed AS (
    SELECT category, duty FROM tasks WHERE duty IS NOT NULL
    UNION SELECT category, duty FROM completions WHERE duty IS NOT NULL
    UNION SELECT category, duty FROM recurrences WHERE duty IS NOT NULL
), keyed AS (
    SELECT category, duty, role_name_key(duty) AS name_key FROM observed
)
INSERT INTO category_roles(category, duty, name_key, status)
SELECT category, duty, name_key,
       CASE WHEN name_key='' OR count(*) OVER (PARTITION BY category, name_key)>1
            THEN 'needs_review' ELSE 'active' END
FROM keyed;

-- Unused legacy duties remain unassigned until explicitly added to a category.
PRAGMA user_version = 4;
