BEGIN;
CREATE TABLE IF NOT EXISTS post_media (
  id SERIAL PRIMARY KEY,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  media_url VARCHAR(255) NOT NULL,
  media_type VARCHAR(32) NOT NULL DEFAULT 'image',
  position INTEGER NOT NULL,
  CONSTRAINT uq_post_media_position UNIQUE(post_id, position)
);
CREATE INDEX IF NOT EXISTS ix_post_media_post_id ON post_media (post_id);
CREATE TABLE IF NOT EXISTS message_attachments (
  id SERIAL PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  media_url VARCHAR(255) NOT NULL,
  media_type VARCHAR(32) NOT NULL DEFAULT 'image',
  position INTEGER NOT NULL,
  CONSTRAINT uq_message_attachment_position UNIQUE(message_id, position)
);
CREATE INDEX IF NOT EXISTS ix_message_attachments_message_id ON message_attachments (message_id);
INSERT INTO site_settings(key,value,updated_at) VALUES('migration_multi_photo_galleries','complete',CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;
COMMIT;
