-- Stage 10: preserve historical rewards, normalize future rewards, and support periods.
BEGIN;
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS reward_tier VARCHAR(32) NOT NULL DEFAULT 'easy';
ALTER TABLE challenge_completions ADD COLUMN IF NOT EXISTS period_key VARCHAR(32) NOT NULL DEFAULT 'once';
ALTER TABLE challenge_completions ADD COLUMN IF NOT EXISTS points_awarded INTEGER NOT NULL DEFAULT 0;

-- Run the data conversion once, preserving later global configuration changes.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM site_settings WHERE key = 'migration_stage10_rewards') THEN
    UPDATE challenge_completions SET points_awarded = family_challenges.points
    FROM family_challenges WHERE challenge_completions.challenge_id = family_challenges.id;
    UPDATE challenge_completions SET period_key = TO_CHAR(completed_at, 'YYYY-MM-DD')
    FROM family_challenges
    WHERE challenge_completions.challenge_id = family_challenges.id
      AND family_challenges.challenge_type IN ('daily_check_in', 'habit')
      AND challenge_completions.period_key = 'once';
    UPDATE family_challenges SET reward_tier = CASE
      WHEN points <= 5 THEN 'small' WHEN points <= 10 THEN 'easy'
      WHEN points <= 25 THEN 'medium' WHEN points <= 50 THEN 'hard' ELSE 'major' END;
    UPDATE family_challenges SET reward_tier = 'small' WHERE challenge_type = 'daily_check_in';
    UPDATE family_challenges SET reward_tier = 'easy' WHERE challenge_type = 'task';
    UPDATE family_challenges SET reward_tier = 'easy'
      WHERE challenge_type = 'habit' AND reward_tier NOT IN ('small', 'easy');
    UPDATE family_challenges SET reward_tier = 'medium'
      WHERE challenge_type IN ('learning_lesson', 'quiz')
        AND reward_tier NOT IN ('small', 'easy', 'medium');
    UPDATE family_challenges SET points = CASE reward_tier
      WHEN 'small' THEN 5 WHEN 'easy' THEN 10 WHEN 'medium' THEN 25
      WHEN 'hard' THEN 50 WHEN 'major' THEN 100 ELSE 10 END;
    INSERT INTO site_settings (key, value, updated_at)
    VALUES ('migration_stage10_rewards', 'complete', CURRENT_TIMESTAMP);
  END IF;
END $$;

ALTER TABLE challenge_completions DROP CONSTRAINT IF EXISTS uq_challenge_completion_user;
CREATE UNIQUE INDEX IF NOT EXISTS uq_challenge_completion_period
    ON challenge_completions (challenge_id, user_id, period_key);
COMMIT;
