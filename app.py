import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_login import current_user
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db, login_manager, socketio

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

database_url = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/rise_together"
)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "rise-together-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["PREFERRED_URL_SCHEME"] = "https"
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
}
app.config["UPLOAD_FOLDER"] = str(BASE_DIR / "uploads")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"
socketio.init_app(app, async_mode="threading")


def ensure_schema_compatibility():
    db.create_all()
    updates = [
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
    for statement in updates:
        db.session.execute(text(statement))
    db.session.commit()

with app.app_context():
    from models import User
    from routes.api import api_bp
    from routes.auth import auth_bp
    from routes.chat import chat_bp
    from routes.family import family_bp
    from routes.main import main_bp
    from routes.moderation import mod_bp

    ensure_schema_compatibility()
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(family_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(mod_bp)
    app.register_blueprint(api_bp)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def inject_navigation_counts():
    from helpers import is_hevc_upload
    from models import Message, Notification

    unread_notifications = 0
    unread_messages = 0
    if current_user.is_authenticated:
        unread_notifications = Notification.query.filter_by(
            user_id=current_user.id, seen=False
        ).count()
        unread_messages = Message.query.filter_by(
            recipient_id=current_user.id, delivered=False
        ).count()
    return {
        "unread_notifications": unread_notifications,
        "unread_messages": unread_messages,
        "is_hevc_upload": is_hevc_upload,
    }


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
