BEGIN;
CREATE TABLE IF NOT EXISTS family_weekly_reports (
  id SERIAL PRIMARY KEY,
  family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
  week_start DATE NOT NULL,
  week_end DATE NOT NULL,
  snapshot JSON NOT NULL,
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notified_at TIMESTAMP,
  published_at TIMESTAMP,
  published_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  published_post_id INTEGER UNIQUE REFERENCES posts(id) ON DELETE SET NULL,
  CONSTRAINT uq_family_weekly_report UNIQUE (family_id, week_start)
);
CREATE INDEX IF NOT EXISTS ix_family_weekly_reports_family_week ON family_weekly_reports (family_id, week_start DESC);
INSERT INTO site_settings(key,value,updated_at) VALUES('migration_stage26_weekly_family_reports','complete',CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=CURRENT_TIMESTAMP;
COMMIT;
