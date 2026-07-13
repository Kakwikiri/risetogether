BEGIN;

CREATE TABLE IF NOT EXISTS point_transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    reason VARCHAR(240) NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    source_id INTEGER,
    unique_reward_key VARCHAR(180) NOT NULL UNIQUE,
    reversed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    awarded_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_point_transaction_single_recipient CHECK (
        (user_id IS NOT NULL AND family_id IS NULL) OR
        (user_id IS NULL AND family_id IS NOT NULL)
    ),
    CONSTRAINT ck_point_transaction_positive_amount CHECK (amount > 0)
);

CREATE INDEX IF NOT EXISTS ix_point_transactions_user_id ON point_transactions(user_id);
CREATE INDEX IF NOT EXISTS ix_point_transactions_family_id ON point_transactions(family_id);
CREATE INDEX IF NOT EXISTS ix_point_transactions_source_type ON point_transactions(source_type);
CREATE INDEX IF NOT EXISTS ix_point_transactions_created_at ON point_transactions(created_at);

INSERT INTO point_transactions
    (user_id, family_id, amount, reason, source_type, source_id,
     unique_reward_key, reversed, created_at, awarded_by_id)
SELECT cc.user_id, NULL, cc.points_awarded,
       LEFT('Completed ' || fc.title, 240), 'challenge_completion', cc.id,
       'challenge_completion:' || cc.id || ':' || 'personal', FALSE, cc.completed_at, NULL
FROM challenge_completions cc
JOIN family_challenges fc ON fc.id = cc.challenge_id
WHERE cc.verification_status = 'completed' AND cc.points_awarded > 0
ON CONFLICT (unique_reward_key) DO NOTHING;

INSERT INTO point_transactions
    (user_id, family_id, amount, reason, source_type, source_id,
     unique_reward_key, reversed, created_at, awarded_by_id)
SELECT NULL, fc.family_id, cc.points_awarded,
       LEFT('Completed ' || fc.title, 240), 'challenge_completion', cc.id,
       'challenge_completion:' || cc.id || ':' || 'family', FALSE, cc.completed_at, NULL
FROM challenge_completions cc
JOIN family_challenges fc ON fc.id = cc.challenge_id
WHERE cc.verification_status = 'completed' AND cc.points_awarded > 0
ON CONFLICT (unique_reward_key) DO NOTHING;

COMMIT;
