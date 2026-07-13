BEGIN;

ALTER TABLE point_transactions
    ADD COLUMN IF NOT EXISTS transaction_kind VARCHAR(16) NOT NULL DEFAULT 'award';
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_point_transaction_kind') THEN
        ALTER TABLE point_transactions ADD CONSTRAINT ck_point_transaction_kind
            CHECK (transaction_kind IN ('award', 'spend'));
    END IF;
END $$;

INSERT INTO site_settings (key, value, updated_at) VALUES
    ('family_level_2_xp', '100', CURRENT_TIMESTAMP),
    ('family_level_3_xp', '300', CURRENT_TIMESTAMP),
    ('family_level_4_xp', '750', CURRENT_TIMESTAMP),
    ('family_level_5_xp', '1500', CURRENT_TIMESTAMP),
    ('family_level_6_xp', '3000', CURRENT_TIMESTAMP),
    ('family_level_7_xp', '5000', CURRENT_TIMESTAMP),
    ('family_level_rising_interval', '2500', CURRENT_TIMESTAMP)
ON CONFLICT (key) DO NOTHING;

COMMIT;
