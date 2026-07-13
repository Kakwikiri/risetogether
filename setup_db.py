from app import app
from extensions import db
from sqlalchemy import text

with app.app_context():
    db.create_all()
    schema_updates = [
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS audience VARCHAR(20) NOT NULL DEFAULT 'public'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP",
        "ALTER TABLE comment_reactions ADD COLUMN IF NOT EXISTS type VARCHAR(32) NOT NULL DEFAULT 'support'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS original_post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_url VARCHAR(255) DEFAULT ''",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_type VARCHAR(32) DEFAULT 'text'",
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS action_url VARCHAR(255) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_until TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS warning_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_phrase_hash VARCHAR(256) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS country VARCHAR(80) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_hidden_from_directory BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS auto_share_completed_challenges BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS post_type VARCHAR(32) NOT NULL DEFAULT 'standard'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS achievement_type VARCHAR(48) DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS challenge_completion_id INTEGER REFERENCES challenge_completions(id) ON DELETE CASCADE",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS encouraging_message VARCHAR(240) DEFAULT ''",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS reward_tier VARCHAR(32) NOT NULL DEFAULT 'easy'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS completion_frequency VARCHAR(24) NOT NULL DEFAULT 'one_time'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS custom_frequency_days INTEGER",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS evidence_requirement VARCHAR(24) NOT NULL DEFAULT 'none'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS participant_scope VARCHAR(32) NOT NULL DEFAULT 'all_members'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS max_participants INTEGER",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS visibility VARCHAR(24) NOT NULL DEFAULT 'family'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS requires_admin_approval BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS allow_achievement_sharing BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS mandatory_all_members BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE challenge_completions ADD COLUMN IF NOT EXISTS period_key VARCHAR(32) NOT NULL DEFAULT 'once'",
        "ALTER TABLE challenge_completions ADD COLUMN IF NOT EXISTS points_awarded INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'open'",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS privacy VARCHAR(20) NOT NULL DEFAULT 'public'",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES messages(id) ON DELETE SET NULL",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS view_once BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS pinned_until TIMESTAMP",
        "ALTER TABLE post_shares ADD COLUMN IF NOT EXISTS recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    ]
    for statement in schema_updates:
        db.session.execute(text(statement))
    db.session.execute(
        text(
            """
            DELETE FROM reactions r
            USING reactions newer
            WHERE r.id < newer.id
              AND r.post_id = newer.post_id
              AND r.user_id = newer.user_id
            """
        )
    )
    db.session.execute(
        text(
            """
            ALTER TABLE reactions DROP CONSTRAINT IF EXISTS uq_reaction_user_type
            """
        )
    )
    db.session.execute(text("DROP INDEX IF EXISTS uq_reaction_user_type"))
    db.session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_reaction_post_user
            ON reactions (post_id, user_id)
            """
        )
    )
    db.session.commit()
    print("Database tables created successfully.")
