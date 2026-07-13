BEGIN;
CREATE TABLE IF NOT EXISTS encouragement_requests (
 id SERIAL PRIMARY KEY, family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, category VARCHAR(48) NOT NULL,
 content TEXT NOT NULL, visibility VARCHAR(16) NOT NULL DEFAULT 'identity',
 needs_crisis_guidance BOOLEAN NOT NULL DEFAULT FALSE, status VARCHAR(16) NOT NULL DEFAULT 'active',
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT ck_encouragement_visibility CHECK (visibility IN ('identity','anonymous','admins')),
 CONSTRAINT ck_encouragement_status CHECK (status IN ('active','removed'))
);
CREATE INDEX IF NOT EXISTS ix_encouragement_requests_family_id ON encouragement_requests(family_id);
CREATE INDEX IF NOT EXISTS ix_encouragement_requests_user_id ON encouragement_requests(user_id);
CREATE INDEX IF NOT EXISTS ix_encouragement_requests_status ON encouragement_requests(status);
CREATE TABLE IF NOT EXISTS encouragement_responses (
 id SERIAL PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES encouragement_requests(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, reaction VARCHAR(24) NOT NULL,
 comment VARCHAR(1000) NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_encouragement_response_user UNIQUE(request_id,user_id),
 CONSTRAINT ck_encouragement_response_reaction CHECK (reaction IN ('support','understand','keep_going','inspire'))
);
CREATE INDEX IF NOT EXISTS ix_encouragement_responses_request_id ON encouragement_responses(request_id);
CREATE TABLE IF NOT EXISTS encouragement_request_reports (
 id SERIAL PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES encouragement_requests(id) ON DELETE CASCADE,
 reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, reason VARCHAR(500) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'open', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_encouragement_report_user UNIQUE(request_id,reporter_id),
 CONSTRAINT ck_encouragement_report_status CHECK (status IN ('open','dismissed','removed'))
);
CREATE INDEX IF NOT EXISTS ix_encouragement_reports_status ON encouragement_request_reports(status);
COMMIT;
