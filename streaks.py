from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from extensions import db
from feature_flags import is_feature_enabled
from models import Notification, StreakActivity, StreakMilestone, UserStreak
from points import PointLimitExceeded, award_points


STREAK_DEFINITIONS = {
    "challenge_progress": ("Challenge progress", "Complete an approved Family challenge on consecutive local days."),
    "habit": ("Habit", "Complete a Habit challenge on consecutive local days."),
    "reflection": ("Daily reflection", "Save a daily check-in with a meaningful note of at least 10 characters."),
    "encouragement": ("Encouragement", "Send a supportive response with a thoughtful comment of at least 10 characters."),
    "learning": ("Learning", "Complete a quiz, lesson, or reading challenge on consecutive local days."),
}
MILESTONE_REWARDS = {7: 5, 30: 15, 100: 25, 365: 50}


def safe_timezone_name(user):
    name = getattr(getattr(user, "profile", None), "timezone", "Africa/Kampala") or "Africa/Kampala"
    try:
        ZoneInfo(name)
        return name
    except ZoneInfoNotFoundError:
        return "UTC"


def local_activity_date(user, occurred_at=None):
    occurred_at = occurred_at or datetime.utcnow()
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return occurred_at.astimezone(ZoneInfo(safe_timezone_name(user))).date()


def record_streak_activity(user, streak_type, *, source_type, source_id, unique_key, occurred_at=None):
    """Queue one server-verified constructive activity in the active transaction."""
    if not is_feature_enabled("streaks") or streak_type not in STREAK_DEFINITIONS:
        return None, False
    unique_key = (unique_key or "").strip()[:160]
    if not unique_key:
        raise ValueError("A streak activity requires a unique server key.")
    if StreakActivity.query.filter_by(unique_activity_key=unique_key).first():
        return UserStreak.query.filter_by(user_id=user.id, streak_type=streak_type).first(), False
    activity_date = local_activity_date(user, occurred_at)
    if StreakActivity.query.filter_by(
        user_id=user.id, streak_type=streak_type, activity_date=activity_date
    ).first():
        return UserStreak.query.filter_by(user_id=user.id, streak_type=streak_type).first(), False
    streak = UserStreak.query.filter_by(user_id=user.id, streak_type=streak_type).with_for_update().first()
    if not streak:
        streak = UserStreak(user_id=user.id, streak_type=streak_type, grace_days_available=1)
        db.session.add(streak)
        db.session.flush()
    previous_date = streak.last_activity_date
    if previous_date is None:
        streak.current_count = 1
    elif activity_date == previous_date:
        return streak, False
    elif activity_date == previous_date + timedelta(days=1):
        streak.current_count += 1
    elif activity_date == previous_date + timedelta(days=2) and streak.grace_days_available > 0:
        streak.grace_days_available -= 1
        streak.current_count += 1
    elif activity_date > previous_date:
        streak.previous_count = streak.current_count
        streak.current_count = 1
    else:
        return streak, False
    streak.last_activity_date = activity_date
    streak.best_count = max(streak.best_count, streak.current_count)
    db.session.add(StreakActivity(
        user_id=user.id, streak_type=streak_type, activity_date=activity_date,
        source_type=source_type, source_id=source_id, unique_activity_key=unique_key,
    ))
    award_streak_milestone(user, streak)
    return streak, True


def award_streak_milestone(user, streak):
    bonus = MILESTONE_REWARDS.get(streak.current_count)
    if bonus is None or StreakMilestone.query.filter_by(
        streak_id=streak.id, milestone=streak.current_count
    ).first():
        return
    label = STREAK_DEFINITIONS[streak.streak_type][0]
    milestone = StreakMilestone(
        streak_id=streak.id, milestone=streak.current_count,
        badge_name=f"{streak.current_count}-day {label} streak", bonus_points=0,
    )
    db.session.add(milestone)
    db.session.flush()
    if is_feature_enabled("personal_points"):
        try:
            _transaction, created = award_points(
                amount=bonus, reason=f"{streak.current_count}-day {label} streak milestone",
                source_type="streak_milestone", source_id=milestone.id,
                unique_reward_key=f"streak:{streak.id}:milestone:{streak.current_count}",
                user_id=user.id, repeatable=False,
            )
            if created:
                milestone.bonus_points = bonus
        except PointLimitExceeded:
            milestone.bonus_points = 0
    db.session.add(Notification(
        user_id=user.id, category="streak_milestone",
        message=f"You reached a {streak.current_count}-day {label} streak. Your steady effort matters.",
        action_url="/streaks",
    ))


def record_challenge_streaks(completion):
    if completion.verification_status != "completed":
        return
    occurred_at = completion.completed_at
    record_streak_activity(
        completion.user, "challenge_progress", source_type="challenge_completion",
        source_id=completion.id, unique_key=f"challenge-progress:{completion.id}",
        occurred_at=occurred_at,
    )
    if completion.challenge.challenge_type == "habit":
        record_streak_activity(
            completion.user, "habit", source_type="challenge_completion",
            source_id=completion.id, unique_key=f"habit-completion:{completion.id}",
            occurred_at=occurred_at,
        )
    if completion.challenge.challenge_type in {"learning_lesson", "reading"}:
        record_streak_activity(
            completion.user, "learning", source_type="challenge_completion",
            source_id=completion.id, unique_key=f"learning-completion:{completion.id}",
            occurred_at=occurred_at,
        )


def queue_expiring_streak_warning(user):
    """Queue at most one compassionate warning per local day; never counts as activity."""
    if not is_feature_enabled("streaks"):
        return False
    today = local_activity_date(user)
    queued = False
    for streak in user.streaks.filter(UserStreak.current_count > 1).all():
        warning_due = (
            streak.last_activity_date == today - timedelta(days=1)
            or (
                streak.grace_days_available > 0
                and streak.last_activity_date == today - timedelta(days=2)
            )
        )
        if warning_due and streak.last_warning_date != today:
            label = STREAK_DEFINITIONS.get(streak.streak_type, ("Meaningful", ""))[0]
            db.session.add(Notification(
                user_id=user.id, category="streak_reminder",
                message=f"Your {label} streak is still within reach today.. only if it feels supportive.",
                action_url="/streaks",
            ))
            streak.last_warning_date = today
            queued = True
    return queued
