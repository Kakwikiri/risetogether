BEGIN;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'Africa/Kampala';
CREATE TABLE IF NOT EXISTS user_streaks (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 streak_type VARCHAR(32) NOT NULL, current_count INTEGER NOT NULL DEFAULT 0,
 best_count INTEGER NOT NULL DEFAULT 0, previous_count INTEGER NOT NULL DEFAULT 0,
 last_activity_date DATE, grace_days_available INTEGER NOT NULL DEFAULT 1,
 last_warning_date DATE, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_user_streak_type UNIQUE(user_id,streak_type)
);
CREATE INDEX IF NOT EXISTS ix_user_streaks_user_id ON user_streaks(user_id);
CREATE TABLE IF NOT EXISTS streak_activities (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 streak_type VARCHAR(32) NOT NULL, activity_date DATE NOT NULL, source_type VARCHAR(48) NOT NULL,
 source_id INTEGER, unique_activity_key VARCHAR(160) NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_streak_activity_day UNIQUE(user_id,streak_type,activity_date),
 CONSTRAINT uq_streak_activity_key UNIQUE(unique_activity_key)
);
CREATE INDEX IF NOT EXISTS ix_streak_activities_user_id ON streak_activities(user_id);
CREATE INDEX IF NOT EXISTS ix_streak_activities_date ON streak_activities(activity_date);
CREATE TABLE IF NOT EXISTS streak_milestones (
 id SERIAL PRIMARY KEY, streak_id INTEGER NOT NULL REFERENCES user_streaks(id) ON DELETE CASCADE,
 milestone INTEGER NOT NULL, badge_name VARCHAR(80) NOT NULL, bonus_points INTEGER NOT NULL DEFAULT 0,
 awarded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_streak_milestone_claim UNIQUE(streak_id,milestone)
);
CREATE INDEX IF NOT EXISTS ix_streak_milestones_streak_id ON streak_milestones(streak_id);
COMMIT;
