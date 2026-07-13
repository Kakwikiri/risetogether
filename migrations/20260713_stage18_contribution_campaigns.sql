BEGIN;

CREATE TABLE IF NOT EXISTS family_contribution_campaigns (
    id SERIAL PRIMARY KEY,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    upgrade_key VARCHAR(64) NOT NULL,
    points_required INTEGER NOT NULL CHECK (points_required > 0),
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    deadline TIMESTAMP,
    status VARCHAR(24) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'reached', 'cancelled', 'activated')),
    active_slot BOOLEAN,
    highest_milestone INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    CONSTRAINT uq_family_active_campaign UNIQUE (family_id, active_slot)
);
CREATE INDEX IF NOT EXISTS ix_family_contribution_campaigns_family_id ON family_contribution_campaigns(family_id);
CREATE INDEX IF NOT EXISTS ix_family_contribution_campaigns_status ON family_contribution_campaigns(status);

CREATE TABLE IF NOT EXISTS family_campaign_contributions (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES family_contribution_campaigns(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    contribution_key VARCHAR(120) NOT NULL,
    refunded BOOLEAN NOT NULL DEFAULT FALSE,
    refunded_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_campaign_contribution_key UNIQUE (contribution_key)
);
CREATE INDEX IF NOT EXISTS ix_family_campaign_contributions_campaign_id ON family_campaign_contributions(campaign_id);
CREATE INDEX IF NOT EXISTS ix_family_campaign_contributions_user_id ON family_campaign_contributions(user_id);
CREATE INDEX IF NOT EXISTS ix_family_campaign_contributions_created_at ON family_campaign_contributions(created_at);

COMMIT;
