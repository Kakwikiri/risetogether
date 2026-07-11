import os

from flask import Blueprint, Response, current_app, jsonify, send_from_directory
from flask_login import current_user, login_required
from werkzeug.utils import safe_join

from models import Family, MediaAsset, Message

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
        }
    )


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
