BEGIN;

ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS reversed_at TIMESTAMP;
ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS reversed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS reversal_reason VARCHAR(500) NOT NULL DEFAULT '';
ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS suspicious BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS suspicious_reason VARCHAR(500) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS ix_point_transactions_suspicious ON point_transactions(suspicious);

CREATE TABLE IF NOT EXISTS point_security_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    family_id INTEGER REFERENCES families(id) ON DELETE SET NULL,
    event_type VARCHAR(64) NOT NULL,
    source_type VARCHAR(64) NOT NULL DEFAULT '',
    source_id INTEGER,
    details VARCHAR(500) NOT NULL DEFAULT '',
    ip_address VARCHAR(64) NOT NULL DEFAULT '',
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_point_security_events_user_id ON point_security_events(user_id);
CREATE INDEX IF NOT EXISTS ix_point_security_events_family_id ON point_security_events(family_id);
CREATE INDEX IF NOT EXISTS ix_point_security_events_event_type ON point_security_events(event_type);
CREATE INDEX IF NOT EXISTS ix_point_security_events_created_at ON point_security_events(created_at);

COMMIT;
