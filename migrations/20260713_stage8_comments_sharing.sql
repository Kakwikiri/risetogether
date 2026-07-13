-- Stage 8: typed comment encouragement and immutable attributed feed shares.
BEGIN;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP;
ALTER TABLE comment_reactions ADD COLUMN IF NOT EXISTS type VARCHAR(32) NOT NULL DEFAULT 'support';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS original_post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_public_post_reshare
    ON posts (original_post_id, user_id)
    WHERE original_post_id IS NOT NULL AND audience = 'public';
CREATE UNIQUE INDEX IF NOT EXISTS uq_family_post_reshare
    ON posts (original_post_id, user_id, family_id)
    WHERE original_post_id IS NOT NULL AND family_id IS NOT NULL;
COMMIT;
