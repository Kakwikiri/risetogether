import os
from html import escape
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, Response, request
from flask_login import current_user
from sqlalchemy import text
from werkzeug.exceptions import RequestEntityTooLarge
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
app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
app.config["IMAGE_UPLOAD_LIMIT"] = int(os.getenv("IMAGE_UPLOAD_LIMIT_MB", "5")) * 1024 * 1024
app.config["VIDEO_UPLOAD_LIMIT"] = int(os.getenv("VIDEO_UPLOAD_LIMIT_MB", "25")) * 1024 * 1024
app.config["FILE_UPLOAD_LIMIT"] = int(os.getenv("FILE_UPLOAD_LIMIT_MB", "10")) * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = max(
    app.config["IMAGE_UPLOAD_LIMIT"],
    app.config["VIDEO_UPLOAD_LIMIT"],
    app.config["FILE_UPLOAD_LIMIT"],
) + 1024 * 1024
app.config["REALTIME_MEDIA_ENABLED"] = (
    os.getenv("REALTIME_MEDIA_ENABLED", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"
socketio.init_app(
    app,
    async_mode="threading",
    ping_interval=25,
    ping_timeout=60,
    logger=os.getenv("SOCKETIO_LOGGER", "").lower() == "true",
    engineio_logger=os.getenv("ENGINEIO_LOGGER", "").lower() == "true",
)


def ensure_schema_compatibility():
    db.create_all()
    from models import MediaAsset, MessageDeletion
    from helpers import get_media_type, mimetype_for_filename

    MediaAsset.__table__.create(db.engine, checkfirst=True)
    MessageDeletion.__table__.create(db.engine, checkfirst=True)
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
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    if upload_folder.exists():
        for path in upload_folder.iterdir():
            if not path.is_file():
                continue
            filename = path.name
            if MediaAsset.query.filter_by(filename=filename).first():
                continue
            data = path.read_bytes()
            db.session.add(
                MediaAsset(
                    filename=filename,
                    content_type=mimetype_for_filename(filename),
                    media_type=get_media_type(filename),
                    data=data,
                    size=len(data),
                )
            )
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
        "realtime_media_enabled": app.config["REALTIME_MEDIA_ENABLED"],
    }


def find_user_by_identifier(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    return User.query.filter(
        (User.email == identifier.lower()) | (User.username.ilike(identifier))
    ).first()


def validate_cli_password(password):
    if len(password or "") < 8:
        raise click.ClickException("Password must be at least 8 characters.")


def admin_setup_token_is_valid(token):
    expected = os.getenv("ADMIN_SETUP_TOKEN", "").strip()
    return bool(expected) and token and token == expected


@app.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(error):
    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return f"Upload is too large. Please choose a file under {limit_mb} MB.", 413


def admin_setup_form(token="", message="", status=200):
    safe_message = escape(message) if message else ""
    safe_token = escape(token or "")
    html = f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>RiseTogether Admin Setup</title>
  </head>
  <body>
    <h1>RiseTogether Admin Setup</h1>
    {'<p><strong>' + safe_message + '</strong></p>' if safe_message else ''}
    <form method="post">
      <input type="hidden" name="token" value="{safe_token}" />
      <label>Action
        <select name="action">
          <option value="create">Create admin</option>
          <option value="promote">Promote existing user</option>
          <option value="reset">Reset admin password</option>
        </select>
      </label>
      <p><label>Username <input name="username" autocomplete="username" /></label></p>
      <p><label>Email <input name="email" type="email" autocomplete="email" /></label></p>
      <p><label>Country <input name="country" value="Other" /></label></p>
      <p><label>Password <input name="password" type="password" autocomplete="new-password" /></label></p>
      <p><label>Confirm password <input name="confirm_password" type="password" autocomplete="new-password" /></label></p>
      <button type="submit">Apply</button>
    </form>
  </body>
</html>
"""
    return Response(html, status=status, mimetype="text/html")


@app.route("/setup/admin", methods=["GET", "POST"])
def admin_setup_web():
    token = request.values.get("token", "").strip()
    if not os.getenv("ADMIN_SETUP_TOKEN", "").strip():
        return Response("Admin setup is disabled.", status=404, mimetype="text/plain")
    if not admin_setup_token_is_valid(token):
        return admin_setup_form(token="", message="Invalid or missing setup token.", status=403)
    if request.method == "GET":
        return admin_setup_form(token=token)

    from models import Profile

    action = request.form.get("action", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    country = request.form.get("country", "Other").strip() or "Other"
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    try:
        if action == "create":
            if not username or not email:
                raise ValueError("Username and email are required.")
            if password != confirm_password:
                raise ValueError("Passwords do not match.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")
            duplicate = User.query.filter(
                (User.username == username) | (User.email == email)
            ).first()
            if duplicate:
                raise ValueError("A user with that username or email already exists.")
            user = User(username=username, email=email, country=country)
            user.set_password(password)
            user.is_admin = True
            user.is_banned = False
            user.ban_until = None
            user.is_verified = True
            db.session.add(user)
            db.session.flush()
            db.session.add(Profile(user_id=user.id, display_name=username))
            db.session.commit()
            return admin_setup_form(token=token, message=f"Admin created for {username}.")

        identifier = email or username
        user = find_user_by_identifier(identifier)
        if not user:
            raise ValueError("No user found with that username or email.")
        if action == "promote":
            user.is_admin = True
            user.is_banned = False
            user.ban_until = None
            db.session.commit()
            return admin_setup_form(token=token, message=f"Promoted {user.username} to admin.")
        if action == "reset":
            if not user.is_admin:
                raise ValueError("That user is not currently an admin.")
            if password != confirm_password:
                raise ValueError("Passwords do not match.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")
            user.set_password(password)
            user.is_banned = False
            user.ban_until = None
            db.session.commit()
            return admin_setup_form(token=token, message=f"Password reset for {user.username}.")
        raise ValueError("Choose a valid action.")
    except Exception as exc:
        db.session.rollback()
        return admin_setup_form(token=token, message=str(exc), status=400)


@app.cli.command("create-admin")
def create_admin_command():
    """Create a production admin in the currently configured database."""
    from models import Profile

    username = click.prompt("Username").strip()
    email = click.prompt("Email").strip().lower()
    country = click.prompt("Country", default="Other").strip() or "Other"
    password = click.prompt(
        "Password",
        hide_input=True,
        confirmation_prompt=True,
    )
    validate_cli_password(password)

    if not username:
        raise click.ClickException("Username is required.")
    if not email:
        raise click.ClickException("Email is required.")

    try:
        duplicate = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        if duplicate:
            raise click.ClickException("A user with that username or email already exists.")

        user = User(username=username, email=email, country=country)
        user.set_password(password)
        user.is_admin = True
        user.is_banned = False
        user.ban_until = None
        user.is_verified = True
        db.session.add(user)
        db.session.flush()
        db.session.add(Profile(user_id=user.id, display_name=username))
        db.session.commit()
    except click.ClickException:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise click.ClickException(f"Admin could not be created: {exc}") from exc

    click.echo(f"Admin created: {username} <{email}>")


@app.cli.command("promote-admin")
def promote_admin_command():
    """Promote an existing user to admin in the current database."""
    identifier = click.prompt("Existing username or email").strip()
    user = find_user_by_identifier(identifier)
    if not user:
        raise click.ClickException("No user found with that username or email.")
    if user.is_admin:
        click.echo(f"{user.username} is already an admin.")
        return

    try:
        user.is_admin = True
        user.is_banned = False
        user.ban_until = None
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise click.ClickException(f"User could not be promoted: {exc}") from exc

    click.echo(f"Promoted to admin: {user.username} <{user.email}>")


@app.cli.command("reset-admin-password")
def reset_admin_password_command():
    """Reset an existing admin password using the app password hash method."""
    identifier = click.prompt("Admin username or email").strip()
    user = find_user_by_identifier(identifier)
    if not user:
        raise click.ClickException("No user found with that username or email.")
    if not user.is_admin:
        raise click.ClickException("That user is not currently an admin.")

    password = click.prompt(
        "New password",
        hide_input=True,
        confirmation_prompt=True,
    )
    validate_cli_password(password)

    try:
        user.set_password(password)
        user.is_banned = False
        user.ban_until = None
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise click.ClickException(f"Admin password could not be reset: {exc}") from exc

    click.echo(f"Password reset for admin: {user.username} <{user.email}>")


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
