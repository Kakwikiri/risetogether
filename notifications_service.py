from datetime import datetime, timedelta

from extensions import db, socketio
from sqlalchemy.exc import IntegrityError
from helpers import send_device_push
from sqlalchemy import or_
from models import Message, MessageDeletion, Notification, NotificationDeliveryKey, NotificationPreference, User


NOTIFICATION_CATEGORIES = {
    "message": "Messages",
    "friends": "Friends",
    "families": "Families",
    "support": "Support and encouragement",
    "challenges": "Challenges",
    "reminders": "Reminders",
    "admin": "Admin notices",
}

CATEGORY_ALIASES = {
    "family_chat": "message", "voice_note": "message", "video_note": "message", "call": "message", "message_reaction": "message",
    "comment": "families", "comment_reply": "families", "mention": "families", "comment_reaction": "families", "reaction": "families",
    "follow": "friends", "friend_request": "friends", "friend_accept": "friends", "followed_post": "friends",
    "family_invitation": "families", "family_invite": "families", "family_poll": "families", "family_role": "families",
    "weekly_report": "families", "weekly_family_report": "families", "family_upgrade": "families", "upgrade_campaign": "families",
    "campaign_milestone": "families", "contribution_campaign": "families", "family_upgrade_unlocked": "families",
    "campaign_contribution": "families", "contribution_received": "families", "family_level": "families", "family_level_up": "families",
    "challenge_invitation": "challenges", "quiz_starting": "challenges", "family_challenge": "challenges", "challenge_created": "challenges",
    "challenge_completed": "challenges", "challenge_pending": "challenges", "challenge_approved": "challenges", "challenge_approval": "challenges",
    "goal": "challenges", "goal_progress": "challenges", "challenge_reminder": "reminders",
    "encouragement": "support", "checkin_support": "support", "encouragement_request": "support", "encouragement_response": "support",
    "appreciation": "support", "post_support": "support", "listen_accepted": "support", "return_checkin": "support", "return_thanks": "support",
    "share": "families", "live": "families", "family": "families",
    "family_moderation": "admin", "points_reversed": "admin", "admin_warning": "admin",
}

IMPORTANT_EVENT_CATEGORIES = {
    "message", "voice_note", "video_note", "message_reaction", "friend_request", "family_invite",
    "encouragement_response", "checkin_support", "appreciation", "post_support", "listen_accepted",
    "return_checkin", "return_thanks", "challenge_approved", "challenge_approval",
    "family", "family_moderation", "points_reversed", "admin_warning",
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
                 reminder=False, push=True, important=None):
    if not notification_allowed(user_id, category):
        return None, False
    raw_category = category
    canonical = canonical_category(category)
    is_important = raw_category in IMPORTANT_EVENT_CATEGORIES if important is None else bool(important)
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
        existing.important = existing.important or is_important
        notification = existing
        created = False
    else:
        notification = Notification(
            user_id=user_id, category=canonical, message=message[:1000],
            action_url=action_url[:255], group_key=normalized_group,
            dedupe_key=dedupe_key[:180] if dedupe_key else None,
            important=is_important,
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
        "important": notification.important,
        "created_at": notification.updated_at.strftime("%Y-%m-%d %H:%M"),
    }, room=f"user-{user_id}")
    if push and is_important and (created or not reminder):
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


def important_unread_count(user_id):
    return Notification.query.filter(
        Notification.user_id == user_id,
        Notification.seen == False,
        Notification.important == True,
        Notification.category != "message",
    ).count()


def unread_private_message_count(user_id):
    return Message.query.filter(
        Message.recipient_id == user_id,
        Message.family_id == None,
        Message.read_at == None,
        or_(Message.expires_at == None, Message.expires_at > datetime.utcnow()),
        ~Message.deletions.any(MessageDeletion.user_id == user_id),
    ).count()


def queue_device_push(notification_id):
    """Deliver Web Push after the request/socket response is no longer blocked."""
    from flask import current_app

    app = current_app._get_current_object()

    def deliver():
        with app.app_context():
            notification = db.session.get(Notification, notification_id)
            if not notification:
                return
            send_device_push(notification)
            db.session.commit()

    socketio.start_background_task(deliver)
