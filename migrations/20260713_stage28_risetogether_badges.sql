BEGIN;
CREATE TABLE IF NOT EXISTS rise_badge_assignments (
  id SERIAL PRIMARY KEY,
  badge_type VARCHAR(40) NOT NULL,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  verification_note VARCHAR(500) NOT NULL,
  assigned_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at TIMESTAMP,
  CONSTRAINT uq_rise_badge_user_type UNIQUE (badge_type,user_id),
  CONSTRAINT uq_rise_badge_family_type UNIQUE (badge_type,family_id),
  CONSTRAINT ck_rise_badge_one_subject CHECK ((user_id IS NOT NULL AND family_id IS NULL) OR (user_id IS NULL AND family_id IS NOT NULL)),
  CONSTRAINT ck_rise_badge_assignable_type CHECK (badge_type IN ('verified_person','official_organization','trusted_family','platform_moderator')),
  CONSTRAINT ck_rise_badge_status CHECK (status IN ('active','revoked'))
);
CREATE INDEX IF NOT EXISTS ix_rise_badge_user_active ON rise_badge_assignments(user_id,status);
CREATE INDEX IF NOT EXISTS ix_rise_badge_family_active ON rise_badge_assignments(family_id,status);
INSERT INTO rise_badge_assignments(badge_type,user_id,status,verification_note,assigned_by_id,assigned_at)
SELECT 'verified_person',u.id,'active','Legacy verification migrated into the audited RiseTogether badge system.',(SELECT id FROM users WHERE admin_role='super_admin' ORDER BY created_at ASC LIMIT 1),CURRENT_TIMESTAMP
FROM users u WHERE u.is_verified=TRUE ON CONFLICT(badge_type,user_id) DO NOTHING;
INSERT INTO rise_badge_assignments(badge_type,user_id,status,verification_note,assigned_by_id,assigned_at)
SELECT 'platform_moderator',u.id,'active','Existing website moderation role migrated into the protected badge system.',(SELECT id FROM users WHERE admin_role='super_admin' ORDER BY created_at ASC LIMIT 1),CURRENT_TIMESTAMP
FROM users u WHERE u.admin_role IN ('moderator','admin') ON CONFLICT(badge_type,user_id) DO NOTHING;
INSERT INTO site_settings(key,value,updated_at) VALUES('migration_stage28_risetogether_badges','complete',CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;
COMMIT;
