from datetime import datetime

from flask import current_app, has_request_context
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from models import PremiumSubscription, SiteSetting


ECONOMY_DEFAULTS = {
    "point_transfer_daily_limit": 10,
    "personal_reward_daily_limit": 100,
    "contribution_hourly_limit": 20,
    "free_family_capacity": 50,
    "free_image_upload_mb": 5,
    "free_video_upload_mb": 25,
    "free_file_upload_mb": 10,
    "premium_image_upload_mb": 15,
    "premium_video_upload_mb": 75,
    "premium_file_upload_mb": 30,
    "free_voice_note_seconds": 180,
    "free_video_note_seconds": 180,
    "premium_voice_note_seconds": 600,
    "premium_video_note_seconds": 600,
    "free_family_admins": 2,
    "free_family_moderators": 4,
    "premium_family_admins": 10,
    "premium_family_moderators": 20,
    "premium_family_capacity": 500,
    "premium_monthly_price": 5,
    "premium_yearly_price": 50,
    "premium_family_monthly_price": 12,
    "premium_family_yearly_price": 120,
}


def economy_setting_int(key, default=None, minimum=0, maximum=10_000_000):
    fallback = ECONOMY_DEFAULTS.get(key, default if default is not None else 0)
    try:
        setting = SiteSetting.query.get(f"economy.{key}")
        value = int(setting.value) if setting and setting.value not in (None, "") else int(fallback)
    except (SQLAlchemyError, TypeError, ValueError):
        value = int(fallback)
    return max(minimum, min(maximum, value))


def economy_setting_text(key, default="", allowed=None):
    try:
        setting = SiteSetting.query.get(f"economy.{key}")
        value = (setting.value if setting else default).strip()
    except (SQLAlchemyError, AttributeError):
        value = default
    return value if not allowed or value in allowed else default


def subscription_is_active(subscription, now=None):
    if not subscription or subscription.status != "active":
        return False
    now = now or datetime.utcnow()
    if subscription.expires_at and subscription.expires_at <= now:
        return False
    return True


def active_user_subscription(user_id):
    if not user_id:
        return None
    rows = PremiumSubscription.query.filter_by(
        user_id=user_id, plan="personal", status="active"
    ).order_by(PremiumSubscription.purchased_at.desc()).all()
    return next((row for row in rows if subscription_is_active(row)), None)


def active_family_subscription(family_id):
    if not family_id:
        return None
    rows = PremiumSubscription.query.filter_by(
        family_id=family_id, plan="family", status="active"
    ).order_by(PremiumSubscription.purchased_at.desc()).all()
    return next((row for row in rows if subscription_is_active(row)), None)


def user_has_premium(user=None):
    from feature_flags import is_feature_enabled

    user = user or (current_user if has_request_context() and current_user.is_authenticated else None)
    return bool(
        user
        and is_feature_enabled("premium_membership")
        and is_feature_enabled("premium_profiles")
        and active_user_subscription(user.id)
    )


def family_has_premium(family):
    from feature_flags import is_feature_enabled

    return bool(
        family
        and is_feature_enabled("premium_membership")
        and is_feature_enabled("premium_families")
        and active_family_subscription(family.id)
    )


def upload_limit_for(media_type, user=None):
    premium = user_has_premium(user)
    premium_limits = premium and _flag_enabled("premium_upload_limits")
    prefix = "premium" if premium_limits else "free"
    normalized = "file" if media_type not in {"image", "video"} else media_type
    configured_mb = economy_setting_int(f"{prefix}_{normalized}_upload_mb", minimum=1, maximum=2048)
    base_key = {"image": "IMAGE_UPLOAD_LIMIT", "video": "VIDEO_UPLOAD_LIMIT"}.get(
        normalized, "FILE_UPLOAD_LIMIT"
    )
    premium_key = {"image": "PREMIUM_IMAGE_UPLOAD_LIMIT", "video": "PREMIUM_VIDEO_UPLOAD_LIMIT"}.get(
        normalized, "PREMIUM_FILE_UPLOAD_LIMIT"
    )
    safe_ceiling = max(int(current_app.config[base_key]), int(current_app.config[premium_key]))
    return min(safe_ceiling, configured_mb * 1024 * 1024)


def recording_limit_seconds(media_type, user=None):
    premium = user_has_premium(user) and _flag_enabled("premium_upload_limits")
    prefix = "premium" if premium else "free"
    key = f"{prefix}_{'voice' if media_type == 'audio' else 'video'}_note_seconds"
    return economy_setting_int(key, minimum=30, maximum=3600)


def _flag_enabled(name):
    from feature_flags import is_feature_enabled

    return is_feature_enabled(name)
