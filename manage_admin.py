import getpass
import sys

from app import app
from extensions import db
from models import Profile, User


def main():
    if len(sys.argv) >= 3:
        email = sys.argv[1].strip().lower()
        username = sys.argv[2].strip()
    else:
        email = "admin@risetogether.local"
        username = "Kakwikiri"
        print(f"No details provided. Creating/updating default admin: {username} ({email})")
    password = getpass.getpass("Admin password: ")
    reset_phrase = getpass.getpass("Reset word: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1
    if len(reset_phrase) < 8:
        print("Reset word must be at least 8 characters.")
        return 1
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            db.session.add(Profile(user_id=user.id, display_name=username))
        user.username = user.username or username
        user.set_password(password)
        user.set_reset_phrase(reset_phrase)
        user.is_admin = True
        user.is_banned = False
        user.is_verified = True
        user.is_hidden_from_directory = True
        db.session.commit()
    print(f"Admin ready: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
