from functools import wraps

from flask import current_app, render_template
from sqlalchemy.exc import SQLAlchemyError

from models import SiteSetting


FEATURE_FLAG_DEFINITIONS = {
    "daily_checkins": ("Daily check-ins", True),
    "personal_points": ("Personal points", True),
    "family_points": ("Family points", True),
    "family_xp": ("Family XP", True),
    "streaks": ("Streaks", False),
    "achievement_posts": ("Achievement posts", True),
    "family_levels": ("Family levels", False),
    "family_upgrades": ("Family upgrades", True),
    "point_transfers": ("Point transfers", True),
    "referral_rewards": ("Referral rewards", True),
    "contribution_campaigns": ("Contribution campaigns", True),
    "premium_membership": ("Premium membership", True),
    "premium_families": ("Premium Families", True),
    "premium_profiles": ("Premium profiles", True),
    "premium_storage": ("Premium storage", False),
    "premium_upload_limits": ("Premium upload limits", True),
    "premium_themes": ("Premium themes", False),
    "premium_analytics": ("Premium analytics", False),
    "premium_challenges": ("Premium challenges", False),
    "premium_verification_applications": ("Premium verification applications", False),
    "premium_beta_testing": ("Premium beta testing", False),
    "weekly_reports": ("Weekly reports", False),
    "enhanced_notifications": ("Enhanced notifications", True),
    "verification_badges": ("Verification badges", True),
    "anonymous_support_posts": ("Anonymous support posts", False),
    "media_autoplay": ("Media autoplay", True),
    "video_notes": ("Premium video notes", False),
    "family_leaderboards": ("Family leaderboards", True),
}
SETTING_PREFIX = "feature_flag."
TRUE_VALUES = {"1", "true", "yes", "on"}


def feature_flag_key(name):
    return f"{SETTING_PREFIX}{name}"


def feature_flag_exists(name):
    return name in FEATURE_FLAG_DEFINITIONS


def default_feature_flags():
    return {
        name: default
        for name, (_label, default) in FEATURE_FLAG_DEFINITIONS.items()
    }


def get_feature_flags():
    flags = default_feature_flags()
    try:
        settings = SiteSetting.query.filter(
            SiteSetting.key.in_([feature_flag_key(name) for name in flags])
        ).all()
    except SQLAlchemyError:
        current_app.logger.exception("feature_flags_load_failed")
        return flags
    for setting in settings:
        name = setting.key.removeprefix(SETTING_PREFIX)
        if name in flags:
            flags[name] = (setting.value or "").strip().lower() in TRUE_VALUES
    return flags


def is_feature_enabled(name):
    if not feature_flag_exists(name):
        current_app.logger.warning("unknown_feature_flag name=%s", name)
        return False
    return get_feature_flags()[name]


def feature_required(name):
    if not feature_flag_exists(name):
        raise ValueError(f"Unknown feature flag: {name}")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not is_feature_enabled(name):
                label = FEATURE_FLAG_DEFINITIONS[name][0]
                return render_template("coming_soon.html", feature_name=label), 404
            return view(*args, **kwargs)

        return wrapped

    return decorator
