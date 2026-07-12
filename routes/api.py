import os
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory
from flask_login import current_user, login_required
from werkzeug.utils import safe_join

from extensions import db
from models import Family, MediaAsset, Message, PushSubscription

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    is_view_once = Message.query.filter_by(media_url=filename, view_once=True).first()
    upload_path = safe_join(current_app.config["UPLOAD_FOLDER"], filename)
    if upload_path and os.path.exists(upload_path):
        response = send_from_directory(
            current_app.config["UPLOAD_FOLDER"],
            filename,
            conditional=True,
            max_age=0 if is_view_once else 60 * 60 * 24 * 30,
        )
    else:
        asset = MediaAsset.query.filter_by(filename=filename).first_or_404()
        response = Response(asset.data, mimetype=asset.content_type)
        response.headers["Content-Length"] = str(asset.size)
    if is_view_once:
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
    else:
        response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return response


@api_bp.route("/users/current")
@login_required
def current_user_info():
    profile = current_user.profile
    return jsonify(
        {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "display_name": profile.display_name,
            "avatar": profile.avatar,
            "bio": profile.bio,
            "privacy_posts": profile.privacy_posts,
            "notifications_enabled": profile.notifications_enabled,
            "notification_previews_enabled": profile.notification_previews_enabled,
        }
    )


@api_bp.route("/push/public-key")
@login_required
def push_public_key():
    return jsonify({"public_key": current_app.config.get("VAPID_PUBLIC_KEY", "")})


@api_bp.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Invalid push subscription."}), 400
    subscription = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if not subscription:
        subscription = PushSubscription(endpoint=endpoint)
    subscription.user_id = current_user.id
    subscription.p256dh = p256dh
    subscription.auth = auth
    subscription.active = True
    subscription.last_used_at = datetime.utcnow()
    current_user.profile.notifications_enabled = True
    db.session.add(subscription)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    query = PushSubscription.query.filter_by(user_id=current_user.id, active=True)
    if endpoint:
        query = query.filter_by(endpoint=endpoint)
    query.update({"active": False})
    current_user.profile.notifications_enabled = False
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/families/<int:family_id>/members")
@login_required
def family_members(family_id):
    family = Family.query.get_or_404(family_id)
    members = [
        {
            "user_id": member.user_id,
            "username": member.user.username,
            "role": member.role,
            "joined_at": member.joined_at.isoformat(),
        }
        for member in family.members
    ]
    return jsonify({"family": family.name, "members": members})
