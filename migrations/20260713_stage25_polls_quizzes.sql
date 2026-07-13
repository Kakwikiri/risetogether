BEGIN;

ALTER TABLE family_polls ADD COLUMN IF NOT EXISTS results_visibility VARCHAR(24) NOT NULL DEFAULT 'after_vote';
UPDATE family_polls SET results_visibility = 'after_vote' WHERE results_visibility IS NULL OR results_visibility NOT IN ('always', 'after_vote', 'after_close');
ALTER TABLE family_polls DROP CONSTRAINT IF EXISTS ck_family_poll_results_visibility;
ALTER TABLE family_polls ADD CONSTRAINT ck_family_poll_results_visibility CHECK (results_visibility IN ('always', 'after_vote', 'after_close'));

ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS pass_mark INTEGER NOT NULL DEFAULT 60;
ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS attempt_limit INTEGER NOT NULL DEFAULT 1;
UPDATE quizzes SET attempt_limit = CASE WHEN allow_multiple_attempts THEN 10 ELSE 1 END WHERE attempt_limit IS NULL OR attempt_limit < 1;
ALTER TABLE quizzes DROP CONSTRAINT IF EXISTS ck_quiz_pass_mark;
ALTER TABLE quizzes ADD CONSTRAINT ck_quiz_pass_mark CHECK (pass_mark BETWEEN 1 AND 100);
ALTER TABLE quizzes DROP CONSTRAINT IF EXISTS ck_quiz_attempt_limit;
ALTER TABLE quizzes ADD CONSTRAINT ck_quiz_attempt_limit CHECK (attempt_limit BETWEEN 1 AND 10);

ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS explanation TEXT NOT NULL DEFAULT '';
ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS percentage INTEGER NOT NULL DEFAULT 0;
ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS passed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS points_awarded INTEGER NOT NULL DEFAULT 0;
ALTER TABLE quiz_attempts DROP CONSTRAINT IF EXISTS ck_quiz_attempt_percentage;
ALTER TABLE quiz_attempts ADD CONSTRAINT ck_quiz_attempt_percentage CHECK (percentage BETWEEN 0 AND 100);
CREATE INDEX IF NOT EXISTS ix_quiz_attempt_user_quiz_submitted ON quiz_attempts (user_id, quiz_id, submitted_at);
CREATE INDEX IF NOT EXISTS ix_family_poll_vote_poll_user ON family_poll_votes (poll_id, user_id);

INSERT INTO site_settings (key, value, updated_at)
VALUES ('migration_stage25_polls_quizzes', 'complete', CURRENT_TIMESTAMP)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP;

COMMIT;
