import os
import re
import secrets
import smtplib
import urllib.parse
import urllib.request
from urllib.parse import urljoin
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import confirm_login, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import PasswordResetToken, Profile, SiteSetting, User
from ownership import is_platform_owner_username

auth_bp = Blueprint("auth", __name__)
SAFE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,80}$")

COUNTRIES = [
    "Uganda",
    "Kenya",
    "Tanzania",
    "Rwanda",
    "Burundi",
    "South Sudan",
    "Democratic Republic of the Congo",
    "Nigeria",
    "Ghana",
    "South Africa",
    "Ethiopia",
    "Somalia",
    "Sudan",
    "Egypt",
    "United States",
    "United Kingdom",
    "Canada",
    "United Arab Emirates",
    "Saudi Arabia",
    "India",
    "Pakistan",
    "Philippines",
    "Germany",
    "France",
    "Italy",
    "Spain",
    "Australia",
    "Other",
]


def render_signup(**context):
    return render_template("signup.html", countries=COUNTRIES, **context)


def setting_value(key, default=""):
    setting = SiteSetting.query.get(key)
    return setting.value if setting and setting.value else default


def config_value(setting_key, *env_names, default=""):
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return setting_value(setting_key, default)


def public_url_for(endpoint, **values):
    base_url = (
        os.getenv("PUBLIC_BASE_URL", "").strip()
        or os.getenv("SITE_URL", "").strip()
        or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    )
    if not base_url and os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip():
        base_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME').strip()}"
    if base_url:
        return urljoin(base_url.rstrip("/") + "/", url_for(endpoint, **values).lstrip("/"))
    return url_for(endpoint, _external=True, _scheme="https", **values)


def google_oauth_config():
    return {
        "client_id": config_value("google_client_id", "GOOGLE_CLIENT_ID", "GOOGLE_ID"),
        "client_secret": config_value(
            "google_client_secret",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_SECRET",
            "GOOGLE_CLIENT_SECRET_KEY",
        ),
    }


def should_make_admin(email):
    return False


def send_password_reset_email(user, reset_url):
    host = config_value("smtp_host", "SMTP_HOST")
    sender = config_value("smtp_from", "SMTP_FROM", "MAIL_FROM", "RESET_EMAIL_FROM")
    if not host or not sender:
        current_app.logger.info("Password reset link for %s: %s", user.email, reset_url)
        return False
    message = EmailMessage()
    message["Subject"] = "Reset your RiseTogether password"
    message["From"] = sender
    message["To"] = user.email
    message.set_content(
        f"Open this link to reset your RiseTogether password:\n\n{reset_url}\n\nThis link expires in 1 hour."
    )
    try:
        port = int(config_value("smtp_port", "SMTP_PORT", default="587"))
        username = config_value("smtp_username", "SMTP_USERNAME", "MAIL_USERNAME")
        password = config_value("smtp_password", "SMTP_PASSWORD", "MAIL_PASSWORD")
        use_ssl = config_value("smtp_use_ssl", "SMTP_USE_SSL", default="").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(host, port, timeout=15) as smtp:
            if not use_ssl:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
        return True
    except Exception:
        current_app.logger.exception("Password reset email failed for %s", user.email)
        return False


def send_password_reset_code_email(user, code):
    host = config_value("smtp_host", "SMTP_HOST")
    sender = config_value("smtp_from", "SMTP_FROM", "MAIL_FROM", "RESET_EMAIL_FROM")
    if not host or not sender:
        current_app.logger.warning("Password reset SMTP is not configured for %s", user.email)
        return False
    message = EmailMessage()
    message["Subject"] = "Your RiseTogether password reset code"
    message["From"] = sender
    message["To"] = user.email
    message.set_content(
        "Use this RiseTogether password reset code:\n\n"
        f"{code}\n\n"
        "This code expires in 3 hours. If you did not request it, you can ignore this email."
    )
    try:
        port = int(config_value("smtp_port", "SMTP_PORT", default="587"))
        username = config_value("smtp_username", "SMTP_USERNAME", "MAIL_USERNAME")
        password = config_value("smtp_password", "SMTP_PASSWORD", "MAIL_PASSWORD")
        use_ssl = config_value("smtp_use_ssl", "SMTP_USE_SSL", default="").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(host, port, timeout=15) as smtp:
            if not use_ssl:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
        return True
    except Exception:
        current_app.logger.exception("Password reset code email failed for %s", user.email)
        return False


def normalize_reset_email():
    return request.form.get("email", session.get("reset_email", "")).strip().lower()


def latest_active_reset(user):
    if not user:
        return None
    return (
        PasswordResetToken.query.filter_by(user_id=user.id, used=False)
        .filter(PasswordResetToken.code_hash != "")
        .order_by(PasswordResetToken.created_at.desc())
        .first()
    )


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    referral_token = (request.values.get("referral_token") or request.args.get("ref") or session.get("referral_token") or "").strip()
    if referral_token:
        session["referral_token"] = referral_token
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        country = request.form.get("country", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not email or not password or not country:
            flash("Please complete all fields.", "warning")
            return render_signup()
        if not SAFE_USERNAME_RE.fullmatch(username):
            flash("Usernames may use only letters, numbers, and underscores. Badge-like symbols are not allowed.", "warning")
            return render_signup()
        if is_platform_owner_username(username) and not should_make_admin(email):
            flash("That username is reserved for the RiseTogether platform owner.", "danger")
            return render_signup()
        if country not in COUNTRIES:
            country = "Other"
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "warning")
            return render_signup()
        existing = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        if existing:
            flash("Username or email already exists.", "danger")
            return render_signup()
        user = User(username=username, email=email, country=country)
        user.set_password(password)
        if should_make_admin(email) and is_platform_owner_username(username):
            user.is_admin = True
            user.admin_role = "super_admin"
        db.session.add(user)
        db.session.commit()
        profile = Profile(user_id=user.id, display_name=username)
        db.session.add(profile)
        from referrals import register_referral_signup
        register_referral_signup(user, referral_token)
        db.session.commit()
        session.pop("referral_token", None)
        login_user(user)
        flash("Welcome to RiseTogether! Your safe community starts here.", "success")
        return redirect(url_for("main.home"))
    return render_signup(referral_token=referral_token)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        identifier = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        user = User.query.filter(
            (User.email == identifier.lower()) | (User.username.ilike(identifier))
        ).first()
        if user and user.check_password(password):
            if user.ban_until and user.ban_until <= datetime.utcnow():
                user.is_banned = False
                user.ban_until = None
                db.session.commit()
            if user.is_banned:
                flash("This account has been banned. Please contact an admin.", "danger")
                return render_template("login.html")
            remember = request.form.get("remember") == "1"
            # A restored login page may still carry an older remembered account.
            # Clear that identity before applying the credentials just verified.
            session.clear()
            logout_user()
            login_user(user, remember=remember)
            flash("Logged in successfully.", "success")
            return redirect(url_for("main.home"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@auth_bp.route("/reauthenticate", methods=["GET", "POST"])
@login_required
def reauthenticate():
    next_url = request.args.get("next") or url_for("main.home")
    if request.method == "POST":
        password = request.form.get("password", "")
        if current_user.check_password(password):
            confirm_login()
            flash("Session confirmed.", "success")
            return redirect(next_url)
        flash("Password confirmation failed.", "danger")
    return render_template("reauthenticate.html", next_url=next_url)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        email = normalize_reset_email()
        user = User.query.filter_by(email=email).first()
        if user and not user.is_banned:
            now = datetime.utcnow()
            active_reset = latest_active_reset(user)
            cooldown_until = None
            if active_reset and active_reset.last_sent_at:
                cooldown_until = active_reset.last_sent_at + timedelta(seconds=60)
            if not cooldown_until or cooldown_until <= now:
                PasswordResetToken.query.filter_by(user_id=user.id, used=False).update(
                    {"used": True}
                )
                code = f"{secrets.randbelow(1000000):06d}"
                reset = PasswordResetToken(
                    user_id=user.id,
                    token=secrets.token_urlsafe(32),
                    code_hash=generate_password_hash(code),
                    expires_at=now + timedelta(hours=3),
                    attempts=0,
                    last_sent_at=now,
                )
                db.session.add(reset)
                db.session.commit()
                send_password_reset_code_email(user, code)
            else:
                db.session.commit()
                current_app.logger.info("Password reset resend cooldown active for %s", user.email)
        session["reset_email"] = email
        flash(
            "If an account exists for that email, a reset code has been sent.",
            "info",
        )
        return redirect(url_for("auth.verify_reset_code"))
    return render_template("forgot_password.html")


@auth_bp.route("/forgot-password/verify", methods=["GET", "POST"])
def verify_reset_code():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    email = normalize_reset_email()
    if request.method == "POST":
        action = request.form.get("action", "verify")
        user = User.query.filter_by(email=email).first()
        if action == "resend":
            if user and not user.is_banned:
                now = datetime.utcnow()
                active_reset = latest_active_reset(user)
                last_sent_at = active_reset.last_sent_at if active_reset else None
                if last_sent_at and last_sent_at + timedelta(seconds=60) > now:
                    flash("Please wait before requesting another code.", "warning")
                    return render_template("verify_reset_code.html", email=email)
                PasswordResetToken.query.filter_by(user_id=user.id, used=False).update(
                    {"used": True}
                )
                code = f"{secrets.randbelow(1000000):06d}"
                reset = PasswordResetToken(
                    user_id=user.id,
                    token=secrets.token_urlsafe(32),
                    code_hash=generate_password_hash(code),
                    expires_at=now + timedelta(hours=3),
                    attempts=0,
                    last_sent_at=now,
                )
                db.session.add(reset)
                db.session.commit()
                send_password_reset_code_email(user, code)
            session["reset_email"] = email
            flash("If an account exists for that email, a reset code has been sent.", "info")
            return render_template("verify_reset_code.html", email=email)

        code = request.form.get("code", "").strip().replace(" ", "")
        reset = latest_active_reset(user)
        now = datetime.utcnow()
        if not reset or reset.expires_at < now or reset.attempts >= 5:
            if reset:
                reset.used = True
                db.session.commit()
            flash("The reset code is incorrect or expired.", "danger")
            return render_template("verify_reset_code.html", email=email)
        if not code.isdigit() or len(code) != 6 or not check_password_hash(reset.code_hash, code):
            reset.attempts += 1
            if reset.attempts >= 5:
                reset.used = True
            db.session.commit()
            flash("The reset code is incorrect or expired.", "danger")
            return render_template("verify_reset_code.html", email=email)
        session["password_reset_id"] = reset.id
        session["reset_email"] = email
        flash("Code verified. Choose a new password.", "success")
        return redirect(url_for("auth.reset_password"))
    return render_template("verify_reset_code.html", email=email)


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    reset_id = session.get("password_reset_id")
    reset = PasswordResetToken.query.filter_by(id=reset_id, used=False).first()
    if not reset or reset.expires_at < datetime.utcnow() or reset.attempts >= 5:
        session.pop("password_reset_id", None)
        flash("Please request a new reset code.", "warning")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "warning")
            return render_template("reset_password.html")
        if password != confirm_password:
            flash("Passwords do not match.", "warning")
            return render_template("reset_password.html")
        reset.user.set_password(password)
        reset.used = True
        db.session.commit()
        session.pop("password_reset_id", None)
        session.pop("reset_email", None)
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("reset_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_token_legacy(token):
    flash("Password resets now use an email verification code. Please request a new code.", "info")
    return redirect(url_for("auth.forgot_password"))


@auth_bp.route("/login/google")
def google_login():
    google_config = google_oauth_config()
    client_id = google_config["client_id"]
    client_secret = google_config["client_secret"]
    if not client_id or not client_secret:
        missing = []
        if not client_id:
            missing.append("GOOGLE_CLIENT_ID")
        if not client_secret:
            missing.append("GOOGLE_CLIENT_SECRET")
        current_app.logger.warning("Google login missing configuration: %s", ", ".join(missing))
        flash(f"Google login is missing: {', '.join(missing)}.", "warning")
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(16)
    session["google_oauth_state"] = state
    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": public_url_for("auth.google_callback"),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@auth_bp.route("/login/google/callback")
def google_callback():
    if request.args.get("error"):
        flash("Google login was cancelled or denied.", "warning")
        return redirect(url_for("auth.login"))
    if request.args.get("state") != session.pop("google_oauth_state", None):
        flash("Google login could not be verified.", "danger")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    google_config = google_oauth_config()
    client_id = google_config["client_id"]
    client_secret = google_config["client_secret"]
    if not code or not client_id or not client_secret:
        flash("Google login is not configured correctly.", "warning")
        return redirect(url_for("auth.login"))
    token_data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": public_url_for("auth.google_callback"),
            "grant_type": "authorization_code",
        }
    ).encode()
    try:
        token_request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_response = urllib.request.urlopen(token_request, timeout=10)
        import json

        access_token = json.loads(token_response.read())["access_token"]
        user_request = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_response = urllib.request.urlopen(user_request, timeout=10)
        info = json.loads(user_response.read())
    except Exception:
        current_app.logger.exception("Google login failed")
        flash("Google login failed. Please try email/password.", "danger")
        return redirect(url_for("auth.login"))
    email = info.get("email", "").lower()
    if not email:
        flash("Google did not provide an email address.", "danger")
        return redirect(url_for("auth.login"))
    if info.get("verified_email") is False:
        flash("Please verify your Google email before using Google login.", "warning")
        return redirect(url_for("auth.login"))
    user = User.query.filter_by(email=email).first()
    created_user = user is None
    if not user:
        username = re.sub(r"[^A-Za-z0-9_]", "_", email.split("@")[0])[:80].strip("_") or "member"
        base_username = username
        counter = 1
        while User.query.filter_by(username=username).first():
            counter += 1
            username = f"{base_username}{counter}"
        if is_platform_owner_username(username) and not should_make_admin(email):
            username = f"{username}_member"
        user = User(username=username, email=email, country="Other")
        user.set_password(secrets.token_urlsafe(24))
        if should_make_admin(email) and is_platform_owner_username(username):
            user.is_admin = True
            user.admin_role = "super_admin"
        db.session.add(user)
        db.session.commit()
        db.session.add(Profile(user_id=user.id, display_name=info.get("name") or username))
        from referrals import register_referral_signup
        register_referral_signup(user, session.get("referral_token", ""))
        db.session.commit()
    elif not user.profile:
        db.session.add(Profile(user_id=user.id, display_name=info.get("name") or user.username))
        db.session.commit()
    if user.is_banned:
        flash("This account has been banned. Please contact an admin.", "danger")
        return redirect(url_for("auth.login"))
    login_user(user)
    if created_user:
        session.pop("referral_token", None)
    flash("Logged in with Google.", "success")
    return redirect(url_for("main.home"))


@auth_bp.route("/logout")
@login_required
def logout():
    session.clear()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
