import hmac
import secrets

from flask import current_app, flash, jsonify, redirect, request, session, url_for


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_SESSION_KEY = "_csrf_token"


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_protect():
    if request.method in SAFE_METHODS or not current_app.config.get("CSRF_ENABLED", True):
        return None
    expected = session.get(CSRF_SESSION_KEY, "")
    supplied = request.form.get("csrf_token", "") or request.headers.get(
        "X-CSRF-Token", ""
    )
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return None
    current_app.logger.warning(
        "csrf_validation_failed method=%s path=%s remote_addr=%s",
        request.method,
        request.path,
        request.remote_addr,
    )
    message = "Your form expired or could not be verified. Refresh the page and try again."
    if request.endpoint == "auth.login":
        session.pop(CSRF_SESSION_KEY, None)
        flash("The login page had expired. It has been refreshed safely; please sign in again.", "info")
        return redirect(url_for("auth.login"), code=303)
    if request.path.startswith("/api/") or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": False, "error": message}), 400
    return message, 400


def init_csrf(app):
    app.before_request(csrf_protect)
    app.jinja_env.globals["csrf_token"] = csrf_token
