import os
import secrets
import smtplib
import urllib.parse
import urllib.request
from urllib.parse import urljoin
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import confirm_login, current_user, login_required, login_user, logout_user

from extensions import db
from models import PasswordResetToken, Profile, SiteSetting, User

auth_bp = Blueprint("auth", __name__)

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


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        country = request.form.get("country", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not email or not password or not country:
            flash("Please complete all fields.", "warning")
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
        if should_make_admin(email):
            user.is_admin = True
            user.admin_role = "super_admin" if User.query.filter_by(is_admin=True).count() == 0 else "admin"
        db.session.add(user)
        db.session.commit()
        profile = Profile(user_id=user.id, display_name=username)
        db.session.add(profile)
        db.session.commit()
        login_user(user)
        flash("Welcome to RiseTogether! Your safe community starts here.", "success")
        return redirect(url_for("main.home"))
    return render_signup()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
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
    reset_url = None
    if request.method == "POST":
        identifier = request.form.get("email", "").strip()
        reset_phrase = request.form.get("reset_phrase", "").strip()
        user = User.query.filter(
            (User.email == identifier.lower()) | (User.username.ilike(identifier))
        ).first()
        if user and not user.is_banned:
            if user.reset_phrase_hash and not user.check_reset_phrase(reset_phrase):
                flash("Reset word is incorrect.", "danger")
                return render_template("forgot_password.html", reset_url=None)
            token = secrets.token_urlsafe(32)
            reset = PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
            db.session.add(reset)
            db.session.commit()
            reset_url = public_url_for("auth.reset_password", token=token)
            sent = send_password_reset_email(user, reset_url)
            if sent:
                reset_url = None
        flash(
            "If that email exists, a password reset link has been prepared.",
            "info",
        )
    return render_template("forgot_password.html", reset_url=reset_url)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    reset = PasswordResetToken.query.filter_by(token=token, used=False).first_or_404()
    if reset.expires_at < datetime.utcnow():
        flash("This reset link has expired. Please request a new one.", "warning")
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
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("reset_password.html")


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
    if not user:
        username = email.split("@")[0]
        base_username = username
        counter = 1
        while User.query.filter_by(username=username).first():
            counter += 1
            username = f"{base_username}{counter}"
        user = User(username=username, email=email, country="Other")
        user.set_password(secrets.token_urlsafe(24))
        if should_make_admin(email):
            user.is_admin = True
            user.admin_role = "super_admin" if User.query.filter_by(is_admin=True).count() == 0 else "admin"
        db.session.add(user)
        db.session.commit()
        db.session.add(Profile(user_id=user.id, display_name=info.get("name") or username))
        db.session.commit()
    elif not user.profile:
        db.session.add(Profile(user_id=user.id, display_name=info.get("name") or user.username))
        db.session.commit()
    if user.is_banned:
        flash("This account has been banned. Please contact an admin.", "danger")
        return redirect(url_for("auth.login"))
    login_user(user)
    flash("Logged in with Google.", "success")
    return redirect(url_for("main.home"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
