BEGIN;
CREATE TABLE IF NOT EXISTS goals (
 id SERIAL PRIMARY KEY, scope VARCHAR(16) NOT NULL, owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 family_id INTEGER REFERENCES families(id) ON DELETE CASCADE, title VARCHAR(160) NOT NULL,
 description TEXT NOT NULL DEFAULT '', category VARCHAR(48) NOT NULL, start_date DATE NOT NULL,
 target_date DATE, measurement_type VARCHAR(20) NOT NULL, target_amount DOUBLE PRECISION NOT NULL,
 current_progress DOUBLE PRECISION NOT NULL DEFAULT 0, visibility VARCHAR(16) NOT NULL DEFAULT 'private',
 status VARCHAR(16) NOT NULL DEFAULT 'active', completed_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT ck_goal_scope CHECK(scope IN ('personal','family')),
 CONSTRAINT ck_goal_visibility CHECK(visibility IN ('private','family','public')),
 CONSTRAINT ck_goal_measurement CHECK(measurement_type IN ('number','percentage','binary')),
 CONSTRAINT ck_goal_status CHECK(status IN ('active','completed','archived')),
 CONSTRAINT ck_goal_target_positive CHECK(target_amount > 0)
);
CREATE INDEX IF NOT EXISTS ix_goals_owner_user_id ON goals(owner_user_id); CREATE INDEX IF NOT EXISTS ix_goals_family_id ON goals(family_id);
CREATE TABLE IF NOT EXISTS goal_participants (id SERIAL PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, CONSTRAINT uq_goal_participant UNIQUE(goal_id,user_id));
CREATE TABLE IF NOT EXISTS goal_milestones (id SERIAL PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE, title VARCHAR(160) NOT NULL, target_amount DOUBLE PRECISION NOT NULL, completed_at TIMESTAMP, CONSTRAINT uq_goal_milestone_target UNIQUE(goal_id,target_amount));
CREATE TABLE IF NOT EXISTS goal_progress_entries (id SERIAL PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, amount DOUBLE PRECISION NOT NULL, note VARCHAR(500) NOT NULL DEFAULT '', evidence_url VARCHAR(255) NOT NULL DEFAULT '', evidence_type VARCHAR(24) NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS goal_activities (id SERIAL PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, event_type VARCHAR(32) NOT NULL, message VARCHAR(300) NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS goal_encouragements (id SERIAL PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, reaction VARCHAR(24) NOT NULL, message VARCHAR(500) NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, CONSTRAINT uq_goal_encouragement_user UNIQUE(goal_id,user_id));
ALTER TABLE posts ADD COLUMN IF NOT EXISTS goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_goal ON posts(goal_id) WHERE goal_id IS NOT NULL;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM site_settings WHERE key='migration_stage24_family_goals') THEN
  INSERT INTO goals(scope,owner_user_id,family_id,title,description,category,start_date,target_date,measurement_type,target_amount,current_progress,visibility,status,created_at)
  SELECT 'family',f.owner_id,f.id,f.goal_title,COALESCE(f.goal_description,''),'family_growth',COALESCE(f.start_date::date,f.created_at::date),f.target_date::date,'percentage',100,0,'family','active',f.created_at
  FROM families f WHERE f.owner_id IS NOT NULL AND COALESCE(f.goal_title,'') <> '';
  INSERT INTO goal_participants(goal_id,user_id) SELECT g.id,g.owner_user_id FROM goals g WHERE g.scope='family' ON CONFLICT(goal_id,user_id) DO NOTHING;
  INSERT INTO site_settings(key,value,updated_at) VALUES('migration_stage24_family_goals','complete',CURRENT_TIMESTAMP);
 END IF;
END $$;
COMMIT;
