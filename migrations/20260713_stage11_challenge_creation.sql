-- Stage 11: structured challenge participation, schedules, evidence, and approval.
BEGIN;
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS completion_frequency VARCHAR(24) NOT NULL DEFAULT 'one_time';
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS custom_frequency_days INTEGER;
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS evidence_requirement VARCHAR(24) NOT NULL DEFAULT 'none';
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS participant_scope VARCHAR(32) NOT NULL DEFAULT 'all_members';
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS max_participants INTEGER;
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS visibility VARCHAR(24) NOT NULL DEFAULT 'family';
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS requires_admin_approval BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS allow_achievement_sharing BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE family_challenges SET completion_frequency = 'daily'
WHERE challenge_type IN ('daily_check_in', 'habit') AND completion_frequency = 'one_time';
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_family_challenges_max_participants') THEN
    ALTER TABLE family_challenges ADD CONSTRAINT ck_family_challenges_max_participants
      CHECK (max_participants IS NULL OR max_participants >= 1);
  END IF;
END $$;
COMMIT;
