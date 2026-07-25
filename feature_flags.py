from functools import wraps

from flask import current_app, has_request_context, render_template
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from models import FamilyMember, SiteSetting


FEATURE_FLAG_DEFINITIONS = {
    "daily_checkins": ("Daily check-ins", True),
    "goals": ("Goals and progress reminders", True),
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
FEATURE_FLAG_DESCRIPTIONS = {
    "daily_checkins": "Private or public emotional check-ins and supportive responses.",
    "goals": "Personal and Family goals, progress updates, milestones and encouragement.",
    "personal_points": "Points earned by an individual through verified activity.",
    "family_points": "Shared Family points used for approved Family upgrades.",
    "family_xp": "Permanent Family experience used to calculate Family levels.",
    "streaks": "Optional consistency tracking with grace days and milestones.",
    "achievement_posts": "Lets completed achievements be shared as feed posts.",
    "family_levels": "Displays Family growth levels calculated from permanent XP.",
    "family_upgrades": "Allows Families to unlock capacity and collaboration improvements.",
    "point_transfers": "Allows permitted point gifts and transfers with safety limits.",
    "referral_rewards": "Rewards qualified invitations after genuine activity requirements.",
    "contribution_campaigns": "Lets members combine points toward a Family upgrade.",
    "premium_membership": "Master access switch for paid or manually granted Premium.",
    "premium_families": "Premium capacity and convenience for eligible Families.",
    "premium_profiles": "Premium profile frame and supporter presentation. Requires Premium membership and an active subscription.",
    "premium_storage": "Reserved control for larger Premium storage; keep off until storage accounting is enabled.",
    "premium_upload_limits": "Larger media sizes and longer recordings for active Premium accounts.",
    "premium_themes": "Reserved control for Premium visual themes.",
    "premium_analytics": "Reserved control for advanced Premium account insights.",
    "premium_challenges": "Reserved control for additional Premium challenge tools.",
    "premium_verification_applications": "Allows Premium users to submit verification for manual review; Premium never guarantees approval.",
    "premium_beta_testing": "Shows Super Admin tools for granting test subscriptions without payment.",
    "weekly_reports": "Creates a weekly Family summary of progress and recognition.",
    "enhanced_notifications": "Adds richer grouped and contextual notifications.",
    "verification_badges": "Displays manually approved user, organization and Family badges.",
    "anonymous_support_posts": "Allows identity-protected requests for Family encouragement.",
    "media_autoplay": "Allows eligible feed media to play automatically.",
    "video_notes": "Allows active Premium accounts to record and send video notes.",
    "family_leaderboards": "Shows Family learning or challenge rankings when enough activity exists.",
}
SETTING_PREFIX = "feature_flag."
ROLLOUT_PREFIX = "feature_rollout."
ROLLOUT_USERS_PREFIX = "feature_rollout_users."
ROLLOUT_FAMILIES_PREFIX = "feature_rollout_families."
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


def feature_rollout_key(name):
    return f"{ROLLOUT_PREFIX}{name}"


def feature_rollout_users_key(name):
    return f"{ROLLOUT_USERS_PREFIX}{name}"


def feature_rollout_families_key(name):
    return f"{ROLLOUT_FAMILIES_PREFIX}{name}"


def get_feature_rollouts():
    flags = get_feature_flags()
    rollouts = {}
    keys = [
        key
        for name in FEATURE_FLAG_DEFINITIONS
        for key in (
            feature_rollout_key(name),
            feature_rollout_users_key(name),
            feature_rollout_families_key(name),
        )
    ]
    try:
        settings = {row.key: row.value or "" for row in SiteSetting.query.filter(SiteSetting.key.in_(keys)).all()}
    except SQLAlchemyError:
        settings = {}
    for name in FEATURE_FLAG_DEFINITIONS:
        fallback = "everyone" if flags[name] else "off"
        mode = settings.get(feature_rollout_key(name), fallback)
        if mode not in {"off", "selected", "everyone"}:
            mode = fallback
        user_ids = {
            int(value) for value in settings.get(feature_rollout_users_key(name), "").split(",")
            if value.strip().isdigit()
        }
        family_ids = {
            int(value) for value in settings.get(feature_rollout_families_key(name), "").split(",")
            if value.strip().isdigit()
        }
        rollouts[name] = {"mode": mode, "user_ids": user_ids, "family_ids": family_ids}
    return rollouts


def get_effective_feature_flags(user=None):
    if user is None and has_request_context() and current_user.is_authenticated:
        user = current_user
    user_id = getattr(user, "id", None)
    family_ids = set()
    if user_id:
        try:
            family_ids = {
                row.family_id for row in FamilyMember.query.filter_by(user_id=user_id).all()
            }
        except SQLAlchemyError:
            family_ids = set()
    return {
        name: (
            rollout["mode"] == "everyone"
            or (
                rollout["mode"] == "selected"
                and (
                    user_id in rollout["user_ids"]
                    or bool(family_ids & rollout["family_ids"])
                )
            )
        )
        for name, rollout in get_feature_rollouts().items()
    }


def is_feature_enabled(name, user=None):
    if not feature_flag_exists(name):
        current_app.logger.warning("unknown_feature_flag name=%s", name)
        return False
    return get_effective_feature_flags(user)[name]


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
