from flask import Blueprint, current_app, jsonify, send_from_directory
from flask_login import current_user, login_required

from models import Family

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


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
