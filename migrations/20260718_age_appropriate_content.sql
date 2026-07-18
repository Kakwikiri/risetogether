BEGIN;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS birth_date DATE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS age_rating VARCHAR(16) NOT NULL DEFAULT 'general';
ALTER TABLE posts DROP CONSTRAINT IF EXISTS ck_post_age_rating;
ALTER TABLE posts ADD CONSTRAINT ck_post_age_rating CHECK (age_rating IN ('general','adult'));
INSERT INTO site_settings(key,value,updated_at) VALUES('migration_age_appropriate_content','complete',CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;
COMMIT;
