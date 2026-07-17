BEGIN;

ALTER TABLE families
ADD COLUMN IF NOT EXISTS certificate_style VARCHAR(32) NOT NULL DEFAULT 'growth';

INSERT INTO site_settings(key,value,updated_at)
VALUES('migration_family_certificate_styles','complete',CURRENT_TIMESTAMP)
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;

COMMIT;
