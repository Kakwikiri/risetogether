BEGIN;

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS interests TEXT NOT NULL DEFAULT '';

INSERT INTO site_settings(key,value,updated_at)
VALUES('migration_profile_interests','complete',CURRENT_TIMESTAMP)
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;

COMMIT;
