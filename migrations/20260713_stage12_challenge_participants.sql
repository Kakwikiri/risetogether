-- Stage 12: explicit challenge participation and participant-based progress.
BEGIN;
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS mandatory_all_members BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS challenge_participants (
    id SERIAL PRIMARY KEY,
    challenge_id INTEGER NOT NULL REFERENCES family_challenges(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_challenge_participant_user UNIQUE (challenge_id, user_id)
);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM site_settings WHERE key = 'migration_stage12_participants') THEN
    INSERT INTO challenge_participants (challenge_id, user_id, joined_at)
    SELECT challenge_id, user_id, MIN(completed_at)
    FROM challenge_completions
    GROUP BY challenge_id, user_id
    ON CONFLICT (challenge_id, user_id) DO NOTHING;
    INSERT INTO site_settings (key, value, updated_at)
    VALUES ('migration_stage12_participants', 'complete', CURRENT_TIMESTAMP);
  END IF;
END $$;
COMMIT;
