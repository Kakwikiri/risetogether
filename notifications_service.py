from datetime import datetime, timedelta

from extensions import db, socketio
from sqlalchemy.exc import IntegrityError
from helpers import send_device_push
from models import Notification, NotificationDeliveryKey, NotificationPreference, User


NOTIFICATION_CATEGORIES = {
    "message": "Messages", "comment": "Comments", "reaction": "Reactions",
    "follow": "Follows and connections", "family_invitation": "Family invitations",
    "challenge_invitation": "Challenge invitations", "challenge_reminder": "Challenge reminders",
    "challenge_completed": "Challenge completions", "goal_progress": "Goal progress",
    "weekly_report": "Weekly reports", "upgrade_campaign": "Upgrade campaigns",
    "contribution_received": "Contributions received", "family_level": "Family level increases",
    "encouragement": "Encouragement requests",
}

CATEGORY_ALIASES = {
    "family_chat": "message", "voice_note": "message", "video_note": "message", "call": "message",
    "comment_reply": "comment", "mention": "comment", "comment_reaction": "comment",
    "friend_request": "follow", "friend_accept": "follow", "followed_post": "follow",
    "family_invite": "family_invitation", "family_poll": "family_invitation", "family_role": "family_invitation",
    "quiz_starting": "challenge_invitation", "family_challenge": "challenge_invitation", "challenge_created": "challenge_invitation",
    "challenge_pending": "challenge_completed", "challenge_approved": "challenge_completed", "challenge_approval": "challenge_completed",
    "goal": "goal_progress", "weekly_family_report": "weekly_report",
    "family_upgrade": "upgrade_campaign", "campaign_milestone": "upgrade_campaign",
    "contribution_campaign": "upgrade_campaign", "family_upgrade_unlocked": "upgrade_campaign",
    "campaign_contribution": "contribution_received", "family_level_up": "family_level",
    "checkin_support": "encouragement", "encouragement_request": "encouragement", "encouragement_response": "encouragement",
}


def canonical_category(category):
    return CATEGORY_ALIASES.get(category, category if category in NOTIFICATION_CATEGORIES else category)


def notification_allowed(user_id, category):
    user = User.query.get(user_id)
    if not user or not user.profile or not user.profile.notifications_enabled:
        return False
    canonical = canonical_category(category)
    preference = NotificationPreference.query.filter_by(user_id=user_id, category=canonical).first()
    return preference is None or preference.enabled


def smart_notify(*, user_id, category, message, action_url="", group_key="", dedupe_key=None,
                 reminder=False, push=True):
    if not notification_allowed(user_id, category):
        return None, False
    canonical = canonical_category(category)
    delivery_key = None
    if dedupe_key:
        try:
            with db.session.begin_nested():
                delivery_key = NotificationDeliveryKey(key=dedupe_key[:180])
                db.session.add(delivery_key)
                db.session.flush()
        except IntegrityError:
            existing_key = NotificationDeliveryKey.query.get(dedupe_key[:180])
            return (existing_key.notification if existing_key else None), False
    group_target = action_url.split("#", 1)[0]
    normalized_group = (group_key or f"{canonical}:{group_target}")[:180]
    cutoff = datetime.utcnow() - timedelta(hours=24)
    existing = None
    if normalized_group:
        existing = Notification.query.filter(
            Notification.user_id == user_id, Notification.group_key == normalized_group,
            Notification.seen.is_(False), Notification.updated_at >= cutoff,
        ).with_for_update().order_by(Notification.updated_at.desc()).first()
    if existing:
        if reminder:
            if delivery_key:
                delivery_key.notification_id = existing.id
            return existing, False
        existing.event_count += 1
        existing.message = message[:1000]
        existing.action_url = action_url[:255]
        existing.updated_at = datetime.utcnow()
        notification = existing
        created = False
    else:
        notification = Notification(
            user_id=user_id, category=canonical, message=message[:1000],
            action_url=action_url[:255], group_key=normalized_group,
            dedupe_key=dedupe_key[:180] if dedupe_key else None,
        )
        db.session.add(notification)
        db.session.flush()
        created = True
    if delivery_key:
        delivery_key.notification_id = notification.id
    socketio.emit("notification_received", {
        "id": notification.id, "category": notification.category,
        "message": notification.message, "action_url": notification.action_url,
        "event_count": notification.event_count,
        "created_at": notification.updated_at.strftime("%Y-%m-%d %H:%M"),
    }, room=f"user-{user_id}")
    if push and (created or not reminder):
        send_device_push(notification)
    return notification, created


def save_notification_preferences(user, form):
    for category in NOTIFICATION_CATEGORIES:
        preference = NotificationPreference.query.filter_by(user_id=user.id, category=category).first()
        if not preference:
            preference = NotificationPreference(user_id=user.id, category=category)
            db.session.add(preference)
        preference.enabled = form.get(f"notification_{category}") == "on"


def preference_map(user):
    stored = {row.category: row.enabled for row in user.notification_preferences.all()}
    return {category: stored.get(category, True) for category in NOTIFICATION_CATEGORIES}
