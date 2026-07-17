BEGIN;

CREATE TABLE IF NOT EXISTS referral_codes (
  id SERIAL PRIMARY KEY,
  inviter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
  token VARCHAR(80) NOT NULL UNIQUE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_referral_codes_inviter_id ON referral_codes(inviter_id);
CREATE INDEX IF NOT EXISTS ix_referral_codes_family_id ON referral_codes(family_id);
CREATE INDEX IF NOT EXISTS ix_referral_codes_token ON referral_codes(token);

CREATE TABLE IF NOT EXISTS referral_conversions (
  id SERIAL PRIMARY KEY,
  referral_code_id INTEGER NOT NULL REFERENCES referral_codes(id) ON DELETE CASCADE,
  referred_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  qualified_at TIMESTAMP,
  rewarded_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_referral_conversions_referral_code_id ON referral_conversions(referral_code_id);
CREATE INDEX IF NOT EXISTS ix_referral_conversions_referred_user_id ON referral_conversions(referred_user_id);

CREATE TABLE IF NOT EXISTS user_activity_days (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  activity_date DATE NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_user_activity_day UNIQUE(user_id,activity_date)
);
CREATE INDEX IF NOT EXISTS ix_user_activity_days_user_id ON user_activity_days(user_id);
CREATE INDEX IF NOT EXISTS ix_user_activity_days_activity_date ON user_activity_days(activity_date);

INSERT INTO site_settings(key,value,updated_at)
VALUES('migration_referral_rewards','complete',CURRENT_TIMESTAMP)
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;

COMMIT;
