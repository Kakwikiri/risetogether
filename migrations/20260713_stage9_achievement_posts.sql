-- Stage 9: opt-in achievement posts linked to immutable challenge completions.
BEGIN;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS auto_share_completed_challenges BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS post_type VARCHAR(32) NOT NULL DEFAULT 'standard';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS achievement_type VARCHAR(48) DEFAULT '';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS challenge_completion_id INTEGER REFERENCES challenge_completions(id) ON DELETE CASCADE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS encouraging_message VARCHAR(240) DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_challenge_completion
    ON posts (challenge_completion_id)
    WHERE challenge_completion_id IS NOT NULL;
COMMIT;
