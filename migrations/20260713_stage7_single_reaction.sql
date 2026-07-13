-- Stage 7: retain the newest reaction and enforce one choice per user/post.
-- This migration is idempotent and does not delete posts or users.
BEGIN;

DELETE FROM reactions r
USING reactions newer
WHERE r.post_id = newer.post_id
  AND r.user_id = newer.user_id
  AND r.id < newer.id;

ALTER TABLE reactions DROP CONSTRAINT IF EXISTS uq_reaction_user_type;
DROP INDEX IF EXISTS uq_reaction_user_type;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reaction_post_user
    ON reactions (post_id, user_id);

COMMIT;
