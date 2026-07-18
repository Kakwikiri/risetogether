import os
import base64
from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import safe_join

from extensions import db
from helpers import user_avatar_url
from models import Family, FamilyMember, MediaAsset, Message, MessageAttachment, Notification, Profile, PushSubscription, User
from notifications_service import important_unread_count, unread_private_message_count

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    is_view_once = Message.query.filter_by(media_url=filename, view_once=True).first()
    if not is_view_once:
        is_view_once = (
            Message.query.join(MessageAttachment)
            .filter(MessageAttachment.media_url == filename, Message.view_once == True)
            .first()
        )
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
    if request.args.get("download") == "1" and not is_view_once:
        response.headers.set(
            "Content-Disposition",
            "attachment",
            filename=f"RiseTogether-{os.path.basename(filename)}",
        )
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
            "avatar": user_avatar_url(current_user),
            "bio": profile.bio,
            "privacy_posts": profile.privacy_posts,
            "notifications_enabled": profile.notifications_enabled,
            "notification_previews_enabled": profile.notification_previews_enabled,
            "auto_share_completed_challenges": profile.auto_share_completed_challenges,
        }
    )


@api_bp.route("/users/search")
@login_required
def search_users():
    query = request.args.get("q", "").strip()
    family_id = request.args.get("family_id", type=int)
    target = request.args.get("target", "").strip()
    if len(query) < 1:
        return jsonify({"results": []})

    search = f"%{query}%"
    users_query = User.query.filter(
        User.id != current_user.id,
        User.is_hidden_from_directory == False,
        User.is_banned == False,
        or_(User.username.ilike(search), User.email.ilike(search)),
    ).order_by(User.username.asc())
    if family_id:
        member_user_ids = db.session.query(FamilyMember.user_id).filter_by(family_id=family_id)
        users_query = users_query.filter(~User.id.in_(member_user_ids))

    users = users_query.limit(20).all()
    try:
        profile_matches = (
            User.query.join(Profile)
            .filter(
                User.id != current_user.id,
                User.is_hidden_from_directory == False,
                User.is_banned == False,
                Profile.display_name.ilike(search),
            )
            .order_by(User.username.asc())
            .limit(20)
            .all()
        )
    except Exception as error:
        current_app.logger.warning("User display-name search failed: %s", error)
        profile_matches = []
    if family_id:
        member_user_ids = db.session.query(FamilyMember.user_id).filter_by(family_id=family_id)
        member_ids = {row[0] for row in member_user_ids.all()}
        profile_matches = [user for user in profile_matches if user.id not in member_ids]

    results = []
    seen_user_ids = set()
    for user in users + profile_matches:
        if user.id in seen_user_ids:
            continue
        seen_user_ids.add(user.id)
        display_name = user.profile.display_name if user.profile else user.username
        result_url = (
            url_for("chat.direct_chat", user_id=user.id)
            if target == "chat"
            else url_for("main.profile", username=user.username)
        )
        results.append(
            {
                "id": user.id,
                "username": user.username,
                "display_name": display_name,
                "label": f"{display_name} @{user.username}",
                "url": result_url,
            }
        )
        if len(results) >= 10:
            break
    return jsonify({"results": results})


@api_bp.route("/families/search")
@login_required
def search_families():
    query = request.args.get("q", "").strip()
    if len(query) < 1:
        return jsonify({"results": []})

    search = f"%{query}%"
    families = (
        Family.query.filter(
            or_(
                Family.name.ilike(search),
                Family.description.ilike(search),
                Family.goal_title.ilike(search),
                Family.category.ilike(search),
            )
        )
        .order_by(Family.name.asc())
        .limit(10)
        .all()
    )
    return jsonify(
        {
            "results": [
                {
                    "id": family.id,
                    "name": family.name,
                    "label": family.name,
                    "meta": f"{family.category.replace('_', ' ').title()} · {family.privacy.replace('_', ' ').title()}",
                    "url": url_for("family.family_detail", family_id=family.id),
                }
                for family in families
            ]
        }
    )


@api_bp.route("/push/public-key")
@login_required
def push_public_key():
    public_key = current_app.config.get("VAPID_PUBLIC_KEY", "").strip().strip('"\'')
    try:
        padding = "=" * ((4 - len(public_key) % 4) % 4)
        decoded = base64.urlsafe_b64decode(public_key + padding)
        valid = len(decoded) == 65 and decoded[0] == 4
    except (ValueError, TypeError):
        valid = False
    private_key = current_app.config.get("VAPID_PRIVATE_KEY", "").strip()
    if private_key.startswith("-----BEGIN"):
        private_valid = "PRIVATE KEY-----" in private_key
    else:
        try:
            private_padding = "=" * ((4 - len(private_key) % 4) % 4)
            private_valid = len(base64.urlsafe_b64decode(private_key + private_padding)) == 32
        except (ValueError, TypeError):
            private_valid = False
    subject = current_app.config.get("VAPID_SUBJECT", "").strip()
    if not valid or not private_valid or not subject.startswith(("mailto:", "https://")):
        invalid_fields = []
        if not valid:
            invalid_fields.append("VAPID_PUBLIC_KEY")
        if not private_valid:
            invalid_fields.append("VAPID_PRIVATE_KEY")
        if not subject.startswith(("mailto:", "https://")):
            invalid_fields.append("VAPID_SUBJECT")
        return jsonify({
            "error": "Phone notifications are not configured correctly. Device alerts need valid Render settings: " + ", ".join(invalid_fields) + ". This affects both phones and computers.",
            "configured": False,
            "invalid_fields": invalid_fields,
        }), 503
    return jsonify({"public_key": public_key, "configured": True})


@api_bp.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    parsed_endpoint = urlparse(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.netloc
        or not p256dh
        or not auth
        or len(endpoint) > 4096
        or len(p256dh) > 4096
        or len(auth) > 1024
    ):
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
    Notification.query.filter_by(
        user_id=current_user.id,
        dedupe_key=f"device-notifications:{current_user.id}",
    ).update({"seen": True})
    db.session.add(subscription)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "A device endpoint is required."}), 400
    query = PushSubscription.query.filter_by(user_id=current_user.id, endpoint=endpoint, active=True)
    query.update({"active": False})
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/unread-counts")
@login_required
def unread_counts():
    unread_messages = unread_private_message_count(current_user.id)
    important_notifications = important_unread_count(current_user.id)
    return jsonify({
        "messages": unread_messages,
        "important_notifications": important_notifications,
        "combined": unread_messages + important_notifications,
    })


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
