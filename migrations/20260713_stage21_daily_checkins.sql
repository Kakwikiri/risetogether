BEGIN;

CREATE TABLE IF NOT EXISTS daily_check_ins (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mood VARCHAR(32) NOT NULL,
    note VARCHAR(500) NOT NULL DEFAULT '',
    privacy VARCHAR(24) NOT NULL DEFAULT 'private',
    family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
    checkin_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_daily_checkin_user_date UNIQUE (user_id, checkin_date),
    CONSTRAINT ck_daily_checkin_mood CHECK (mood IN ('happy','peaceful','motivated','okay','tired','worried','struggling','prefer_not_to_say')),
    CONSTRAINT ck_daily_checkin_privacy CHECK (privacy IN ('private','family','all_families','public'))
);

CREATE INDEX IF NOT EXISTS ix_daily_check_ins_user_id ON daily_check_ins(user_id);
CREATE INDEX IF NOT EXISTS ix_daily_check_ins_family_id ON daily_check_ins(family_id);
CREATE INDEX IF NOT EXISTS ix_daily_check_ins_checkin_date ON daily_check_ins(checkin_date);
CREATE INDEX IF NOT EXISTS ix_daily_check_ins_privacy ON daily_check_ins(privacy);

CREATE TABLE IF NOT EXISTS checkin_responses (
    id SERIAL PRIMARY KEY,
    checkin_id INTEGER NOT NULL REFERENCES daily_check_ins(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reaction VARCHAR(24) NOT NULL,
    message VARCHAR(500) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_checkin_response_user UNIQUE (checkin_id, user_id),
    CONSTRAINT ck_checkin_response_reaction CHECK (reaction IN ('support','understand','keep_going','inspire'))
);

CREATE INDEX IF NOT EXISTS ix_checkin_responses_checkin_id ON checkin_responses(checkin_id);
CREATE INDEX IF NOT EXISTS ix_checkin_responses_user_id ON checkin_responses(user_id);

COMMIT;
