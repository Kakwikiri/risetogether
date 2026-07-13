BEGIN;

ALTER TABLE families ADD COLUMN IF NOT EXISTS banner_image VARCHAR(255) NOT NULL DEFAULT '';
ALTER TABLE families ADD COLUMN IF NOT EXISTS theme VARCHAR(32) NOT NULL DEFAULT 'classic';

CREATE TABLE IF NOT EXISTS family_upgrade_purchases (
    id SERIAL PRIMARY KEY,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    upgrade_key VARCHAR(64) NOT NULL,
    cost INTEGER NOT NULL CHECK (cost > 0),
    purchased_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    purchased_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_family_upgrade_purchase UNIQUE (family_id, upgrade_key)
);
CREATE INDEX IF NOT EXISTS ix_family_upgrade_purchases_family_id ON family_upgrade_purchases(family_id);

CREATE TABLE IF NOT EXISTS family_gallery_items (
    id SERIAL PRIMARY KEY,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    uploaded_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    media_url VARCHAR(255) NOT NULL,
    caption VARCHAR(240) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_family_gallery_items_family_id ON family_gallery_items(family_id);

COMMIT;
