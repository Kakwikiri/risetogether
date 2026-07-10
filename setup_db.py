from app import app
from extensions import db
from sqlalchemy import text

with app.app_context():
    db.create_all()
    schema_updates = [
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS audience VARCHAR(20) NOT NULL DEFAULT 'public'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE",
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
            USING reactions duplicate
            WHERE r.id > duplicate.id
              AND r.post_id = duplicate.post_id
              AND r.user_id = duplicate.user_id
              AND r.type = duplicate.type
            """
        )
    )
    db.session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_reaction_user_type
            ON reactions (post_id, user_id, type)
            """
        )
    )
    db.session.commit()
    print("Database tables created successfully.")
