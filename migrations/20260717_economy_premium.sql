BEGIN;

CREATE TABLE IF NOT EXISTS premium_subscriptions (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
  plan VARCHAR(20) NOT NULL,
  billing_period VARCHAR(20) NOT NULL,
  purchased_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  auto_renew BOOLEAN NOT NULL DEFAULT FALSE,
  granted_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_premium_subscription_one_subject CHECK (
    (user_id IS NOT NULL AND family_id IS NULL) OR
    (user_id IS NULL AND family_id IS NOT NULL)
  ),
  CONSTRAINT ck_premium_subscription_plan CHECK (plan IN ('personal','family')),
  CONSTRAINT ck_premium_subscription_period CHECK (billing_period IN ('monthly','yearly','lifetime')),
  CONSTRAINT ck_premium_subscription_status CHECK (status IN ('active','expired','cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_premium_subscriptions_user_id ON premium_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS ix_premium_subscriptions_family_id ON premium_subscriptions(family_id);
CREATE INDEX IF NOT EXISTS ix_premium_subscriptions_expires_at ON premium_subscriptions(expires_at);
CREATE INDEX IF NOT EXISTS ix_premium_subscriptions_status ON premium_subscriptions(status);

CREATE TABLE IF NOT EXISTS verification_applications (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
  submitted_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  application_type VARCHAR(32) NOT NULL,
  statement TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TIMESTAMP,
  reviewed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  review_note VARCHAR(500) NOT NULL DEFAULT '',
  CONSTRAINT ck_verification_application_one_subject CHECK (
    (user_id IS NOT NULL AND family_id IS NULL) OR
    (user_id IS NULL AND family_id IS NOT NULL)
  ),
  CONSTRAINT ck_verification_application_type CHECK (
    application_type IN ('verified_user','official_organization','trusted_family')
  ),
  CONSTRAINT ck_verification_application_status CHECK (
    status IN ('pending','approved','rejected','withdrawn')
  )
);
CREATE INDEX IF NOT EXISTS ix_verification_applications_user_id ON verification_applications(user_id);
CREATE INDEX IF NOT EXISTS ix_verification_applications_family_id ON verification_applications(family_id);
CREATE INDEX IF NOT EXISTS ix_verification_applications_status ON verification_applications(status);

INSERT INTO site_settings(key,value,updated_at)
VALUES('migration_economy_premium','complete',CURRENT_TIMESTAMP)
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;

COMMIT;
