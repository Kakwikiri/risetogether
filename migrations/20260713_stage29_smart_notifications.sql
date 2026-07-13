BEGIN;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS group_key VARCHAR(180) NOT NULL DEFAULT '';
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(180);
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS event_count INTEGER NOT NULL DEFAULT 1;
UPDATE notifications SET updated_at=created_at WHERE updated_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_notifications_user_group_unread ON notifications(user_id,group_key,seen,updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_dedupe_key ON notifications(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS notification_preferences(
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category VARCHAR(48) NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_notification_preference_user_category UNIQUE(user_id,category)
);
CREATE INDEX IF NOT EXISTS ix_notification_preferences_user ON notification_preferences(user_id);
CREATE TABLE IF NOT EXISTS notification_delivery_keys(
  key VARCHAR(180) PRIMARY KEY,
  notification_id INTEGER REFERENCES notifications(id) ON DELETE CASCADE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_notification_delivery_keys_notification ON notification_delivery_keys(notification_id);
INSERT INTO site_settings(key,value,updated_at) VALUES('migration_stage29_smart_notifications','complete',CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;
COMMIT;
