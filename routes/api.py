import os
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import safe_join

from extensions import db
from helpers import user_avatar_url
from models import Family, FamilyMember, MediaAsset, Message, Profile, PushSubscription, User

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
            "avatar": user_avatar_url(current_user),
            "bio": profile.bio,
            "privacy_posts": profile.privacy_posts,
            "notifications_enabled": profile.notifications_enabled,
            "notification_previews_enabled": profile.notification_previews_enabled,
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
