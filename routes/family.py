import os
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from feature_flags import feature_required, is_feature_enabled
from family_levels import family_level_summary
from family_upgrades import (
    CERTIFICATE_STYLES, active_challenge_limit, campaign_contributed_points, configured_upgrade_catalog,
    family_has_upgrade, next_capacity_target, open_quiz_limit, upgrade_can_be_targeted,
    purchased_upgrade_keys, upgrade_definition, upgrade_is_available,
)
from helpers import get_media_type, get_upload_limit, save_media, validate_upload
from ownership import is_platform_owner
from models import (
    Appreciation,
    ChallengeCompletion,
    ChallengeParticipant,
    DailyCheckIn,
    EncouragementRequest,
    EncouragementRequestReport,
    EncouragementResponse,
    CheckInResponse,
    AuditLog,
    Block,
    Family,
    FamilyChallenge,
    FamilyMember,
    FamilyWeeklyReport,
    FamilyMemberRestriction,
    FamilyModerationLog,
    FamilyGalleryItem,
    FamilyCampaignContribution,
    FamilyContributionCampaign,
    FamilyUpgradePurchase,
    Goal,
    GoalActivity,
    FamilyPoll,
    FamilyPollOption,
    FamilyPollVote,
    MediaAsset,
    Message,
    Notification,
    Post,
    PointTransaction,
    PointSecurityEvent,
    Profile,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizChoice,
    QuizQuestion,
    SiteSetting,
    User,
)
from points import (
    PointLimitExceeded, award_challenge_completion_points, award_points, family_point_balance,
    personal_point_balance, reverse_reward_group, spend_family_points,
    spend_personal_points,
)
from streaks import record_challenge_streaks, record_streak_activity
from notifications_service import smart_notify
from weekly_reports import get_or_create_weekly_report, report_post_content

family_bp = Blueprint("family", __name__)

FAMILY_CATEGORIES = {
    "class": "Class / course",
    "learning": "Learning",
    "quiz_and_trivia": "Quiz and trivia",
    "motivation": "Motivation",
    "fitness": "Fitness",
    "business": "Business",
    "coding": "Coding",
    "books": "Books",
    "language_learning": "Language learning",
    "accountability": "Accountability",
    "friendship_and_support": "Friendship and support",
    "custom": "Custom",
}

FAMILY_PRIVACY_OPTIONS = {"public", "private", "invite_only"}
ENCOURAGEMENT_CATEGORIES = {
    "listen": "I need someone to listen", "motivation": "Motivation",
    "advice": "Advice", "celebration": "Celebration",
    "grief": "Grief or sadness", "alone": "Feeling alone",
    "study_work": "Study/work encouragement", "other": "Other",
}
ENCOURAGEMENT_REACTIONS = {
    "support": "Support", "understand": "I Understand",
    "keep_going": "Keep Going", "inspire": "You Inspire Me",
}
APPRECIATION_MESSAGES = {
    "thank_you": "Thank you.",
    "words_helped": "Your words helped me.",
    "appreciate_advice": "I appreciate your advice.",
}
CRISIS_PHRASES = (
    "kill myself", "end my life", "want to die", "going to die",
    "hurt myself", "harm myself", "suicide", "not safe right now",
    "immediate danger",
)

CHALLENGE_TYPES = {
    "task": "Task",
    "daily_check_in": "Daily check-in",
    "learning_lesson": "Learning lesson",
    "habit": "Habit",
    "quiz": "Quiz",
    "team": "Team challenge",
    "major": "Major one-time challenge",
    "reading": "Reading",
    "fitness": "Fitness",
    "saving": "Saving",
    "reflection": "Reflection",
    "creative": "Creative",
}

REWARD_TIER_DEFAULTS = {
    "small": 5,
    "easy": 10,
    "medium": 25,
    "hard": 50,
    "major": 100,
}
REWARD_TIER_LABELS = {
    "small": "Small action",
    "easy": "Easy",
    "medium": "Medium",
    "hard": "Hard",
    "major": "Major milestone",
}
CHALLENGE_ALLOWED_REWARD_TIERS = {
    "daily_check_in": {"small"},
    "habit": {"small", "easy"},
    "task": {"easy"},
    "learning_lesson": {"easy", "medium"},
    "quiz": {"small", "easy", "medium"},
    "team": {"small", "easy", "medium", "hard"},
    "major": {"small", "easy", "medium", "hard", "major"},
    "reading": {"easy", "medium"},
    "fitness": {"small", "easy", "medium"},
    "saving": {"easy", "medium", "hard"},
    "reflection": {"small", "easy"},
    "creative": {"easy", "medium", "hard"},
}
COMPLETION_FREQUENCIES = {"one_time", "daily", "weekly", "custom"}
EVIDENCE_REQUIREMENTS = {"none", "completion_note", "photo", "video", "audio", "file", "admin_approval"}
PARTICIPANT_SCOPES = {"all_members", "admins_moderators", "owners_admins"}
CHALLENGE_VISIBILITIES = {"family", "public", "admins_only"}

CHALLENGE_STATUSES = {"active", "draft", "closed"}

QUIZ_CAPABLE_CATEGORIES = {
    "class",
    "learning",
    "quiz_and_trivia",
    "coding",
    "books",
    "language_learning",
}

QUIZ_STATUSES = {"open", "draft", "closed"}

ACHIEVEMENT_TYPES = {
    "challenge_completed",
    "goal_achieved",
    "streak_milestone",
    "quiz_passed",
    "family_level_increased",
    "family_upgrade_unlocked",
    "encouragement_milestone",
    "weekly_family_milestone",
}

FAMILY_ROLES = {"owner", "admin", "moderator", "member"}
FAMILY_ROLE_LABELS = {
    "owner": "Owner",
    "admin": "Admin",
    "moderator": "Moderator",
    "member": "Member",
}
FAMILY_ROLE_RANK = {
    "owner": 4,
    "admin": 3,
    "moderator": 2,
    "member": 1,
}
FAMILY_PERMISSIONS = {
    "edit_family": {"owner"},
    "change_family_image": {"owner", "admin"},
    "manage_roles": {"owner"},
    "manage_members": {"owner", "admin"},
    "warn_members": {"owner", "admin", "moderator"},
    "mute_members": {"owner", "admin", "moderator"},
    "suspend_members": {"owner", "admin"},
    "create_poll": {"owner", "admin"},
    "create_challenge": {"owner", "admin"},
    "create_quiz": {"owner", "admin"},
    "create_campaign": {"owner", "admin"},
    "invite_members": {"owner", "admin"},
    "delete_family": {"owner"},
    "activate_upgrade": {"owner", "admin"},
}

FAMILY_CREATION_GRANTS = {
    "create_poll": "can_create_polls",
    "create_quiz": "can_create_quizzes",
    "create_challenge": "can_create_challenges",
    "create_campaign": "can_create_campaigns",
}


def default_family_member_limit():
    try:
        return max(2, int(current_app.config.get("DEFAULT_FAMILY_MEMBER_LIMIT", 50)))
    except (TypeError, ValueError):
        return 50


def effective_family_member_limit(family):
    owner = User.query.get(family.owner_id) if family.owner_id else None
    if owner and is_platform_owner(owner):
        return 1_000_000_000
    from premium import economy_setting_int, family_has_premium
    base_limit = max(
        family.member_limit or default_family_member_limit(),
        economy_setting_int("free_family_capacity", 50, minimum=2, maximum=500),
    )
    if family_has_premium(family):
        return max(base_limit, economy_setting_int("premium_family_capacity", 500, minimum=50, maximum=100_000))
    return base_limit


def active_family_members_query(family_id):
    return FamilyMember.query.join(User, FamilyMember.user_id == User.id).filter(
        FamilyMember.family_id == family_id,
        User.is_banned == False,
    )


def active_family_member_count(family):
    return active_family_members_query(family.id).count()


def family_is_full(family):
    return active_family_member_count(family) >= effective_family_member_limit(family)


def family_capacity_status(family):
    limit = effective_family_member_limit(family)
    count = active_family_member_count(family)
    return {
        "member_count": count,
        "member_limit": limit,
        "member_limit_label": "Unlimited" if limit >= 1_000_000_000 else str(limit),
        "remaining_slots": max(0, limit - count),
        "is_full": count >= limit,
    }


def family_role_limit(family, role):
    from premium import economy_setting_int, family_has_premium

    if role == "admin":
        base = economy_setting_int("free_family_admins", 2, minimum=1, maximum=1000)
        if family_has_upgrade(family.id, "extra_admins"):
            base += 2
        if family_has_premium(family):
            base = max(base, economy_setting_int("premium_family_admins", 10, minimum=1, maximum=1000))
        return base
    base = economy_setting_int("free_family_moderators", 4, minimum=1, maximum=1000)
    if family_has_upgrade(family.id, "extra_moderators"):
        base += 5
    if family_has_premium(family):
        base = max(base, economy_setting_int("premium_family_moderators", 20, minimum=1, maximum=1000))
    return base


def validate_family_image_upload(file):
    is_valid, message = validate_upload(file)
    if not is_valid:
        return False, message
    if get_media_type(file.filename) != "image":
        return False, "Family picture must be an image."
    try:
        from PIL import Image
    except ImportError:
        file.stream.seek(0)
        return True, ""
    try:
        position = file.stream.tell()
        file.stream.seek(0)
        with Image.open(file.stream) as image:
            image.verify()
        file.stream.seek(position)
    except OSError:
        file.stream.seek(0)
        return False, "Family picture file is not a valid image."
    file.stream.seek(0)
    return True, ""


def media_filename_is_referenced(filename):
    if not filename:
        return False
    if Family.query.filter_by(profile_image=filename).first():
        return True
    if Profile.query.filter_by(avatar=filename).first():
        return True
    if Post.query.filter_by(media_url=filename).first():
        return True
    if Message.query.filter_by(media_url=filename).first():
        return True
    return False


def cleanup_family_image(filename):
    if not filename or media_filename_is_referenced(filename):
        return
    safe_filename = os.path.basename(filename)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], safe_filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            current_app.logger.warning("family_image_cleanup_failed filename=%s", safe_filename)
    asset = MediaAsset.query.filter_by(filename=safe_filename).first()
    if asset:
        db.session.delete(asset)
        db.session.commit()


def add_family_notification(user_id, category, message, action_url, important=None):
    notification, _ = smart_notify(
        user_id=user_id, category=category, message=message, action_url=action_url,
        important=important,
    )
    return notification


def notify_family_level_increase(family, previous_level):
    if previous_level is None or not is_feature_enabled("family_levels"):
        return
    db.session.flush()
    summary = family_level_summary(family)
    if summary["level"] <= previous_level:
        return
    for membership in family.members:
        smart_notify(
            user_id=membership.user_id, category="family_level",
            message=f"{family.name} grew to Level {summary['level']} · {summary['name']}!",
            action_url=url_for("family.family_detail", family_id=family.id) + "#family-home",
            group_key=f"family-level:{family.id}:{summary['level']}",
            dedupe_key=f"family-level:{family.id}:{summary['level']}:{membership.user_id}",
        )


def normalize_family_role(role):
    return role if role in FAMILY_ROLES else "member"


def family_role(member):
    return normalize_family_role(member.role) if member else None


def family_has_permission(member, permission):
    return family_role(member) in FAMILY_PERMISSIONS.get(permission, set())


def family_can_create(member, permission):
    if family_has_permission(member, permission):
        return True
    field = FAMILY_CREATION_GRANTS.get(permission)
    return bool(member and field and getattr(member, field, False))


def role_rank(role):
    return FAMILY_ROLE_RANK.get(normalize_family_role(role), 0)


def current_family_member_or_redirect(family, message="Join this Family first."):
    member = family_member_for_current_user(family)
    if not member:
        flash(message, "warning")
    return member


def log_family_action(family, action, target_user_id=None, previous_role="", new_role="", reason=""):
    db.session.add(
        FamilyModerationLog(
            family_id=family.id,
            actor_id=current_user.id,
            target_user_id=target_user_id,
            action=action,
            previous_role=previous_role or "",
            new_role=new_role or "",
            reason=reason or "",
        )
    )


def user_blocked_or_suspended(user_id):
    user = User.query.get(user_id)
    if not user or user.is_banned:
        return True
    blocked = Block.query.filter(
        ((Block.blocker_id == current_user.id) & (Block.blocked_id == user_id))
        | ((Block.blocker_id == user_id) & (Block.blocked_id == current_user.id))
    ).first()
    return blocked is not None


def active_family_restriction(family_id, user_id, restriction_type=None):
    query = FamilyMemberRestriction.query.filter_by(
        family_id=family_id,
        user_id=user_id,
        active=True,
    ).filter(
        (FamilyMemberRestriction.ends_at == None)
        | (FamilyMemberRestriction.ends_at > datetime.utcnow())
    )
    if restriction_type:
        query = query.filter_by(restriction_type=restriction_type)
    return query.first()


def family_admin_required(family):
    member = family_member_for_current_user(family)
    return member if family_role(member) in {"owner", "admin"} else None


def family_member_for_current_user(family):
    return FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()


def parse_family_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def parse_family_datetime(value):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except ValueError:
            continue
    return None


def family_form_context(**extra):
    context = {
        "categories": FAMILY_CATEGORIES,
        "privacy_options": FAMILY_PRIVACY_OPTIONS,
        "default_member_limit": default_family_member_limit(),
    }
    context.update(extra)
    return context


def parse_points(value):
    try:
        points = int((value or "10").strip())
    except ValueError:
        return None
    if points < 0:
        return None
    return min(points, 10000)


def challenge_reward_values():
    values = dict(REWARD_TIER_DEFAULTS)
    settings = SiteSetting.query.filter(
        SiteSetting.key.in_([f"challenge_reward_{key}" for key in values])
    ).all()
    for setting in settings:
        tier = setting.key.removeprefix("challenge_reward_")
        try:
            configured = int(setting.value)
        except (TypeError, ValueError):
            continue
        if tier in values and 5 <= configured <= 10000:
            values[tier] = configured
    return values


def challenge_completion_period(challenge, moment=None):
    moment = moment or datetime.utcnow()
    frequency = getattr(challenge, "completion_frequency", None)
    if not frequency:
        frequency = "daily" if challenge.challenge_type in {"daily_check_in", "habit"} else "one_time"
    if frequency == "daily":
        return moment.strftime("%Y-%m-%d")
    if frequency == "weekly":
        iso_year, iso_week, _ = moment.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if frequency == "custom":
        days = challenge.custom_frequency_days or 1
        origin = challenge.starts_at or challenge.created_at or moment
        elapsed_days = max(0, (moment.date() - origin.date()).days)
        return f"custom-{elapsed_days // days}"
    return "once"


def member_can_participate(challenge, membership):
    if not membership:
        return False
    if challenge.participant_scope == "admins_moderators":
        return membership.role in {"owner", "admin", "moderator"}
    if challenge.participant_scope == "owners_admins":
        return membership.role in {"owner", "admin"}
    return True


def eligible_challenge_members(challenge):
    memberships = active_family_members_query(challenge.family_id).all()
    return [
        membership for membership in memberships
        if member_can_participate(challenge, membership)
        and not active_family_restriction(challenge.family_id, membership.user_id, "suspend")
    ]


def challenge_time_remaining(challenge):
    if not challenge.ends_at:
        return "No deadline"
    remaining = challenge.ends_at - datetime.utcnow()
    if remaining.total_seconds() <= 0:
        return "Ended"
    if remaining.days >= 1:
        return f"{remaining.days} day{'s' if remaining.days != 1 else ''} remaining"
    hours = max(1, int(remaining.total_seconds() // 3600))
    return f"{hours} hour{'s' if hours != 1 else ''} remaining"


def reward_for_challenge(challenge):
    values = challenge_reward_values()
    return values.get(challenge.reward_tier, values["easy"])


def parse_optional_int(value, minimum=0, maximum=100000):
    value = (value or "").strip()
    if not value:
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    if number < minimum:
        return None
    return min(number, maximum)


def challenge_is_current(challenge):
    now = datetime.utcnow()
    if challenge.status != "active":
        return False
    if challenge.starts_at and challenge.starts_at > now:
        return False
    if challenge.ends_at and challenge.ends_at < now:
        return False
    return True


def quiz_is_open(quiz):
    now = datetime.utcnow()
    if quiz.status != "open":
        return False
    if quiz.opens_at and quiz.opens_at > now:
        return False
    if quiz.closes_at and quiz.closes_at < now:
        return False
    return True


def family_supports_quizzes(family):
    return family.category in QUIZ_CAPABLE_CATEGORIES or family.category == "custom"


def challenge_dashboard(family, members, current_member):
    challenges = family.challenges.order_by(FamilyChallenge.created_at.desc()).all()
    can_manage = family_has_permission(current_member, "create_challenge")
    challenges = [
        challenge for challenge in challenges
        if (
            (challenge.visibility == "public" and family.privacy == "public")
            or (challenge.visibility == "family" and current_member is not None)
            or (challenge.visibility == "admins_only" and can_manage)
        )
    ]
    active_challenges = [challenge for challenge in challenges if challenge_is_current(challenge)]
    completions = ChallengeCompletion.query.join(FamilyChallenge).filter(
        FamilyChallenge.family_id == family.id
    ).all()
    completed_challenge_ids = {
        completion.challenge_id
        for completion in completions
        if completion.user_id == current_user.id
        and completion.period_key == challenge_completion_period(completion.challenge)
        and completion.verification_status == "completed"
    }
    pending_challenge_ids = {
        completion.challenge_id for completion in completions
        if completion.user_id == current_user.id
        and completion.period_key == challenge_completion_period(completion.challenge)
        and completion.verification_status == "pending"
    }
    completion_counts = {}
    member_points = {membership.user_id: 0 for membership in members}
    member_completed = {membership.user_id: 0 for membership in members}
    for completion in completions:
        if completion.verification_status == "completed":
            completion_counts[completion.challenge_id] = completion_counts.get(completion.challenge_id, 0) + 1
        if completion.user_id in member_points and completion.verification_status == "completed":
            member_points[completion.user_id] += completion.points_awarded or 0
            member_completed[completion.user_id] += 1
    progress_by_challenge = {}
    family_participant_count = 0
    family_completed_count = 0
    for challenge in active_challenges:
        eligible_members = eligible_challenge_members(challenge)
        eligible_by_user = {membership.user_id: membership for membership in eligible_members}
        if challenge.mandatory_all_members:
            participant_members = eligible_members
            joined_user_ids = set(eligible_by_user)
        else:
            joined_user_ids = {
                participant.user_id for participant in challenge.participants.all()
                if participant.user_id in eligible_by_user
            }
            participant_members = [eligible_by_user[user_id] for user_id in joined_user_ids]
        period_key = challenge_completion_period(challenge)
        period_completions = [
            completion for completion in completions
            if completion.challenge_id == challenge.id
            and completion.period_key == period_key
        ]
        completed_user_ids = {
            completion.user_id for completion in period_completions
            if completion.verification_status == "completed"
            and completion.user_id in joined_user_ids
        }
        pending_user_ids = {
            completion.user_id for completion in period_completions
            if completion.verification_status == "pending"
            and completion.user_id in joined_user_ids
        }
        def user_visible_in_stack(user):
            return (
                not user.is_hidden_from_directory
                or user.id == current_user.id
                or current_user.is_admin
            )
        completed_users = [
            eligible_by_user[user_id].user for user_id in completed_user_ids
            if user_visible_in_stack(eligible_by_user[user_id].user)
        ]
        working_users = [
            membership.user for membership in participant_members
            if membership.user_id not in completed_user_ids
            and user_visible_in_stack(membership.user)
        ]
        participant_count = len(participant_members)
        completed_count = len(completed_user_ids)
        percentage = round((completed_count / participant_count) * 100) if participant_count else 0
        current_joined = bool(
            current_member and (
                challenge.mandatory_all_members and current_member.user_id in eligible_by_user
                or current_member.user_id in joined_user_ids
            )
        )
        progress_by_challenge[challenge.id] = {
            "participant_count": participant_count,
            "completed_count": completed_count,
            "working_count": max(0, participant_count - completed_count),
            "percentage": percentage,
            "completed_users": completed_users,
            "working_users": working_users,
            "time_remaining": challenge_time_remaining(challenge),
            "current_joined": current_joined,
            "current_completed": bool(current_member and current_member.user_id in completed_user_ids),
            "current_pending": bool(current_member and current_member.user_id in pending_user_ids),
        }
        family_participant_count += participant_count
        family_completed_count += completed_count
    family_progress = (
        round((family_completed_count / family_participant_count) * 100)
        if family_participant_count else None
    )
    return {
        "challenges": challenges,
        "active_challenges": active_challenges,
        "completed_challenge_ids": completed_challenge_ids,
        "pending_challenge_ids": pending_challenge_ids,
        "completion_counts": completion_counts,
        "member_points": member_points,
        "member_completed": member_completed,
        "family_progress": family_progress,
        "family_participant_count": family_participant_count,
        "family_completed_count": family_completed_count,
        "progress_by_challenge": progress_by_challenge,
        "can_create_challenges": family_can_create(current_member, "create_challenge"),
        "can_manage_challenges": can_manage,
    }


def quiz_dashboard(family, members, current_member):
    quizzes = family.quizzes.order_by(Quiz.created_at.desc()).all()
    open_quizzes = [quiz for quiz in quizzes if quiz_is_open(quiz)]
    attempts = QuizAttempt.query.join(Quiz).filter(
        Quiz.family_id == family.id,
        QuizAttempt.submitted_at != None,
    ).all()
    quiz_points = {membership.user_id: 0 for membership in members}
    for attempt in attempts:
        if attempt.user_id in quiz_points:
            quiz_points[attempt.user_id] += attempt.score or 0
    quiz_history = [
        attempt
        for attempt in sorted(attempts, key=lambda item: item.submitted_at, reverse=True)
        if attempt.user_id == current_user.id
    ]
    leaderboard = sorted(
        [
            {
                "user": membership.user,
                "points": quiz_points.get(membership.user_id, 0),
            }
            for membership in members
        ],
        key=lambda item: item["points"],
        reverse=True,
    )
    leaderboard = [row for row in leaderboard if row["points"]]
    return {
        "quizzes": quizzes,
        "open_quizzes": open_quizzes,
        "quiz_history": quiz_history[:5],
        "quiz_leaderboard": leaderboard[:10],
        "supports_quizzes": family_supports_quizzes(family),
        "can_create_quizzes": bool(
            family_can_create(current_member, "create_quiz") and family_supports_quizzes(family)
        ),
        "can_review_quizzes": family_has_permission(current_member, "create_quiz"),
    }


def close_expired_family_polls(family):
    now = datetime.utcnow()
    expired_polls = FamilyPoll.query.filter(
        FamilyPoll.family_id == family.id,
        FamilyPoll.status == "open",
        FamilyPoll.closes_at.isnot(None),
        FamilyPoll.closes_at <= now,
    ).all()
    for poll in expired_polls:
        poll.status = "closed"
    if expired_polls:
        db.session.commit()


def poll_dashboard(family, current_member):
    close_expired_family_polls(family)
    polls = (
        FamilyPoll.query.filter_by(family_id=family.id)
        .order_by(FamilyPoll.created_at.desc())
        .limit(8)
        .all()
    )
    poll_rows = []
    for poll in polls:
        options = poll.options.order_by(FamilyPollOption.position.asc()).all()
        votes = poll.votes.all()
        option_totals = {}
        for vote in votes:
            option_totals[vote.option_id] = option_totals.get(vote.option_id, 0) + 1
        voter_ids = {vote.user_id for vote in votes}
        user_option_ids = {vote.option_id for vote in votes if current_member and vote.user_id == current_user.id}
        total_voters = len(voter_ids)
        eligible_voters = max(family.members.count(), 1)
        user_has_voted = bool(user_option_ids)
        show_results = (
            poll.status == "closed" or poll.results_visibility == "always"
            or (poll.results_visibility == "after_vote" and user_has_voted)
            or family_has_permission(current_member, "create_poll")
        )
        option_rows = []
        total_votes = sum(option_totals.values())
        percentage_base = total_votes if poll.allows_multiple_choices else total_voters
        for option in options:
            count = option_totals.get(option.id, 0)
            percentage = round((count / percentage_base) * 100) if percentage_base else 0
            option_rows.append(
                {
                    "option": option,
                    "votes": count,
                    "percentage": percentage,
                    "selected": option.id in user_option_ids,
                }
            )
        poll_rows.append(
            {
                "poll": poll,
                "options": option_rows,
                "total_voters": total_voters,
                "participation_percentage": min(100, round(total_voters / eligible_voters * 100)),
                "user_has_voted": user_has_voted,
                "show_results": show_results,
                "is_open": poll.status == "open",
                "can_close": bool(
                    current_member
                    and poll.status == "open"
                    and (
                        poll.creator_id == current_user.id
                        or family_has_permission(current_member, "create_poll")
                    )
                ),
            }
        )
    return {
        "poll_rows": poll_rows,
        "can_create_polls": family_can_create(current_member, "create_poll"),
    }


def badges_for_member(membership, stats, top_quiz_score):
    badges = []
    if stats["encouragements"] >= 3:
        badges.append("Encourager")
    if stats["weekly_activity"] >= 3:
        badges.append("Consistent Member")
    if stats["posts"] >= 2 or stats["chat_messages"] >= 10:
        badges.append("Helpful Contributor")
    if stats["completed_challenges"] >= 3:
        badges.append("Challenge Finisher")
    if stats["quiz_points"] > 0 and stats["quiz_points"] == top_quiz_score:
        badges.append("Quiz Champion")
    if family_role(membership) in {"owner", "admin"}:
        badges.append("Family Builder")
    return badges


def family_home_dashboard(family, members, current_member):
    now = datetime.utcnow()
    week_start = now - timedelta(days=7)
    can_manage_challenges = family_has_permission(current_member, "create_challenge")
    active_challenges = [
        challenge for challenge in family.challenges.all()
        if challenge_is_current(challenge)
        and (
            (challenge.visibility == "public" and family.privacy == "public")
            or (challenge.visibility == "family" and current_member is not None)
            or (challenge.visibility == "admins_only" and can_manage_challenges)
        )
    ]
    challenge_ids = [challenge.id for challenge in family.challenges.all()]
    active_challenge_ids = {challenge.id for challenge in active_challenges}
    completions = (
        ChallengeCompletion.query.filter(
            ChallengeCompletion.challenge_id.in_(challenge_ids),
            ChallengeCompletion.verification_status == "completed",
        ).all()
        if challenge_ids
        else []
    )
    attempts = QuizAttempt.query.join(Quiz).filter(
        Quiz.family_id == family.id,
        QuizAttempt.submitted_at != None,
    ).all()
    weekly_quiz_attempts = [attempt for attempt in attempts if attempt.submitted_at and attempt.submitted_at >= week_start]
    weekly_quiz_highlight = None
    if weekly_quiz_attempts:
        best = max(weekly_quiz_attempts, key=lambda item: (item.percentage or 0, item.score or 0))
        weekly_quiz_highlight = {
            "attempts": len(weekly_quiz_attempts),
            "participants": len({item.user_id for item in weekly_quiz_attempts}),
            "passes": sum(1 for item in weekly_quiz_attempts if item.passed),
            "best": best,
        }
    family_posts = (
        family.posts.filter(Post.is_hidden == False)
        .order_by(Post.created_at.desc())
        .all()
    )
    family_messages = (
        Message.query.filter_by(family_id=family.id)
        .order_by(Message.created_at.desc())
        .limit(8)
        .all()
    )
    all_family_messages = Message.query.filter_by(family_id=family.id).all()
    upcoming_quiz = (
        family.quizzes.filter(Quiz.status == "open")
        .order_by(Quiz.opens_at.asc().nullsfirst(), Quiz.created_at.asc())
        .first()
        if family_supports_quizzes(family)
        else None
    )
    stats_by_user = {
        membership.user_id: {
            "completed_challenges": 0,
            "pending_challenges": len(active_challenges),
            "challenge_points": 0,
            "quiz_points": 0,
            "posts": 0,
            "chat_messages": 0,
            "weekly_activity": 0,
            "encouragements": 0,
        }
        for membership in members
    }
    completed_active_by_user = {membership.user_id: set() for membership in members}
    weekly_achievements = []
    for completion in completions:
        if completion.user_id not in stats_by_user:
            continue
        stats = stats_by_user[completion.user_id]
        stats["completed_challenges"] += 1
        stats["challenge_points"] += completion.points_awarded or 0
        if completion.challenge_id in active_challenge_ids:
            completed_active_by_user[completion.user_id].add(completion.challenge_id)
        if completion.completed_at and completion.completed_at >= week_start:
            stats["weekly_activity"] += 1
            weekly_achievements.append(
                {
                    "user": completion.user,
                    "label": f"completed {completion.challenge.title}",
                    "created_at": completion.completed_at,
                }
            )
    for attempt in attempts:
        if attempt.user_id not in stats_by_user:
            continue
        stats = stats_by_user[attempt.user_id]
        stats["quiz_points"] += attempt.score or 0
        if attempt.submitted_at and attempt.submitted_at >= week_start:
            stats["weekly_activity"] += 1
            weekly_achievements.append(
                {
                    "user": attempt.user,
                    "label": f"scored {attempt.score} points on {attempt.quiz.title}",
                    "created_at": attempt.submitted_at,
                }
            )
    for post in family_posts:
        if post.user_id not in stats_by_user:
            continue
        stats_by_user[post.user_id]["posts"] += 1
        stats_by_user[post.user_id]["encouragements"] += 1
        if post.created_at and post.created_at >= week_start:
            stats_by_user[post.user_id]["weekly_activity"] += 1
            weekly_achievements.append(
                {
                    "user": post.author,
                    "label": "shared a Family post",
                    "created_at": post.created_at,
                }
            )
    for message in all_family_messages:
        if message.sender_id not in stats_by_user:
            continue
        stats_by_user[message.sender_id]["chat_messages"] += 1
        if message.created_at and message.created_at >= week_start:
            stats_by_user[message.sender_id]["weekly_activity"] += 1
    top_quiz_score = max((stats["quiz_points"] for stats in stats_by_user.values()), default=0)
    member_progress = []
    for membership in members:
        stats = stats_by_user[membership.user_id]
        stats["pending_challenges"] = max(
            len(active_challenges) - len(completed_active_by_user[membership.user_id]),
            0,
        )
        progress_percent = None
        if active_challenges:
            progress_percent = round(
                (len(completed_active_by_user[membership.user_id]) / len(active_challenges)) * 100
            )
        total_points = stats["challenge_points"] + stats["quiz_points"]
        member_progress.append(
            {
                "membership": membership,
                "completed_challenges": stats["completed_challenges"],
                "pending_challenges": stats["pending_challenges"],
                "challenge_points": stats["challenge_points"],
                "quiz_points": stats["quiz_points"],
                "total_points": total_points,
                "progress_percent": progress_percent,
                "badges": badges_for_member(membership, stats, top_quiz_score),
            }
        )
    member_progress.sort(key=lambda row: row["total_points"], reverse=True)
    weekly_achievements.sort(key=lambda row: row["created_at"], reverse=True)
    hour = (datetime.utcnow() + timedelta(hours=3)).hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_activity = sum(
        1 for item in weekly_achievements if item["created_at"] and item["created_at"] >= today_start
    ) + sum(1 for message in family_messages if message.created_at and message.created_at >= today_start)
    needs_encouragement = next(
        (
            post for post in family_posts
            if post.user_id != current_user.id
            and post.comments.count() == 0
            and post.reactions.count() == 0
        ),
        None,
    )
    new_members = sorted(
        [membership for membership in members if membership.joined_at and membership.joined_at >= week_start],
        key=lambda membership: membership.joined_at,
        reverse=True,
    )[:5]
    top_supporter = next(
        (row for row in member_progress if row["total_points"] or row["completed_challenges"]),
        member_progress[0] if member_progress else None,
    )
    active_campaign = FamilyContributionCampaign.query.filter(
        FamilyContributionCampaign.family_id == family.id,
        FamilyContributionCampaign.status.in_(["active", "reached"]),
    ).order_by(FamilyContributionCampaign.created_at.desc()).first()
    campaign_summary = None
    if active_campaign:
        contributed = campaign_contributed_points(active_campaign)
        available = family_point_balance(family.id)
        campaign_summary = {
            "campaign": active_campaign,
            "upgrade": upgrade_definition(active_campaign.upgrade_key) or {},
            "progress": min(100, round(((available + contributed) / active_campaign.points_required) * 100)),
            "remaining": max(0, active_campaign.points_required - available - contributed),
        }
    return {
        "dashboard_greeting": greeting,
        "dashboard_today_activity": today_activity,
        "dashboard_active_challenges": active_challenges[:5],
        "dashboard_member_progress": member_progress,
        "dashboard_recent_posts": family_posts[:4],
        "dashboard_recent_messages": family_messages[:5],
        "dashboard_upcoming_quiz": upcoming_quiz,
        "dashboard_weekly_achievements": weekly_achievements[:6],
        "dashboard_needs_encouragement": needs_encouragement,
        "dashboard_new_members": new_members,
        "dashboard_top_supporter": top_supporter,
        "dashboard_campaign": campaign_summary,
        "dashboard_quiz_highlight": weekly_quiz_highlight,
    }


TIMELINE_FILTERS = {"all", "challenges", "goals", "members", "upgrades"}


def family_activity_timeline(family, current_member, is_super_admin=False):
    """Build a quiet, privacy-aware timeline from existing authoritative records."""
    selected_filter = request.args.get("activity", "all").strip().lower()
    if selected_filter not in TIMELINE_FILTERS:
        selected_filter = "all"
    if not current_member and not is_super_admin:
        return {"family_activity_events": [], "family_activity_filter": selected_filter,
                "family_activity_visible": False}

    events = []

    def add(category, title, detail, created_at, icon="✦", major=False):
        if created_at:
            events.append({"category": category, "title": title, "detail": detail,
                           "created_at": created_at, "icon": icon, "major": major})

    for membership in family.members.order_by(FamilyMember.joined_at.desc()).limit(30).all():
        add("members", "Member joined", f"{membership.user.username} joined the Family.",
            membership.joined_at, "+")

    participants = ChallengeParticipant.query.join(FamilyChallenge).filter(
        FamilyChallenge.family_id == family.id
    ).order_by(ChallengeParticipant.joined_at.desc()).limit(80).all()
    joined_groups = {}
    for participant in participants:
        key = participant.joined_at.date() if participant.joined_at else None
        if key:
            joined_groups.setdefault(key, []).append(participant)
    for day, rows in joined_groups.items():
        label = "today" if day == datetime.utcnow().date() else day.strftime("%b %d")
        add("challenges", "Challenge participation",
            f"{len(rows)} {'member' if len(rows) == 1 else 'members'} joined challenges {label}.",
            max(row.joined_at for row in rows), "✓")

    completions = ChallengeCompletion.query.join(FamilyChallenge).filter(
        FamilyChallenge.family_id == family.id,
        ChallengeCompletion.verification_status == "completed",
    ).order_by(ChallengeCompletion.completed_at.desc()).limit(120).all()
    completion_groups = {}
    for completion in completions:
        key = completion.completed_at.date() if completion.completed_at else None
        if key:
            completion_groups.setdefault(key, []).append(completion)
    for day, rows in completion_groups.items():
        label = "today" if day == datetime.utcnow().date() else f"on {day.strftime('%b %d')}"
        add("challenges", "Challenges completed",
            f"{len(rows)} {'member completed a challenge' if len(rows) == 1 else 'members completed challenges'} {label}.",
            max(row.completed_at for row in rows), "★", len(rows) >= 5)

    attempts = QuizAttempt.query.join(Quiz).filter(
        Quiz.family_id == family.id, QuizAttempt.submitted_at != None
    ).order_by(QuizAttempt.submitted_at.desc()).limit(30).all()
    for attempt in attempts:
        add("challenges", "Quiz completed",
            f"{attempt.user.username} completed {attempt.quiz.title}.", attempt.submitted_at, "?")

    for poll in FamilyPoll.query.filter_by(family_id=family.id).order_by(FamilyPoll.created_at.desc()).limit(20):
        add("goals", "Poll created", poll.question, poll.created_at, "◉")

    achievement_labels = {
        "goal_achieved": ("goals", "Family goal achieved", "◎"),
        "family_level_increased": ("upgrades", "Family level increased", "✦"),
        "weekly_family_milestone": ("goals", "Family milestone reached", "★"),
        "encouragement_milestone": ("goals", "Encouragement milestone reached", "♡"),
    }
    achievements = Post.query.filter(
        Post.family_id == family.id,
        Post.post_type == "achievement",
        Post.achievement_type.in_(list(achievement_labels)),
    ).order_by(Post.created_at.desc()).limit(30).all()
    for post in achievements:
        category, title, icon = achievement_labels[post.achievement_type]
        add(category, title, post.content or post.encouraging_message or "A major Family moment.",
            post.created_at, icon, True)

    campaigns = FamilyContributionCampaign.query.filter_by(family_id=family.id).order_by(
        FamilyContributionCampaign.created_at.desc()).limit(20).all()
    for campaign in campaigns:
        upgrade = upgrade_definition(campaign.upgrade_key) or {}
        add("upgrades", "Upgrade campaign started",
            f"The Family began working toward {upgrade.get('name', 'an upgrade')}.",
            campaign.created_at, "◇")
        for contribution in campaign.contributions.filter_by(refunded=False).order_by(
            FamilyCampaignContribution.created_at.desc()).limit(30).all():
            contributor = contribution.user.username if contribution.user else "A member"
            add("upgrades", "Points contributed",
                f"{contributor} contributed {contribution.amount} points toward the shared upgrade.",
                contribution.created_at, "♡")
        if campaign.highest_milestone:
            add("upgrades", "Campaign milestone reached",
                f"The campaign reached {campaign.highest_milestone}% of its journey.",
                campaign.activated_at or campaign.created_at, "★", campaign.highest_milestone == 100)

    for purchase in family.upgrade_purchases.order_by(FamilyUpgradePurchase.purchased_at.desc()).limit(20):
        upgrade = upgrade_definition(purchase.upgrade_key) or {}
        add("upgrades", "Family upgrade unlocked",
            f"Together, the Family unlocked {upgrade.get('name', 'a new upgrade')}.",
            purchase.purchased_at, "◆", True)

    for log in family.moderation_logs.filter_by(action="goal_progress_updated").order_by(
        FamilyModerationLog.created_at.desc()).limit(20).all():
        add("goals", "Goal progress updated", log.reason or "The shared Family goal was updated.",
            log.created_at, "◎")

    goal_events = GoalActivity.query.join(Goal).filter(Goal.family_id == family.id).order_by(
        GoalActivity.created_at.desc()).limit(40).all()
    for goal_event in goal_events:
        title = {
            "progress": "Goal progress updated", "milestone": "Goal milestone achieved",
            "completed": "Family goal achieved", "participant_joined": "Member joined a goal",
            "created": "Family goal created",
        }.get(goal_event.event_type, "Family goal updated")
        add("goals", title, goal_event.message, goal_event.created_at, "◎",
            goal_event.event_type in {"milestone", "completed"})

    events.sort(key=lambda event: event["created_at"], reverse=True)
    if selected_filter != "all":
        events = [event for event in events if event["category"] == selected_filter]
    return {"family_activity_events": events[:60], "family_activity_filter": selected_filter,
            "family_activity_visible": True}


def validate_family_payload(form):
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    category = form.get("category", "friendship_and_support").strip()
    custom_category = form.get("custom_category", "").strip()
    goal_title = form.get("goal_title", "").strip()
    goal_description = form.get("goal_description", "").strip()
    privacy = form.get("privacy", "public").strip()
    member_limit_raw = form.get("member_limit", "").strip()
    start_date = parse_family_date(form.get("start_date"))
    target_date = parse_family_date(form.get("target_date"))

    if category not in FAMILY_CATEGORIES:
        return None, "Choose a valid Family type."
    if category == "custom" and len(custom_category) < 3:
        return None, "Add a custom Family type with at least 3 characters."
    if category != "custom":
        custom_category = ""
    if privacy not in FAMILY_PRIVACY_OPTIONS:
        privacy = "public"
    if not name:
        return None, "Family name is required."
    if not goal_title:
        return None, "Add a shared goal for this Family."
    member_limit = default_family_member_limit()
    if member_limit_raw:
        try:
            member_limit = int(member_limit_raw)
        except ValueError:
            return None, "Member limit must be a number."
        if member_limit < 2:
            return None, "Member limit must allow at least 2 members."
    return (
        {
            "name": name,
            "description": description,
            "category": category,
            "custom_category": custom_category,
            "goal_title": goal_title,
            "goal_description": goal_description,
            "start_date": start_date,
            "target_date": target_date,
            "privacy": privacy,
            "member_limit": member_limit,
        },
        None,
    )


@family_bp.route("/families")
@login_required
def families():
    query = request.args.get("q", "").strip()
    family_query = Family.query
    if query:
        search = f"%{query}%"
        family_query = family_query.filter(
            or_(
                Family.name.ilike(search),
                Family.description.ilike(search),
                Family.goal_title.ilike(search),
            )
        )
    families = family_query.order_by(Family.created_at.desc()).limit(60).all()
    capacity_by_family = {family.id: family_capacity_status(family) for family in families}
    joined_family_ids = [membership.family_id for membership in current_user.family_memberships]
    recommended_families = Family.query.filter(
        Family.is_active.is_(True),
        Family.privacy == "public",
        ~Family.id.in_(joined_family_ids or [-1]),
    ).order_by(Family.created_at.desc()).limit(6).all()
    for family in recommended_families:
        capacity_by_family.setdefault(family.id, family_capacity_status(family))
    return render_template(
        "families.html",
        families=families,
        query=query,
        capacity_by_family=capacity_by_family,
        recommended_families=recommended_families,
    )


@family_bp.route("/family/create", methods=["GET", "POST"])
@login_required
def create_family():
    if request.method == "POST":
        payload, error = validate_family_payload(request.form)
        if error:
            flash(error, "warning")
            return render_template("create_family.html", **family_form_context(form=request.form))
        payload["member_limit"] = 50
        family = Family(
            **payload,
            owner_id=current_user.id,
            is_active=True,
        )
        db.session.add(family)
        db.session.commit()
        member = FamilyMember(
            family_id=family.id, user_id=current_user.id, role="owner"
        )
        db.session.add(member)
        db.session.commit()
        flash("Family created successfully.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id))
    return render_template("create_family.html", **family_form_context())


@family_bp.route("/family/<int:family_id>")
@login_required
def family_detail(family_id):
    family = Family.query.get_or_404(family_id)
    member = FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()
    is_super_admin = is_platform_owner(current_user)
    if family.privacy != "public" and not member and not is_super_admin:
        flash("This Family is private. Join through an invitation to enter.", "warning")
        return redirect(url_for("family.families"))
    members = FamilyMember.query.filter_by(family_id=family.id).all()
    encouragement_checkin_count = 0
    if is_feature_enabled("daily_checkins") and member:
        member_ids = [membership.user_id for membership in members]
        today = (datetime.utcnow() + timedelta(hours=3)).date()
        encouragement_checkin_count = DailyCheckIn.query.filter(
            DailyCheckIn.user_id.in_(member_ids),
            DailyCheckIn.checkin_date == today,
            DailyCheckIn.mood.in_(["worried", "struggling"]),
            or_(
                (DailyCheckIn.privacy == "family") & (DailyCheckIn.family_id == family.id),
                DailyCheckIn.privacy == "all_families",
                DailyCheckIn.privacy == "public",
            ),
        ).count()
    capacity = family_capacity_status(family)
    posts = (
        family.posts.filter(or_(Post.is_hidden == False, Post.user_id == current_user.id))
        .order_by(Post.created_at.desc())
        .all()
    )
    if family.is_active and is_feature_enabled("weekly_reports") and (member or is_super_admin):
        ensure_weekly_report_ready(family)
    if family.is_active and member and is_feature_enabled("enhanced_notifications"):
        now = datetime.utcnow()
        reminder_window = now + timedelta(hours=24)
        ending_challenges = FamilyChallenge.query.filter(
            FamilyChallenge.family_id == family.id, FamilyChallenge.status == "active",
            FamilyChallenge.ends_at > now, FamilyChallenge.ends_at <= reminder_window,
        ).limit(5).all()
        for challenge in ending_challenges:
            completed = ChallengeCompletion.query.filter_by(
                challenge_id=challenge.id, user_id=current_user.id,
                period_key=challenge_completion_period(challenge), verification_status="completed",
            ).first()
            if not completed:
                smart_notify(
                    user_id=current_user.id, category="challenge_reminder",
                    message=f"{challenge.title} in {family.name} ends within 24 hours. Join in if it feels right for you.",
                    action_url=url_for("family.family_detail", family_id=family.id) + f"#challenge-{challenge.id}",
                    group_key=f"challenge-reminder:{challenge.id}:{current_user.id}",
                    dedupe_key=f"challenge-reminder:{challenge.id}:{current_user.id}:{challenge.ends_at.date().isoformat()}",
                    reminder=True,
                )
        if ending_challenges:
            db.session.commit()
    return render_family_detail_page(
        family, member, members, capacity, posts, is_super_admin,
        encouragement_checkin_count,
    )


@family_bp.route("/family/<int:family_id>/memories")
@login_required
def family_memories(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    is_super_admin = is_platform_owner(current_user)
    if not member and not is_super_admin:
        abort(403)

    memories = []
    today = datetime.utcnow().date()
    if family.created_at:
        years = today.year - family.created_at.year
        if years > 0 and (family.created_at.month, family.created_at.day) == (today.month, today.day):
            memories.append({
                "date": family.created_at,
                "text": f"{family.name} began {years} year{'s' if years != 1 else ''} ago today.",
            })

    memberships = family.members.order_by(FamilyMember.joined_at.asc()).all()
    if len(memberships) >= 50 and memberships[49].joined_at:
        memories.append({"date": memberships[49].joined_at, "text": f"{family.name} welcomed its 50th member."})

    completions = (
        ChallengeCompletion.query.join(FamilyChallenge)
        .filter(FamilyChallenge.family_id == family.id, ChallengeCompletion.verification_status == "completed")
        .order_by(ChallengeCompletion.completed_at.asc())
        .all()
    )
    if len(completions) >= 100 and completions[99].completed_at:
        memories.append({"date": completions[99].completed_at, "text": f"{family.name} reached 100 challenge completions."})

    first_goal = Goal.query.filter_by(family_id=family.id, scope="family", status="completed").order_by(Goal.completed_at.asc()).first()
    if first_goal and first_goal.completed_at:
        memories.append({"date": first_goal.completed_at, "text": f"{family.name} completed its first shared goal: {first_goal.title}."})

    memories.sort(key=lambda item: item["date"], reverse=True)
    return render_template("family_memories.html", family=family, memories=memories)


def ensure_weekly_report_ready(family):
    try:
        report, _ = get_or_create_weekly_report(family)
        if report.notified_at is None:
            report_url = url_for("family.weekly_family_report", family_id=family.id, report_id=report.id)
            for membership in family.members:
                add_family_notification(
                    membership.user_id, "weekly_family_report",
                    f"Your weekly growth report for {family.name} is ready.", report_url,
                )
            report.notified_at = datetime.utcnow()
        db.session.commit()
        return report
    except IntegrityError:
        db.session.rollback()
        return FamilyWeeklyReport.query.filter_by(
            family_id=family.id,
        ).order_by(FamilyWeeklyReport.week_start.desc()).first()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("weekly_family_report_generation_failed family_id=%s", family.id)
        return None


@family_bp.route("/family/<int:family_id>/weekly-report")
@login_required
@feature_required("weekly_reports")
def latest_weekly_family_report(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    is_super_admin = is_platform_owner(current_user)
    if not member and not is_super_admin:
        flash("Weekly reports are private to Family members.", "warning")
        return redirect(url_for("family.families"))
    report = ensure_weekly_report_ready(family)
    if not report:
        flash("The weekly report could not be prepared yet. Please try again shortly.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    return redirect(url_for("family.weekly_family_report", family_id=family.id, report_id=report.id))


@family_bp.route("/family/<int:family_id>/weekly-report/<int:report_id>")
@login_required
@feature_required("weekly_reports")
def weekly_family_report(family_id, report_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    is_super_admin = is_platform_owner(current_user)
    if not member and not is_super_admin:
        flash("Weekly reports are private to Family members.", "warning")
        return redirect(url_for("family.families"))
    report = FamilyWeeklyReport.query.filter_by(id=report_id, family_id=family.id).first_or_404()
    return render_template(
        "family_weekly_report.html", family=family, report=report,
        data=report.snapshot, can_publish=family_has_permission(member, "create_poll"),
    )


@family_bp.route("/family/<int:family_id>/weekly-report/<int:report_id>/publish", methods=["POST"])
@login_required
@feature_required("weekly_reports")
def publish_weekly_family_report(family_id, report_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "create_poll"):
        flash("Only Family owners and admins can publish weekly reports.", "danger")
        return redirect(url_for("family.weekly_family_report", family_id=family.id, report_id=report_id))
    report = FamilyWeeklyReport.query.filter_by(
        id=report_id, family_id=family.id
    ).with_for_update().first_or_404()
    if report.published_post_id:
        flash("This weekly report is already in the Family feed.", "info")
        return redirect(url_for("main.post_detail", post_id=report.published_post_id))
    post = Post(
        user_id=current_user.id, family_id=family.id,
        content=report_post_content(family, report), media_type="text",
        audience="family", post_type="weekly_report",
        encouraging_message="Every contribution mattered this week.",
    )
    db.session.add(post)
    db.session.flush()
    report.published_post_id = post.id
    report.published_by_id = current_user.id
    report.published_at = datetime.utcnow()
    log_family_action(family, "weekly_report_published", reason=f"Week of {report.week_start.isoformat()}")
    db.session.commit()
    flash("The weekly report was shared with the Family.", "success")
    return redirect(url_for("main.post_detail", post_id=post.id))


def encouragement_request_visible(item, member):
    if not member or item.status != "active":
        return False
    if item.user_id == current_user.id:
        return True
    if item.visibility == "admins":
        return family_role(member) in {"owner", "admin"}
    return True


@family_bp.route("/family/<int:family_id>/encouragement", methods=["GET", "POST"])
@login_required
@feature_required("anonymous_support_posts")
def family_encouragement(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member:
        flash("Join this Family before using encouragement requests.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if request.method == "POST":
        category = request.form.get("category", "").strip()
        content = request.form.get("content", "").strip()
        visibility = request.form.get("visibility", "identity").strip()
        if category not in ENCOURAGEMENT_CATEGORIES:
            flash("Choose an encouragement category.", "warning")
            return redirect(request.url)
        if visibility not in {"identity", "anonymous", "admins"}:
            visibility = "identity"
        if len(content) < 10 or len(content) > 3000:
            flash("Share between 10 and 3,000 characters so others can respond thoughtfully.", "warning")
            return redirect(request.url)
        lowered = content.casefold()
        item = EncouragementRequest(
            family_id=family.id, user_id=current_user.id, category=category,
            content=content, visibility=visibility,
            needs_crisis_guidance=any(phrase in lowered for phrase in CRISIS_PHRASES),
        )
        db.session.add(item)
        db.session.flush()
        request_url = url_for("family.family_encouragement", family_id=family.id) + f"#encouragement-{item.id}"
        for recipient in family.members:
            if recipient.user_id == current_user.id:
                continue
            if visibility == "admins" and recipient.role not in {"owner", "admin"}:
                continue
            requester = current_user.username if visibility == "identity" else "Someone"
            smart_notify(
                user_id=recipient.user_id, category="encouragement",
                message=f"{requester} in {family.name} requested encouragement: {ENCOURAGEMENT_CATEGORIES[category]}.",
                action_url=request_url, group_key=f"encouragement:{family.id}",
                dedupe_key=f"encouragement:{item.id}:{recipient.user_id}",
            )
        db.session.commit()
        flash(
            "Your request was shared. If you may be in immediate danger, please also contact emergency help now."
            if item.needs_crisis_guidance else "Your encouragement request was shared with care.",
            "warning" if item.needs_crisis_guidance else "success",
        )
        return redirect(url_for("family.family_encouragement", family_id=family.id))
    items = EncouragementRequest.query.filter_by(family_id=family.id, status="active").order_by(
        EncouragementRequest.created_at.desc()).limit(80).all()
    items = [item for item in items if encouragement_request_visible(item, member)]
    return render_template(
        "family_encouragement.html", family=family, member=member, items=items,
        categories=ENCOURAGEMENT_CATEGORIES, reactions=ENCOURAGEMENT_REACTIONS,
        can_review_identity=family_has_permission(member, "warn_members"),
    )


@family_bp.route("/family/<int:family_id>/encouragement/<int:request_id>/respond", methods=["POST"])
@login_required
@feature_required("anonymous_support_posts")
def respond_to_encouragement(family_id, request_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    item = EncouragementRequest.query.filter_by(id=request_id, family_id=family.id).first_or_404()
    if not encouragement_request_visible(item, member):
        abort(403)
    reaction = request.form.get("reaction", "").strip()
    comment = request.form.get("comment", "").strip()
    if reaction not in ENCOURAGEMENT_REACTIONS or len(comment) > 1000:
        flash("Choose a supportive response and keep comments under 1,000 characters.", "warning")
        return redirect(url_for("family.family_encouragement", family_id=family.id))
    response = EncouragementResponse.query.filter_by(request_id=item.id, user_id=current_user.id).first()
    is_new = response is None
    if response:
        response.reaction, response.comment = reaction, comment
    else:
        response = EncouragementResponse(
            request_id=item.id, user_id=current_user.id, reaction=reaction, comment=comment)
        db.session.add(response)
    if is_new and item.user_id != current_user.id:
        add_family_notification(
            item.user_id, "encouragement_response",
            f"Someone in {family.name} responded supportively to your request.",
            url_for("family.family_encouragement", family_id=family.id),
        )
        encouraged_name = item.requester.username if item.visibility == "identity" else "someone"
        add_family_notification(
            current_user.id, "encouragement_response",
            f"You encouraged {encouraged_name} today.",
            url_for("main.impact"),
            important=False,
        )
    if len(comment) >= 10:
        db.session.flush()
        record_streak_activity(
            current_user, "encouragement", source_type="encouragement_response",
            source_id=response.id,
            unique_key=f"encouragement-response:{item.id}:{current_user.id}",
        )
    db.session.commit()
    flash("Your support was shared.", "success")
    return redirect(url_for("family.family_encouragement", family_id=family.id))


@family_bp.route("/family/<int:family_id>/encouragement/<int:response_id>/thank", methods=["POST"])
@login_required
@feature_required("anonymous_support_posts")
def thank_encouragement_response(family_id, response_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    response = EncouragementResponse.query.get_or_404(response_id)
    if not member or response.request.family_id != family.id or response.request.user_id != current_user.id:
        abort(403)
    if response.user_id == current_user.id:
        abort(400)
    message_key = request.form.get("message_key", "").strip()
    if message_key not in APPRECIATION_MESSAGES:
        flash("Choose a thank-you message.", "warning")
        return redirect(url_for("family.family_encouragement", family_id=family.id))
    appreciation = Appreciation.query.filter_by(response_id=response.id, sender_id=current_user.id).first()
    is_new = appreciation is None
    if appreciation:
        appreciation.message_key = message_key
    else:
        appreciation = Appreciation(response_id=response.id, sender_id=current_user.id, recipient_id=response.user_id, message_key=message_key)
        db.session.add(appreciation)
    db.session.flush()
    if is_new:
        add_family_notification(
            response.user_id,
            "appreciation",
            "You made someone's day better.",
            url_for("main.impact"),
        )
    db.session.commit()
    flash("Your appreciation was shared.", "success")
    return redirect(url_for("family.family_encouragement", family_id=family.id))


@family_bp.route("/family/<int:family_id>/encouragement/<int:request_id>/report", methods=["POST"])
@login_required
@feature_required("anonymous_support_posts")
def report_encouragement(family_id, request_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    item = EncouragementRequest.query.filter_by(id=request_id, family_id=family.id).first_or_404()
    if not encouragement_request_visible(item, member):
        abort(403)
    reason = request.form.get("reason", "").strip()
    if len(reason) < 5 or len(reason) > 500:
        flash("Please provide a brief report reason.", "warning")
        return redirect(url_for("family.family_encouragement", family_id=family.id))
    existing = EncouragementRequestReport.query.filter_by(request_id=item.id, reporter_id=current_user.id).first()
    if existing:
        flash("You already reported this request for review.", "info")
    else:
        db.session.add(EncouragementRequestReport(request_id=item.id, reporter_id=current_user.id, reason=reason))
        db.session.commit()
        flash("The request was sent to website moderators.", "success")
    return redirect(url_for("family.family_encouragement", family_id=family.id))


def render_family_detail_page(
    family, member, members, capacity, posts, is_super_admin,
    encouragement_checkin_count,
):
    family_level = {
        "level": 1, "name": "Seed", "lifetime_xp": 0, "available_points": 0,
        "progress_percent": 0, "xp_to_next": 100, "challenges_completed": 0,
        "goals_achieved": 0, "encouragement_milestones": 0, "age_days": 0,
    }
    has_gallery = False
    has_advanced_statistics = False
    has_custom_badge_frame = False
    try:
        family_level = family_level_summary(family)
        if is_feature_enabled("family_upgrades"):
            has_gallery = family_has_upgrade(family.id, "family_gallery")
            has_advanced_statistics = family_has_upgrade(family.id, "advanced_statistics")
            has_custom_badge_frame = family_has_upgrade(family.id, "custom_badge_frame")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "optional_family_features_unavailable family_id=%s", family.id
        )
    return render_template(
        "family_detail.html",
        family=family,
        Goal=Goal,
        member=member,
        members=members,
        posts=posts,
        categories=FAMILY_CATEGORIES,
        challenge_types={
            key: label
            for key, label in CHALLENGE_TYPES.items()
            if key != "daily_check_in" or is_feature_enabled("daily_checkins")
        },
        reward_tiers=REWARD_TIER_LABELS,
        reward_values=challenge_reward_values(),
        allowed_reward_tiers=CHALLENGE_ALLOWED_REWARD_TIERS,
        member_can_participate=member_can_participate,
        upload_limits={
            "photo": get_upload_limit("image") // (1024 * 1024),
            "video": get_upload_limit("video") // (1024 * 1024),
            "audio": get_upload_limit("audio") // (1024 * 1024),
            "file": get_upload_limit("file") // (1024 * 1024),
        },
        role_labels=FAMILY_ROLE_LABELS,
        can_edit_family=family_has_permission(member, "edit_family"),
        can_change_family_image=family_has_permission(member, "change_family_image"),
        can_manage_roles=family_has_permission(member, "manage_roles"),
        can_manage_members=family_has_permission(member, "manage_members"),
        can_manage_creation_permissions=family_has_permission(member, "manage_members"),
        can_warn_members=family_has_permission(member, "warn_members"),
        can_suspend_members=family_has_permission(member, "suspend_members"),
        can_invite_members=family_has_permission(member, "invite_members"),
        can_activate_upgrades=family_has_permission(member, "activate_upgrade"),
        is_super_admin_view=is_super_admin,
        encouragement_checkin_count=encouragement_checkin_count,
        has_family_gallery=has_gallery,
        has_advanced_statistics=has_advanced_statistics,
        has_custom_badge_frame=has_custom_badge_frame,
        active_member_count=capacity["member_count"],
        effective_member_limit=capacity["member_limit"],
        effective_member_limit_label=capacity["member_limit_label"],
        family_is_full=capacity["is_full"],
        remaining_slots=capacity["remaining_slots"],
        family_level=family_level,
        **family_activity_timeline(family, member, is_super_admin),
        **family_home_dashboard(family, members, member),
        **poll_dashboard(family, member),
        **challenge_dashboard(family, members, member),
        **quiz_dashboard(family, members, member),
    )


@family_bp.route("/family/<int:family_id>/upgrades")
@login_required
def family_upgrades(family_id):
    if not is_feature_enabled("family_upgrades"):
        flash("Family upgrades are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member:
        flash("Join this Family before viewing its upgrades.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    from premium import family_has_premium
    family_points_enabled = is_feature_enabled("family_points")
    premium_active = family_has_premium(family)
    if not family_points_enabled and not premium_active:
        abort(404)
    purchased = purchased_upgrade_keys(family.id)
    entitled = {
        key for key, definition in configured_upgrade_catalog().items()
        if definition.get("implemented", True) and family_has_upgrade(family.id, key)
    }
    current_capacity = effective_family_member_limit(family)
    next_capacity = next_capacity_target(current_capacity)
    current_family_level = family_level_summary(family)["level"]
    catalog = []
    catalog_source = configured_upgrade_catalog()
    for key, definition in catalog_source.items():
        item = {"key": key, **definition}
        capacity = definition.get("capacity")
        item["unlocked"] = family_has_upgrade(family.id, key) or bool(capacity and current_capacity >= capacity)
        item["level_ready"] = current_family_level >= definition.get("required_level", 1)
        item["campaign_available"] = not item["unlocked"] and upgrade_can_be_targeted(family, key)
        item["available"] = not item["unlocked"] and item["level_ready"] and (
            capacity is None or capacity == next_capacity
        ) and definition.get("implemented", True)
        if definition.get("implemented", True) and (family_points_enabled or item["unlocked"]):
            catalog.append(item)
    purchases = FamilyUpgradePurchase.query.filter_by(family_id=family.id).order_by(
        FamilyUpgradePurchase.purchased_at.desc()
    ).all()
    active_campaign = None
    if family_points_enabled and is_feature_enabled("contribution_campaigns"):
        active_campaign = FamilyContributionCampaign.query.filter_by(
            family_id=family.id, active_slot=True
        ).first()
    campaign_details = None
    if active_campaign:
        contributed = campaign_contributed_points(active_campaign)
        family_available = family_point_balance(family.id)
        contributors = {}
        for contribution in active_campaign.contributions.filter_by(refunded=False).order_by(
            FamilyCampaignContribution.created_at.desc()
        ).all():
            if contribution.user_id not in contributors:
                contributors[contribution.user_id] = {"user": contribution.user, "amount": 0}
            contributors[contribution.user_id]["amount"] += contribution.amount
        campaign_details = {
            "campaign": active_campaign,
            "upgrade": upgrade_definition(active_campaign.upgrade_key) or {},
            "contributed": contributed,
            "family_available": family_available,
            "remaining": max(0, active_campaign.points_required - family_available - contributed),
            "progress": min(100, int(((family_available + contributed) / active_campaign.points_required) * 100)),
            "contributors": sorted(contributors.values(), key=lambda row: row["amount"], reverse=True),
            "history": active_campaign.contributions.filter_by(refunded=False).order_by(
                FamilyCampaignContribution.created_at.desc()
            ).limit(50).all(),
            "required_level": (upgrade_definition(active_campaign.upgrade_key) or {}).get("required_level", 1),
            "level_ready": current_family_level >= (upgrade_definition(active_campaign.upgrade_key) or {}).get("required_level", 1),
        }
    return render_template(
        "family_upgrades.html",
        family=family,
        member=member,
        catalog=catalog,
        purchases=purchases,
        purchased_keys=entitled,
        catalog_by_key=catalog_source,
        available_points=family_point_balance(family.id),
        current_capacity=current_capacity,
        current_capacity_label="Unlimited" if current_capacity >= 1_000_000_000 else str(current_capacity),
        can_purchase=family_has_permission(member, "activate_upgrade"),
        can_start_campaign=is_feature_enabled("contribution_campaigns") and family_can_create(member, "create_campaign"),
        campaign_details=campaign_details,
        personal_balance=personal_point_balance(current_user.id),
        family_points_enabled=family_points_enabled,
        premium_active=premium_active,
        certificate_styles=CERTIFICATE_STYLES,
        current_family_level=current_family_level,
    )


@family_bp.route("/family/<int:family_id>/upgrades/purchase", methods=["POST"])
@login_required
def purchase_family_upgrade(family_id):
    if not (is_feature_enabled("family_upgrades") and is_feature_enabled("family_points")):
        flash("Family upgrades are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.filter_by(id=family_id).with_for_update().first_or_404()
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "activate_upgrade"):
        flash("Only Family owners and admins can activate upgrades.", "danger")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    upgrade_key = request.form.get("upgrade_key", "").strip()
    definition = upgrade_definition(upgrade_key)
    if not definition:
        flash("Choose a valid Family upgrade.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if FamilyUpgradePurchase.query.filter_by(
        family_id=family.id, upgrade_key=upgrade_key
    ).first():
        flash("This Family already owns that upgrade.", "info")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if FamilyContributionCampaign.query.filter_by(family_id=family.id, active_slot=True).first():
        flash("Finish or cancel the active contribution campaign before purchasing directly.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    current_level = family_level_summary(family)["level"]
    if current_level < definition.get("required_level", 1):
        flash(
            f"Reach Family Level {definition.get('required_level', 1)} before unlocking this upgrade.",
            "warning",
        )
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    capacity = definition.get("capacity")
    if capacity:
        current_capacity = effective_family_member_limit(family)
        next_capacity = next_capacity_target(current_capacity)
        if capacity != next_capacity:
            flash("Capacity upgrades must be unlocked in order.", "warning")
            return redirect(url_for("family.family_upgrades", family_id=family.id))
        if capacity < active_family_member_count(family):
            flash("Capacity can never be below the current active member count.", "warning")
            return redirect(url_for("family.family_upgrades", family_id=family.id))
    purchase = FamilyUpgradePurchase(
        family_id=family.id,
        upgrade_key=upgrade_key,
        cost=definition["cost"],
        purchased_by_id=current_user.id,
    )
    db.session.add(purchase)
    try:
        db.session.flush()
        spend_family_points(
            family_id=family.id,
            amount=definition["cost"],
            reason=f"Unlocked {definition['name']}",
            source_type="family_upgrade",
            source_id=purchase.id,
            unique_reward_key=f"family_upgrade:{family.id}:{upgrade_key}",
            awarded_by_id=current_user.id,
        )
        if capacity:
            family.member_limit = capacity
        log_family_action(
            family,
            "family_upgrade_purchased",
            reason=f"{definition['name']} unlocked for {definition['cost']} Family Points.",
        )
        reviewer_ids = {
            row.user_id for row in FamilyMember.query.filter(
                FamilyMember.family_id == family.id,
                FamilyMember.role.in_(["owner", "admin", "moderator"]),
            ).all()
        }
        reviewer_ids.update(
            row.id for row in User.query.filter(
                User.admin_role.in_(["super_admin", "admin", "moderator"])
            ).all()
        )
        for reviewer_id in reviewer_ids - {current_user.id}:
            smart_notify(
                user_id=reviewer_id,
                category="family_upgrade",
                message=f"{family.name} automatically verified its point balance and unlocked {definition['name']}.",
                action_url=url_for("family.family_upgrades", family_id=family.id),
                dedupe_key=f"family-upgrade-verified:{purchase.id}:{reviewer_id}",
                push=False,
                important=False,
            )
        db.session.commit()
    except PointLimitExceeded as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    except IntegrityError:
        db.session.rollback()
        flash("This upgrade was already purchased. No points were spent twice.", "info")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    flash(f"{definition['name']} unlocked for {family.name}.", "success")
    return redirect(url_for("family.family_upgrades", family_id=family.id))


@family_bp.route("/family/<int:family_id>/campaigns", methods=["POST"])
@login_required
def create_upgrade_campaign(family_id):
    if not (is_feature_enabled("family_upgrades") and is_feature_enabled("personal_points") and is_feature_enabled("contribution_campaigns")):
        flash("Member contribution campaigns are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.filter_by(id=family_id).with_for_update().first_or_404()
    member = family_member_for_current_user(family)
    if not family_can_create(member, "create_campaign"):
        flash("You do not have permission to start a contribution campaign.", "danger")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if FamilyContributionCampaign.query.filter_by(family_id=family.id, active_slot=True).first():
        flash("This Family already has an active contribution campaign.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    upgrade_key = request.form.get("upgrade_key", "").strip()
    if not upgrade_can_be_targeted(family, upgrade_key):
        flash("Choose an available upgrade goal. Capacity upgrades must stay in order.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    deadline_raw = request.form.get("deadline", "").strip()
    deadline = parse_family_date(deadline_raw) if deadline_raw else None
    if deadline:
        deadline = deadline.replace(hour=23, minute=59, second=59)
    if deadline_raw and (not deadline or deadline <= datetime.utcnow() or deadline > datetime.utcnow() + timedelta(days=365)):
        flash("Campaign deadline must be within the next 365 days.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    definition = upgrade_definition(upgrade_key)
    family_available = family_point_balance(family.id)
    campaign = FamilyContributionCampaign(
        family_id=family.id,
        upgrade_key=upgrade_key,
        points_required=definition["cost"],
        created_by_id=current_user.id,
        deadline=deadline,
        status="reached" if family_available >= definition["cost"] else "active",
        highest_milestone=100 if family_available >= definition["cost"] else 0,
        active_slot=True,
    )
    db.session.add(campaign)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This Family already has an active campaign.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    for membership in family.members:
        if membership.user_id != current_user.id:
            add_family_notification(
                membership.user_id,
                "contribution_campaign",
                f"{family.name} started a cooperative campaign for {definition['name']}.",
                url_for("family.family_upgrades", family_id=family.id),
            )
    db.session.commit()
    flash("Contribution campaign started. Every member can help at their own pace.", "success")
    return redirect(url_for("family.family_upgrades", family_id=family.id))


@family_bp.route("/family/<int:family_id>/campaigns/<int:campaign_id>/contribute", methods=["POST"])
@login_required
def contribute_to_upgrade_campaign(family_id, campaign_id):
    if not (is_feature_enabled("family_upgrades") and is_feature_enabled("personal_points") and is_feature_enabled("contribution_campaigns")):
        flash("Member contribution campaigns are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    campaign = FamilyContributionCampaign.query.filter_by(
        id=campaign_id, family_id=family_id
    ).with_for_update().first_or_404()
    family = campaign.family
    if not family_member_for_current_user(family):
        flash("Only Family members can contribute.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if campaign.status not in {"active", "reached"} or campaign.active_slot is not True:
        flash("This contribution campaign is no longer accepting points.", "info")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if campaign.deadline and campaign.deadline < datetime.utcnow():
        flash("This campaign deadline has passed. An admin can cancel it to return all contributions.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    selected = request.form.get("amount", "").strip()
    amount_raw = request.form.get("custom_amount", "").strip() if selected == "custom" else selected
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError):
        amount = 0
    if selected != "custom" and amount not in {10, 25, 50, 100}:
        flash("Choose a supported contribution amount.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if amount < 1 or amount > 500:
        flash("Custom contributions must be between 1 and 500 points.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    contributed = campaign_contributed_points(campaign)
    family_available = family_point_balance(family.id)
    remaining = max(0, campaign.points_required - family_available - contributed)
    if remaining <= 0:
        campaign.status = "reached"
        db.session.commit()
        flash("The campaign goal is already reached and ready for admin activation.", "success")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if amount > remaining:
        flash(f"Only {remaining} more points are needed for this campaign.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    recent_count = FamilyCampaignContribution.query.filter(
        FamilyCampaignContribution.user_id == current_user.id,
        FamilyCampaignContribution.created_at >= datetime.utcnow() - timedelta(hours=1),
    ).count()
    from premium import economy_setting_int
    contribution_limit = economy_setting_int("contribution_hourly_limit", 20, minimum=1, maximum=100)
    if recent_count >= contribution_limit:
        flash("You have reached the hourly contribution limit. Please try again later.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    User.query.filter_by(id=current_user.id).with_for_update().first()
    contribution_key = secrets.token_urlsafe(18)
    contribution = FamilyCampaignContribution(
        campaign_id=campaign.id,
        user_id=current_user.id,
        amount=amount,
        contribution_key=contribution_key,
    )
    db.session.add(contribution)
    try:
        db.session.flush()
        spend_personal_points(
            user_id=current_user.id,
            amount=amount,
            reason=f"Contributed to {family.name}: {upgrade_definition(campaign.upgrade_key)['name']}",
            source_type="campaign_contribution",
            source_id=contribution.id,
            unique_reward_key=f"campaign_contribution:{contribution_key}",
        )
        new_total = contributed + amount
        progress = int(((family_available + new_total) / campaign.points_required) * 100)
        crossed = max((milestone for milestone in (25, 50, 75, 100) if progress >= milestone), default=0)
        previous_milestone = campaign.highest_milestone
        if crossed > campaign.highest_milestone:
            campaign.highest_milestone = crossed
        if progress >= 100:
            campaign.status = "reached"
        for membership in family.members:
            if membership.user_id != current_user.id:
                add_family_notification(
                    membership.user_id,
                    "campaign_contribution",
                    f"{current_user.username} contributed {amount} points toward {upgrade_definition(campaign.upgrade_key)['name']}.",
                    url_for("family.family_upgrades", family_id=family.id),
                )
                if crossed > previous_milestone:
                    add_family_notification(
                        membership.user_id,
                        "campaign_milestone",
                        f"{family.name} reached {crossed}% of its {upgrade_definition(campaign.upgrade_key)['name']} campaign!",
                        url_for("family.family_upgrades", family_id=family.id),
                    )
        add_family_notification(
            current_user.id,
            "contribution_received",
            f"Thank you. Your {amount}-point contribution helped {family.name} grow.",
            url_for("family.family_upgrades", family_id=family.id),
        )
        db.session.commit()
    except (IntegrityError, PointLimitExceeded, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) if not isinstance(exc, IntegrityError) else "This contribution was already recorded.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    flash(f"You contributed {amount} Personal Points. Thank you for helping {family.name} grow.", "success")
    return redirect(url_for("family.family_upgrades", family_id=family.id))


@family_bp.route("/family/<int:family_id>/campaigns/<int:campaign_id>/cancel", methods=["POST"])
@login_required
def cancel_upgrade_campaign(family_id, campaign_id):
    if not (is_feature_enabled("family_upgrades") and is_feature_enabled("personal_points") and is_feature_enabled("contribution_campaigns")):
        flash("Member contribution campaigns are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    campaign = FamilyContributionCampaign.query.filter_by(
        id=campaign_id, family_id=family_id
    ).with_for_update().first_or_404()
    family = campaign.family
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "activate_upgrade"):
        flash("Only Family owners and admins can cancel campaigns.", "danger")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if campaign.status == "activated":
        flash("Activated campaign contributions are final and cannot be refunded.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if campaign.status == "cancelled":
        flash("This campaign was already cancelled and refunded.", "info")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    refund_count = 0
    for contribution in campaign.contributions.filter_by(refunded=False).all():
        transaction = PointTransaction.query.filter_by(
            source_type="campaign_contribution", source_id=contribution.id, reversed=False
        ).first()
        if transaction:
            reverse_reward_group(
                transaction,
                reversed_by_id=current_user.id,
                reason="Contribution returned because the Family campaign was cancelled.",
            )
        contribution.refunded = True
        contribution.refunded_at = datetime.utcnow()
        refund_count += 1
    campaign.status = "cancelled"
    campaign.active_slot = None
    campaign.cancelled_at = datetime.utcnow()
    log_family_action(family, "contribution_campaign_cancelled", reason=f"Refunded {refund_count} contributions.")
    for membership in family.members:
        add_family_notification(
            membership.user_id,
            "contribution_campaign",
            f"The {upgrade_definition(campaign.upgrade_key)['name']} campaign was cancelled. Contributed Personal Points were returned.",
            url_for("family.family_upgrades", family_id=family.id),
        )
    db.session.commit()
    flash("Campaign cancelled. Every contribution was returned automatically.", "success")
    return redirect(url_for("family.family_upgrades", family_id=family.id))


@family_bp.route("/family/<int:family_id>/campaigns/<int:campaign_id>/activate", methods=["POST"])
@login_required
def activate_upgrade_campaign(family_id, campaign_id):
    if not (is_feature_enabled("family_upgrades") and is_feature_enabled("personal_points") and is_feature_enabled("contribution_campaigns")):
        flash("Member contribution campaigns are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    campaign = FamilyContributionCampaign.query.filter_by(
        id=campaign_id, family_id=family_id
    ).with_for_update().first_or_404()
    family = Family.query.filter_by(id=family_id).with_for_update().first_or_404()
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "activate_upgrade"):
        flash("Only Family owners and admins can activate campaign upgrades.", "danger")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if campaign.status not in {"active", "reached"} or campaign.active_slot is not True:
        flash("This campaign cannot be activated.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    definition = upgrade_definition(campaign.upgrade_key)
    if not definition:
        flash("This campaign references an upgrade that is no longer available.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    current_level = family_level_summary(family)["level"]
    if current_level < definition.get("required_level", 1):
        flash(
            f"The points are safe. Reach Family Level {definition.get('required_level', 1)} before activating this upgrade.",
            "warning",
        )
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    if not upgrade_is_available(family, campaign.upgrade_key):
        flash("This upgrade is no longer available or was already activated.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    contributed = campaign_contributed_points(campaign)
    family_available = family_point_balance(family.id)
    if family_available + contributed < campaign.points_required:
        flash("The campaign has not reached its goal yet.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    capacity = definition.get("capacity")
    if capacity and (capacity != next_capacity_target(effective_family_member_limit(family)) or capacity < active_family_member_count(family)):
        flash("The capacity upgrade is not valid for the Family’s current size.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    purchase = FamilyUpgradePurchase(
        family_id=family.id, upgrade_key=campaign.upgrade_key,
        cost=campaign.points_required, purchased_by_id=current_user.id,
    )
    db.session.add(purchase)
    try:
        db.session.flush()
        family_points_used = max(0, campaign.points_required - contributed)
        if family_points_used:
            spend_family_points(
                family_id=family.id,
                amount=family_points_used,
                reason=f"Campaign unlocked {definition['name']}",
                source_type="family_upgrade",
                source_id=purchase.id,
                unique_reward_key=f"family_upgrade:{family.id}:{campaign.upgrade_key}",
                awarded_by_id=current_user.id,
            )
        if capacity:
            family.member_limit = capacity
        campaign.status = "activated"
        campaign.active_slot = None
        campaign.activated_at = datetime.utcnow()
        log_family_action(family, "campaign_upgrade_activated", reason=f"{definition['name']} unlocked cooperatively.")
        for membership in family.members:
            add_family_notification(
                membership.user_id,
                "family_upgrade_unlocked",
                f"Together, {family.name} unlocked {definition['name']}!",
                url_for("family.family_detail", family_id=family.id),
            )
        db.session.commit()
    except (IntegrityError, PointLimitExceeded) as exc:
        db.session.rollback()
        flash(str(exc) if not isinstance(exc, IntegrityError) else "This upgrade was already activated.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    flash(f"Together, your Family unlocked {definition['name']}!", "success")
    return redirect(url_for("family.family_upgrades", family_id=family.id))


@family_bp.route("/family/<int:family_id>/banner", methods=["POST"])
@login_required
def update_family_banner(family_id):
    if not is_feature_enabled("family_upgrades"):
        flash("Family upgrades are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "activate_upgrade") or not family_has_upgrade(family.id, "custom_banner"):
        flash("Unlock the custom banner upgrade before changing the banner.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    banner = request.files.get("banner_image")
    if not banner or not banner.filename:
        flash("Choose a banner image.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    valid, message = validate_family_image_upload(banner)
    if not valid:
        flash(message, "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    filename = save_media(banner)
    if not filename:
        flash("The banner could not be saved.", "danger")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    previous = family.banner_image
    family.banner_image = filename
    db.session.commit()
    if previous and previous != filename:
        cleanup_family_image(previous)
    flash("Family banner updated.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/theme", methods=["POST"])
@login_required
def update_family_theme(family_id):
    if not is_feature_enabled("family_upgrades"):
        flash("Family upgrades are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "activate_upgrade") or not family_has_upgrade(family.id, "extra_themes"):
        flash("Unlock extra Family themes before changing the theme.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    theme = request.form.get("theme", "").strip()
    if theme not in {"classic", "sunrise", "ocean", "forest"}:
        flash("Choose a valid Family theme.", "warning")
        return redirect(url_for("family.family_upgrades", family_id=family.id))
    family.theme = theme
    db.session.commit()
    flash("Family theme updated.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/certificate-style", methods=["POST"])
@login_required
def update_family_certificate_style(family_id):
    if not is_feature_enabled("family_upgrades"):
        flash("Family upgrades are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    style = request.form.get("certificate_style", "").strip()
    definition = CERTIFICATE_STYLES.get(style)
    if not family_has_permission(member, "activate_upgrade"):
        flash("Only Family owners and admins can select a certificate style.", "danger")
    elif not definition or not family_has_upgrade(family.id, definition[0]):
        flash("Unlock that certificate with Family Points before selecting it.", "warning")
    else:
        family.certificate_style = style
        db.session.commit()
        flash(f"{definition[1].split(' · ', 1)[0]} certificate selected.", "success")
    return redirect(url_for("family.family_upgrades", family_id=family.id))


@family_bp.route("/family/<int:family_id>/gallery", methods=["POST"])
@login_required
def add_family_gallery_item(family_id):
    if not is_feature_enabled("family_upgrades"):
        flash("Family upgrades are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family_id))
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member or not family_has_upgrade(family.id, "family_gallery"):
        flash("The Family gallery has not been unlocked.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    from premium import family_has_premium
    gallery_limit = 100 if (
        family_has_premium(family) and is_feature_enabled("premium_storage")
    ) else 30
    if family.gallery_items.count() >= gallery_limit:
        flash(f"The Family gallery currently holds up to {gallery_limit} memories.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    media = request.files.get("gallery_image")
    caption = request.form.get("caption", "").strip()
    if len(caption) > 240:
        flash("Gallery captions cannot exceed 240 characters.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if not media or not media.filename:
        flash("Choose a gallery image.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    valid, message = validate_family_image_upload(media)
    if not valid:
        flash(message, "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    filename = save_media(media)
    if not filename:
        flash("The gallery image could not be saved.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    db.session.add(FamilyGalleryItem(
        family_id=family.id, uploaded_by_id=current_user.id,
        media_url=filename, caption=caption,
    ))
    db.session.commit()
    flash("A new memory was added to the Family gallery.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-gallery")


@family_bp.route("/family/<int:family_id>/edit", methods=["GET", "POST"])
@login_required
def edit_family(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    can_edit_details = family_has_permission(member, "edit_family")
    can_change_image = family_has_permission(member, "change_family_image")
    if not can_edit_details and not can_change_image:
        flash("You do not have permission to edit this Family.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if request.method == "POST":
        if not can_edit_details:
            flash("Only the Family owner can edit these Family details.", "danger")
            return redirect(url_for("family.edit_family", family_id=family.id))
        payload, error = validate_family_payload(request.form)
        if error:
            flash(error, "warning")
            return redirect(url_for("family.edit_family", family_id=family.id))
        payload["member_limit"] = effective_family_member_limit(family)
        active_count = active_family_member_count(family)
        if payload["member_limit"] < active_count:
            flash(
                f"Member limit cannot be below the current active member count ({active_count}).",
                "warning",
            )
            return redirect(url_for("family.edit_family", family_id=family.id))
        goal_changed = (
            payload.get("goal_title") != family.goal_title
            or payload.get("goal_description") != family.goal_description
            or payload.get("target_date") != family.target_date
        )
        for key, value in payload.items():
            setattr(family, key, value)
        family.is_active = request.form.get("is_active", "1") == "1"
        if goal_changed:
            log_family_action(
                family, "goal_progress_updated",
                reason=f"The shared goal is now: {family.goal_title or 'Growing together'}.",
            )
        db.session.commit()
        flash("Family updated.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id))
    return render_template(
        "edit_family.html",
        family=family,
        can_edit_family_details=can_edit_details,
        can_change_family_image=can_change_image,
        **family_form_context(),
    )


@family_bp.route("/family/<int:family_id>/image", methods=["POST"])
@login_required
def update_family_image(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "change_family_image"):
        flash("You do not have permission to change this Family picture.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    image_file = request.files.get("profile_image")
    if not image_file or not image_file.filename:
        flash("Choose a Family picture before saving.", "warning")
        return redirect(url_for("family.edit_family", family_id=family.id))
    is_valid, message = validate_family_image_upload(image_file)
    if not is_valid:
        flash(message, "warning")
        return redirect(url_for("family.edit_family", family_id=family.id))
    filename = save_media(image_file)
    if not filename:
        flash("Family picture could not be saved. Try a smaller image.", "danger")
        return redirect(url_for("family.edit_family", family_id=family.id))
    previous_image = family.profile_image
    family.profile_image = filename
    family.profile_image_public_id = filename
    log_family_action(
        family,
        "family_image_changed",
        previous_role="",
        new_role="",
        reason="Family profile picture updated.",
    )
    db.session.commit()
    if previous_image and previous_image != filename:
        cleanup_family_image(previous_image)
    flash("Family picture updated.", "success")
    return redirect(url_for("family.edit_family", family_id=family.id))


@family_bp.route("/family/<int:family_id>/join", methods=["POST"])
@login_required
def join_family(family_id):
    family = Family.query.filter_by(id=family_id).with_for_update().first_or_404()
    existing = FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()
    if existing:
        flash("You are already a part of this family.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if not family.is_active:
        flash("This Family is currently paused.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if family_is_full(family):
        flash("Family is full.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    member = FamilyMember(family_id=family.id, user_id=current_user.id, role="member")
    db.session.add(member)
    if family.owner_id and family.owner_id != current_user.id:
        add_family_notification(
            family.owner_id,
            "family",
            f"{current_user.username} joined your family {family.name}.",
            url_for("family.family_detail", family_id=family.id),
        )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You are already a part of this family.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id))
    flash("You have joined the family.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/poll/create", methods=["POST"])
@login_required
def create_poll(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_can_create(member, "create_poll"):
        flash("You do not have permission to create official polls.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-polls")
    question = request.form.get("question", "").strip()
    option_texts = []
    for value in request.form.getlist("options"):
        text_value = value.strip()
        if text_value and text_value not in option_texts:
            option_texts.append(text_value)
    option_texts = option_texts[:4]
    closes_at = parse_family_datetime(request.form.get("closes_at"))
    if not question or len(question) > 240:
        flash("Poll question must be between 1 and 240 characters.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-polls")
    if len(option_texts) < 2:
        flash("Add at least two poll options.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-polls")
    if closes_at and closes_at <= datetime.utcnow():
        flash("Poll closing time must be in the future.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-polls")
    results_visibility = request.form.get("results_visibility", "after_vote").strip()
    if results_visibility not in {"always", "after_vote", "after_close"}:
        results_visibility = "after_vote"
    poll = FamilyPoll(
        family_id=family.id,
        creator_id=current_user.id,
        question=question,
        allows_multiple_choices=request.form.get("allows_multiple_choices") == "1",
        anonymous_voting=request.form.get("anonymous_voting") == "1",
        results_visibility=results_visibility,
        allow_vote_changes=request.form.get("allow_vote_changes") == "1",
        closes_at=closes_at,
        status="open",
    )
    db.session.add(poll)
    db.session.flush()
    for index, option_text in enumerate(option_texts, start=1):
        db.session.add(
            FamilyPollOption(
                poll_id=poll.id,
                option_text=option_text[:180],
                position=index,
            )
        )
    log_family_action(
        family,
        "poll_created",
        reason=question,
    )
    for membership in family.members:
        if membership.user_id == current_user.id:
            continue
        add_family_notification(
            membership.user_id,
            "family_poll",
            f"New poll in {family.name}: {question}",
            url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}",
        )
    db.session.commit()
    flash("Poll created.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")


@family_bp.route("/family/<int:family_id>/poll/<int:poll_id>/vote", methods=["POST"])
@login_required
def vote_poll(family_id, poll_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member:
        flash("Join this Family before voting.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-polls")
    poll = FamilyPoll.query.filter_by(id=poll_id, family_id=family.id).first_or_404()
    close_expired_family_polls(family)
    if poll.status != "open":
        flash("This poll is closed.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")
    selected_ids = []
    for raw_id in request.form.getlist("option_ids"):
        try:
            selected_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    selected_ids = list(dict.fromkeys(selected_ids))
    valid_options = poll.options.filter(FamilyPollOption.id.in_(selected_ids)).all() if selected_ids else []
    valid_option_ids = [option.id for option in valid_options]
    if not valid_option_ids:
        flash("Choose an option before voting.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")
    if not poll.allows_multiple_choices and len(valid_option_ids) != 1:
        flash("Choose one option for this poll.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")
    existing_votes = FamilyPollVote.query.filter_by(
        poll_id=poll.id,
        user_id=current_user.id,
    ).all()
    if existing_votes and not poll.allow_vote_changes:
        flash("This poll does not allow vote changes.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")
    for vote in existing_votes:
        db.session.delete(vote)
    for option_id in valid_option_ids:
        db.session.add(
            FamilyPollVote(
                poll_id=poll.id,
                option_id=option_id,
                user_id=current_user.id,
            )
        )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Your vote was already recorded.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")
    flash("Vote saved.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")


@family_bp.route("/family/<int:family_id>/poll/<int:poll_id>/close", methods=["POST"])
@login_required
def close_poll(family_id, poll_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    poll = FamilyPoll.query.filter_by(id=poll_id, family_id=family.id).first_or_404()
    if not member or (
        poll.creator_id != current_user.id and not family_has_permission(member, "create_poll")
    ):
        flash("You do not have permission to close this poll.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")
    if poll.status != "closed":
        poll.status = "closed"
        log_family_action(
            family,
            "poll_closed",
            reason=poll.question,
        )
        db.session.commit()
    flash("Poll closed.", "info")
    return redirect(url_for("family.family_detail", family_id=family.id) + f"#poll-{poll.id}")


@family_bp.route("/family/<int:family_id>/challenge/create", methods=["POST"])
@login_required
def create_challenge(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_can_create(member, "create_challenge"):
        flash("You do not have permission to create official challenges.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    active_count = FamilyChallenge.query.filter_by(family_id=family.id, status="active").count()
    challenge_limit = active_challenge_limit(family.id) if is_feature_enabled("family_upgrades") else 3
    if active_count >= challenge_limit:
        flash("This Family has reached its active challenge slot limit.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    challenge_type = request.form.get("challenge_type", "task").strip()
    status = request.form.get("status", "active").strip()
    reward_tier = request.form.get("reward_tier", "easy").strip()
    completion_frequency = request.form.get("completion_frequency", "one_time").strip()
    evidence_requirement = request.form.get("evidence_requirement", "none").strip()
    participant_scope = request.form.get("participant_scope", "all_members").strip()
    visibility = request.form.get("visibility", "family").strip()
    custom_frequency_days = parse_optional_int(
        request.form.get("custom_frequency_days"), minimum=1, maximum=365
    )
    max_participants_raw = request.form.get("max_participants", "").strip()
    max_participants = parse_optional_int(max_participants_raw, minimum=1, maximum=10000)
    requires_admin_approval = request.form.get("requires_admin_approval") == "on"
    allow_achievement_sharing = request.form.get("allow_achievement_sharing") == "on"
    mandatory_all_members = request.form.get("mandatory_all_members") == "on"
    starts_at_raw = request.form.get("starts_at", "").strip()
    ends_at_raw = request.form.get("ends_at", "").strip()
    starts_at = parse_family_date(starts_at_raw)
    ends_at = parse_family_date(ends_at_raw)
    if ends_at:
        ends_at = ends_at.replace(hour=23, minute=59, second=59)
    if not title or len(title) > 160:
        flash("Challenge title is required and cannot exceed 160 characters.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if not description or len(description) > 5000:
        flash("Add clear instructions of no more than 5,000 characters.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge_type not in CHALLENGE_TYPES:
        flash("Choose a valid challenge type.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge_type == "daily_check_in" and not is_feature_enabled("daily_checkins"):
        flash("Daily check-ins are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if status not in CHALLENGE_STATUSES:
        status = "active"
    if (starts_at_raw and starts_at is None) or (ends_at_raw and ends_at is None):
        flash("Enter valid start and end dates.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if completion_frequency not in COMPLETION_FREQUENCIES:
        flash("Choose a valid completion frequency.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge_type == "daily_check_in" and completion_frequency != "daily":
        flash("Daily check-ins must use daily completion frequency.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if completion_frequency == "custom" and custom_frequency_days is None:
        flash("Choose a custom interval between 1 and 365 days.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if evidence_requirement not in EVIDENCE_REQUIREMENTS:
        flash("Choose a valid evidence requirement.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if participant_scope not in PARTICIPANT_SCOPES:
        flash("Choose who can participate.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if max_participants_raw and max_participants is None:
        flash("Maximum participants must be between 1 and 10,000.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if visibility not in CHALLENGE_VISIBILITIES:
        flash("Choose a valid challenge visibility.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if visibility == "public" and family.privacy != "public":
        flash("Private Families cannot publish public challenges.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if starts_at and ends_at and starts_at > ends_at:
        flash("End date must be on or after the start date.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if evidence_requirement == "admin_approval":
        requires_admin_approval = True
    allowed_tiers = CHALLENGE_ALLOWED_REWARD_TIERS.get(challenge_type, set())
    if reward_tier not in allowed_tiers:
        flash("Choose a reward level recommended for that challenge type.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    points = challenge_reward_values()[reward_tier]
    challenge = FamilyChallenge(
        family_id=family.id,
        creator_id=current_user.id,
        title=title,
        description=description,
        challenge_type=challenge_type,
        points=points,
        reward_tier=reward_tier,
        completion_frequency=completion_frequency,
        custom_frequency_days=custom_frequency_days if completion_frequency == "custom" else None,
        evidence_requirement=evidence_requirement,
        participant_scope=participant_scope,
        max_participants=None if mandatory_all_members else max_participants,
        visibility=visibility,
        requires_admin_approval=requires_admin_approval,
        allow_achievement_sharing=allow_achievement_sharing,
        mandatory_all_members=mandatory_all_members,
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
    )
    db.session.add(challenge)
    db.session.flush()
    if challenge.status == "active":
        expiry_label = challenge.ends_at.strftime("%b %d, %Y") if challenge.ends_at else "No fixed expiry"
        db.session.add(Message(
            sender_id=current_user.id,
            family_id=family.id,
            content=f"New challenge: {challenge.title} · Expires: {expiry_label}. Open Family Goals to view or accept it.",
            media_type="text",
        ))
        for membership in family.members:
            may_see = challenge.visibility != "admins_only" or membership.role in {"owner", "admin"}
            if membership.user_id != current_user.id and may_see:
                add_family_notification(
                    membership.user_id,
                    "challenge_created",
                    f"New challenge in {family.name}: {challenge.title}",
                    url_for("family.family_detail", family_id=family.id) + f"#challenge-{challenge.id}",
                )
    db.session.commit()
    flash("Challenge created.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")


@family_bp.route(
    "/family/<int:family_id>/challenge/<int:challenge_id>/participation/<action>",
    methods=["POST"],
)
@login_required
def challenge_participation(family_id, challenge_id, action):
    family = Family.query.get_or_404(family_id)
    membership = family_member_for_current_user(family)
    challenge = FamilyChallenge.query.filter_by(
        id=challenge_id, family_id=family.id
    ).with_for_update().first_or_404()
    if not membership or not member_can_participate(challenge, membership):
        flash("You are not eligible to join this challenge.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge.visibility == "admins_only" and not family_has_permission(membership, "create_challenge"):
        flash("This challenge is limited to Family admins.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge.mandatory_all_members:
        flash("This is a mandatory challenge for eligible Family members.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    existing = ChallengeParticipant.query.filter_by(
        challenge_id=challenge.id, user_id=current_user.id
    ).first()
    if action == "join":
        if active_family_restriction(family.id, current_user.id, "suspend"):
            flash("You are temporarily unable to join Family challenges.", "warning")
        elif not challenge_is_current(challenge):
            flash("This challenge is not currently open.", "warning")
        elif existing:
            flash("You already joined this challenge.", "info")
        else:
            eligible_ids = {member.user_id for member in eligible_challenge_members(challenge)}
            joined_count = ChallengeParticipant.query.filter(
                ChallengeParticipant.challenge_id == challenge.id,
                ChallengeParticipant.user_id.in_(eligible_ids),
            ).count() if eligible_ids else 0
            if challenge.max_participants and joined_count >= challenge.max_participants:
                flash("This challenge has reached its participant limit.", "warning")
                return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
            db.session.add(ChallengeParticipant(
                challenge_id=challenge.id, user_id=current_user.id
            ))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("You already joined this challenge.", "info")
            else:
                flash("You joined the challenge. We’re cheering you on.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if action == "leave":
        if not existing:
            flash("You have not joined this challenge.", "info")
        else:
            current_completion = ChallengeCompletion.query.filter_by(
                challenge_id=challenge.id,
                user_id=current_user.id,
                period_key=challenge_completion_period(challenge),
            ).filter(ChallengeCompletion.verification_status != "rejected").first()
            if current_completion:
                flash("You cannot leave after submitting this challenge period.", "warning")
            else:
                db.session.delete(existing)
                db.session.commit()
                flash("You left the challenge.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    flash("Choose a valid participation action.", "warning")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")


@family_bp.route("/family/<int:family_id>/challenge/<int:challenge_id>/complete", methods=["POST"])
@login_required
def complete_challenge(family_id, challenge_id):
    async_completion = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member:
        flash("Join this Family before completing challenges.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    challenge = FamilyChallenge.query.filter_by(
        id=challenge_id, family_id=family.id
    ).with_for_update().first_or_404()
    if not member_can_participate(challenge, member):
        flash("You are not eligible to participate in this challenge.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if active_family_restriction(family.id, current_user.id, "suspend"):
        flash("You are temporarily unable to complete Family challenges.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if not challenge.mandatory_all_members and not ChallengeParticipant.query.filter_by(
        challenge_id=challenge.id, user_id=current_user.id
    ).first():
        flash("Join this challenge before submitting a completion.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if not challenge_is_current(challenge):
        flash("This challenge is not active.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    recent_completion_count = ChallengeCompletion.query.filter(
        ChallengeCompletion.user_id == current_user.id,
        ChallengeCompletion.completed_at >= datetime.utcnow() - timedelta(hours=1),
    ).count()
    if recent_completion_count >= 20:
        db.session.add(PointSecurityEvent(
            user_id=current_user.id,
            family_id=family.id,
            event_type="completion_rate_limit",
            source_type="family_challenge",
            source_id=challenge.id,
            details="More than 20 challenge completion submissions were attempted within one hour.",
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
        ))
        db.session.commit()
        flash("You have reached the hourly challenge submission limit. Please try again later.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    period_key = challenge_completion_period(challenge)
    existing = ChallengeCompletion.query.filter_by(
        challenge_id=challenge.id, user_id=current_user.id, period_key=period_key
    ).first()
    if existing:
        if existing.verification_status == "rejected":
            db.session.delete(existing)
            db.session.flush()
            existing = None
    if existing:
        flash("You already completed this challenge.", "info")
        if (
            existing.verification_status == "completed"
            and challenge.allow_achievement_sharing
            and is_feature_enabled("achievement_posts")
            and not existing.achievement_post
        ):
            return redirect(url_for(
                "family.share_challenge_achievement",
                family_id=family.id,
                completion_id=existing.id,
            ))
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    evidence_text = request.form.get("evidence_text", "").strip()
    evidence_file = request.files.get("evidence_media")
    evidence_media_url = ""
    if len(evidence_text) > 3000:
        flash("Completion notes cannot exceed 3,000 characters.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    requirement = challenge.evidence_requirement
    needs_file = requirement in {"photo", "video", "audio", "file"}
    if requirement == "completion_note" and not evidence_text:
        flash("Add the required completion note.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if needs_file and not (evidence_file and evidence_file.filename):
        flash(f"This challenge requires {requirement} evidence.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if requirement == "none" and evidence_file and evidence_file.filename:
        flash("This challenge does not accept evidence files.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if evidence_file and evidence_file.filename:
        is_valid, upload_message = validate_upload(evidence_file)
        if not is_valid:
            flash(upload_message, "warning")
            return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
        uploaded_type = get_media_type(evidence_file.filename)
        if requirement in {"photo", "video", "audio"}:
            expected_type = "image" if requirement == "photo" else requirement
            if uploaded_type != expected_type:
                flash(f"Upload a valid {requirement} file for this challenge.", "warning")
                return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
        filename = save_media(evidence_file)
        if filename:
            evidence_media_url = filename
        else:
            flash("The evidence file could not be saved.", "warning")
            return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    verification_status = "pending" if challenge.requires_admin_approval else "completed"
    previous_family_level = family_level_summary(family)["level"] if verification_status == "completed" and is_feature_enabled("family_levels") else None
    completion = ChallengeCompletion(
        challenge_id=challenge.id,
        user_id=current_user.id,
        evidence_text=evidence_text,
        evidence_media_url=evidence_media_url,
        verification_status=verification_status,
        period_key=period_key,
        points_awarded=reward_for_challenge(challenge),
    )
    db.session.add(completion)
    reward_limit_message = ""
    try:
        db.session.flush()
        if verification_status == "completed":
            try:
                award_challenge_completion_points(completion)
            except PointLimitExceeded as exc:
                reward_limit_message = str(exc)
                completion.points_awarded = 0
                db.session.add(PointSecurityEvent(
                    user_id=current_user.id,
                    family_id=family.id,
                    event_type="daily_earning_limit",
                    source_type="challenge_completion",
                    source_id=completion.id,
                    details=reward_limit_message,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
                ))
            record_challenge_streaks(completion)
            notify_family_level_increase(family, previous_family_level)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You already received this challenge reward for the current period.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if verification_status == "pending":
        for leader in family.members.filter(FamilyMember.role.in_(["owner", "admin", "moderator"])).all():
            if leader.user_id != current_user.id:
                add_family_notification(
                    leader.user_id,
                    "challenge_approval",
                    f"{current_user.username} submitted {challenge.title} for approval.",
                    url_for("family.family_detail", family_id=family.id) + "#family-challenges",
                )
        db.session.commit()
        pending_message = "Completion submitted for Family admin approval. Points are pending."
        if async_completion:
            return jsonify({
                "ok": True,
                "status": "pending",
                "message": pending_message,
                "redirect_url": url_for("family.family_detail", family_id=family.id) + "#family-challenges",
            })
        flash(pending_message, "success")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge.creator_id and challenge.creator_id not in {current_user.id}:
        add_family_notification(
            challenge.creator_id,
            "challenge_completed",
            f"{current_user.username} completed {challenge.title}.",
            url_for("family.family_detail", family_id=family.id) + "#family-challenges",
        )
        db.session.commit()
    celebration = {
        "title": "Challenge complete!",
        "message": (
            f"You earned {completion.points_awarded} personal points, and "
            f"{family.name} gained {completion.points_awarded} Family points. Beautiful work."
        ),
        "personal_points": completion.points_awarded,
        "family_points": completion.points_awarded,
    }
    if reward_limit_message:
        celebration["message"] = (
            "Your completion was recorded, but no additional points were added because "
            "today’s repeatable reward limit has been reached."
        )
    if not challenge.allow_achievement_sharing:
        redirect_url = url_for("family.family_detail", family_id=family.id) + "#family-challenges"
        if async_completion:
            return jsonify({"ok": True, "status": "completed", "celebration": celebration, "redirect_url": redirect_url})
        flash(f"Challenge completed. You earned {completion.points_awarded} points.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if not is_feature_enabled("achievement_posts"):
        if async_completion:
            return jsonify({
                "ok": True,
                "status": "completed",
                "celebration": celebration,
                "redirect_url": url_for("family.family_detail", family_id=family.id) + "#family-challenges",
            })
        flash("Challenge marked complete.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if current_user.profile.auto_share_completed_challenges:
        achievement = create_challenge_achievement_post(completion, "family")
        db.session.add(achievement)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            achievement = Post.query.filter_by(challenge_completion_id=completion.id).first()
        flash("Challenge completed and shared inside your Family.", "success")
        if async_completion:
            return jsonify({
                "ok": True,
                "status": "completed",
                "celebration": celebration,
                "redirect_url": (
                    url_for("main.post_detail", post_id=achievement.id)
                    if achievement else url_for("family.family_detail", family_id=family.id) + "#family-challenges"
                ),
            })
        return redirect(
            url_for("main.post_detail", post_id=achievement.id)
            if achievement
            else url_for("family.family_detail", family_id=family.id)
        )
    flash("Challenge completed. Choose whether you want to share the achievement.", "success")
    share_url = url_for(
        "family.share_challenge_achievement",
        family_id=family.id,
        completion_id=completion.id,
    )
    if async_completion:
        return jsonify({
            "ok": True,
            "status": "completed",
            "celebration": celebration,
            "redirect_url": share_url,
            "ask_to_share": True,
        })
    return redirect(share_url)


@family_bp.route(
    "/family/<int:family_id>/challenge/<int:challenge_id>/reward",
    methods=["POST"],
)
@login_required
def update_challenge_reward(family_id, challenge_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "create_challenge"):
        flash("Only Family owners and admins can update challenge rewards.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    challenge = FamilyChallenge.query.filter_by(
        id=challenge_id, family_id=family.id
    ).first_or_404()
    reward_tier = request.form.get("reward_tier", "").strip()
    if reward_tier not in CHALLENGE_ALLOWED_REWARD_TIERS.get(challenge.challenge_type, set()):
        flash("That reward level is not allowed for this challenge type.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    challenge.reward_tier = reward_tier
    challenge.points = challenge_reward_values()[reward_tier]
    db.session.commit()
    if challenge.completions.count():
        flash("Reward updated for future completions. Existing earned points were preserved.", "success")
    else:
        flash("Challenge reward updated.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")


@family_bp.route(
    "/family/<int:family_id>/completion/<int:completion_id>/<action>",
    methods=["POST"],
)
@login_required
def review_challenge_completion(family_id, completion_id, action):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member or member.role not in {"owner", "admin", "moderator"}:
        flash("Only Family leaders can review challenge completions.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    completion = ChallengeCompletion.query.join(FamilyChallenge).filter(
        ChallengeCompletion.id == completion_id,
        FamilyChallenge.family_id == family.id,
    ).first_or_404()
    if action == "invalidate":
        if completion.verification_status != "completed":
            flash("Only an approved completion can be invalidated.", "info")
            return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
        if completion.user_id == current_user.id and not is_platform_owner(current_user):
            flash("You cannot invalidate your own completion.", "warning")
            return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
        reason = request.form.get("reason", "").strip()
        if len(reason) < 10 or len(reason) > 500:
            flash("Add a clear reversal reason between 10 and 500 characters.", "warning")
            return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
        transaction = completion.user.point_transactions.filter_by(
            source_type="challenge_completion", source_id=completion.id, reversed=False
        ).first()
        reversed_transactions = []
        if transaction:
            reversed_transactions = reverse_reward_group(
                transaction, reversed_by_id=current_user.id, reason=reason
            )
        completion.verification_status = "invalidated"
        db.session.add(AuditLog(
            actor_user_id=current_user.id,
            actor_role=current_user.admin_role or member.role,
            action_type="challenge_points_reversed",
            target_user_id=completion.user_id,
            target_family_id=family.id,
            target_content_id=completion.id,
            reason=reason,
            metadata_text=f"transactions={','.join(str(item.id) for item in reversed_transactions)}",
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
        ))
        db.session.commit()
        add_family_notification(
            completion.user_id,
            "challenge_approval",
            f"Points for {completion.challenge.title} were reversed after moderator review.",
            url_for("main.point_history"),
        )
        db.session.commit()
        flash("The completion was invalidated and its Personal and Family Points were reversed.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if completion.verification_status != "pending":
        flash("This completion has already been reviewed.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if action == "reject":
        completion.verification_status = "rejected"
        db.session.commit()
        add_family_notification(
            completion.user_id,
            "challenge_approval",
            f"Your completion for {completion.challenge.title} needs another try.",
            url_for("family.family_detail", family_id=family.id) + "#family-challenges",
        )
        db.session.commit()
        flash("Completion returned to the member.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if action != "approve":
        flash("Choose a valid review action.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if completion.user_id == current_user.id:
        flash("Family leaders cannot approve their own point-bearing completion.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    previous_family_level = family_level_summary(family)["level"] if is_feature_enabled("family_levels") else None
    completion.verification_status = "completed"
    award_challenge_completion_points(completion, awarded_by_id=current_user.id)
    record_challenge_streaks(completion)
    notify_family_level_increase(family, previous_family_level)
    achievement = None
    if (
        completion.challenge.allow_achievement_sharing
        and is_feature_enabled("achievement_posts")
        and completion.user.profile.auto_share_completed_challenges
    ):
        achievement = create_challenge_achievement_post(completion, "family")
        db.session.add(achievement)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        completion = ChallengeCompletion.query.get(completion_id)
        completion.verification_status = "completed"
        award_challenge_completion_points(completion, awarded_by_id=current_user.id)
        db.session.commit()
    action_url = url_for("family.family_detail", family_id=family.id) + "#family-challenges"
    if achievement and achievement.id:
        action_url = url_for("main.post_detail", post_id=achievement.id)
    elif completion.challenge.allow_achievement_sharing and is_feature_enabled("achievement_posts"):
        action_url = url_for(
            "family.share_challenge_achievement",
            family_id=family.id,
            completion_id=completion.id,
        )
    add_family_notification(
        completion.user_id,
        "challenge_approval",
        f"Your completion for {completion.challenge.title} was approved. You earned {completion.points_awarded} points.",
        action_url,
    )
    db.session.commit()
    flash("Completion approved and points awarded.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")


def create_challenge_achievement_post(completion, audience):
    challenge = completion.challenge
    return Post(
        user_id=completion.user_id,
        family_id=challenge.family_id if audience == "family" else None,
        content="",
        media_url="",
        media_type="text",
        audience=audience,
        post_type="achievement",
        achievement_type="challenge_completed",
        challenge_completion_id=completion.id,
        encouraging_message="Your steady effort is worth celebrating. Keep growing together.",
    )


@family_bp.route(
    "/family/<int:family_id>/completion/<int:completion_id>/share-achievement",
    methods=["GET", "POST"],
)
@login_required
def share_challenge_achievement(family_id, completion_id):
    family = Family.query.get_or_404(family_id)
    completion = ChallengeCompletion.query.filter_by(
        id=completion_id,
        user_id=current_user.id,
    ).first_or_404()
    if completion.challenge.family_id != family.id or not family_member_for_current_user(family):
        flash("You cannot share an achievement from that Family.", "warning")
        return redirect(url_for("main.home"))
    if completion.verification_status != "completed" or not completion.challenge.allow_achievement_sharing:
        flash("This challenge completion cannot be shared as an achievement.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if not is_feature_enabled("achievement_posts"):
        flash("Achievement posts are coming soon.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if completion.achievement_post:
        flash("This achievement has already been saved.", "info")
        return redirect(url_for("main.post_detail", post_id=completion.achievement_post.id))
    if request.method == "POST":
        audience = request.form.get("audience", "private")
        if audience not in {"public", "family", "private"}:
            flash("Choose a valid achievement audience.", "warning")
            return redirect(request.url)
        if audience == "public" and family.privacy != "public":
            flash("Achievements from private Families cannot be shared publicly.", "warning")
            return redirect(request.url)
        achievement = create_challenge_achievement_post(completion, audience)
        db.session.add(achievement)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("This achievement has already been saved.", "info")
            return redirect(url_for("family.family_detail", family_id=family.id))
        flash(
            "Achievement kept private." if audience == "private" else "Achievement shared.",
            "success",
        )
        return redirect(url_for("main.post_detail", post_id=achievement.id))
    return render_template(
        "share_achievement.html",
        completion=completion,
        family=family,
        can_share_publicly=family.privacy == "public",
    )


@family_bp.route("/family/<int:family_id>/quiz/create", methods=["POST"])
@login_required
def create_quiz(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_can_create(member, "create_quiz"):
        flash("You do not have permission to create quizzes.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if not family_supports_quizzes(family):
        flash("This Family type does not use quizzes.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
    open_count = Quiz.query.filter_by(family_id=family.id, status="open").count()
    quiz_limit = open_quiz_limit(family.id) if is_feature_enabled("family_upgrades") else 2
    if open_count >= quiz_limit:
        flash("This Family has reached its open quiz slot limit.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    status = request.form.get("status", "open").strip()
    opens_at = parse_family_date(request.form.get("opens_at"))
    closes_at = parse_family_date(request.form.get("closes_at"))
    if closes_at:
        closes_at = closes_at.replace(hour=23, minute=59, second=59)
    if status not in QUIZ_STATUSES:
        status = "open"
    if not title:
        flash("Quiz title is required.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
    time_limit_seconds = parse_optional_int(request.form.get("time_limit_seconds"), 30, 7200)
    pass_mark = parse_optional_int(request.form.get("pass_mark"), 1, 100) or 60
    attempt_limit = parse_optional_int(request.form.get("attempt_limit"), 1, 10) or 1
    if opens_at and closes_at and closes_at <= opens_at:
        flash("Quiz closing date must be after its opening date.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
    quiz = Quiz(
        family_id=family.id,
        creator_id=current_user.id,
        title=title,
        description=description,
        opens_at=opens_at,
        closes_at=closes_at,
        time_limit_seconds=time_limit_seconds,
        status=status,
        allow_multiple_attempts=attempt_limit > 1,
        show_correct_answers=request.form.get("show_correct_answers", "1") == "1",
        pass_mark=pass_mark,
        attempt_limit=attempt_limit,
    )
    db.session.add(quiz)
    db.session.flush()
    question_count = 0
    for position in range(1, 6):
        question_text = request.form.get(f"question_{position}", "").strip()
        if not question_text:
            continue
        points = parse_points(request.form.get(f"question_{position}_points"))
        points = max(5, points or 5)
        correct_choice = request.form.get(f"question_{position}_correct", "").strip()
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_text=question_text,
            question_type="multiple_choice",
            points=points,
            position=position,
            explanation=request.form.get(f"question_{position}_explanation", "").strip()[:1000],
        )
        db.session.add(question)
        db.session.flush()
        valid_choices = 0
        has_correct_choice = False
        for choice_position in range(1, 5):
            choice_text = request.form.get(
                f"question_{position}_choice_{choice_position}", ""
            ).strip()
            if not choice_text:
                continue
            is_correct = correct_choice == str(choice_position)
            has_correct_choice = has_correct_choice or is_correct
            db.session.add(
                QuizChoice(
                    question_id=question.id,
                    choice_text=choice_text,
                    is_correct=is_correct,
                )
            )
            valid_choices += 1
        if valid_choices < 2 or not has_correct_choice:
            db.session.rollback()
            flash("Each quiz question needs at least two choices and one correct answer.", "warning")
            return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
        question_count += 1
    if not question_count:
        db.session.rollback()
        flash("Add at least one quiz question.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
    if quiz.status == "open":
        closing_label = quiz.closes_at.strftime("%b %d, %Y") if quiz.closes_at else "Open-ended"
        db.session.add(Message(
            sender_id=current_user.id,
            family_id=family.id,
            content=f"New quiz: {quiz.title} · Closes: {closing_label}. Open Family Goals to take it.",
            media_type="text",
        ))
        for membership in family.members:
            if membership.user_id != current_user.id:
                add_family_notification(
                    membership.user_id,
                    "quiz_starting",
                    f"New quiz in {family.name}: {quiz.title}",
                    url_for("family.take_quiz", family_id=family.id, quiz_id=quiz.id),
                )
    db.session.commit()
    flash("Quiz created.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")


@family_bp.route("/family/<int:family_id>/quiz/<int:quiz_id>", methods=["GET", "POST"])
@login_required
def take_quiz(family_id, quiz_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member:
        flash("Join this Family before taking quizzes.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    quiz = Quiz.query.filter_by(id=quiz_id, family_id=family.id).first_or_404()
    submitted_attempts = QuizAttempt.query.filter_by(
        quiz_id=quiz.id, user_id=current_user.id
    ).filter(
        QuizAttempt.submitted_at != None
    ).order_by(QuizAttempt.submitted_at.desc()).all()
    existing_attempt = submitted_attempts[0] if submitted_attempts else None
    attempt_id = request.args.get("attempt_id")
    selected_attempt = None
    if attempt_id:
        selected_attempt = QuizAttempt.query.filter_by(
            id=attempt_id, quiz_id=quiz.id, user_id=current_user.id
        ).first()
    attempt_limit = max(1, quiz.attempt_limit or (10 if quiz.allow_multiple_attempts else 1))
    if selected_attempt or len(submitted_attempts) >= attempt_limit:
        attempt = selected_attempt or existing_attempt
        answers = {answer.question_id: answer for answer in attempt.answers.all()}
        return render_template(
            "quiz_take.html",
            family=family,
            quiz=quiz,
            questions=quiz.questions.order_by(QuizQuestion.position.asc()).all(),
            attempt=attempt,
            answers=answers,
            show_results=True,
        )
    if not quiz_is_open(quiz):
        flash("This quiz is not open.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-quizzes")
    questions = quiz.questions.order_by(QuizQuestion.position.asc()).all()
    timer_key = f"quiz_started:{quiz.id}"
    if request.method == "POST":
        started_raw = session.get(timer_key, "")
        try:
            started_at = datetime.fromisoformat(started_raw)
        except (TypeError, ValueError):
            flash("Your quiz session could not be verified. Please open the quiz again.", "warning")
            return redirect(request.url)
        now = datetime.utcnow()
        if started_at > now or started_at < now - timedelta(hours=3):
            flash("Your quiz session expired. Please open the quiz again.", "warning")
            return redirect(request.url)
        if quiz.time_limit_seconds and (now - started_at).total_seconds() > quiz.time_limit_seconds + 15:
            flash("The quiz time limit has passed. Please try again if attempts remain.", "warning")
            return redirect(request.url)
        attempt = QuizAttempt(quiz_id=quiz.id, user_id=current_user.id, started_at=started_at)
        db.session.add(attempt)
        db.session.flush()
        score = 0
        for question in questions:
            selected_choice_id = request.form.get(f"question_{question.id}")
            selected_choice = None
            awarded_points = 0
            if selected_choice_id:
                try:
                    selected_choice_id = int(selected_choice_id)
                except ValueError:
                    selected_choice_id = None
                selected_choice = (
                    QuizChoice.query.filter_by(
                        id=selected_choice_id, question_id=question.id
                    ).first()
                    if selected_choice_id
                    else None
                )
            if selected_choice and selected_choice.is_correct:
                awarded_points = question.points
                score += awarded_points
            db.session.add(
                QuizAnswer(
                    attempt_id=attempt.id,
                    question_id=question.id,
                    selected_choice_id=selected_choice.id if selected_choice else None,
                    awarded_points=awarded_points,
                )
            )
        maximum_score = sum(max(0, question.points or 0) for question in questions)
        # Keep the legacy quiz score ceiling while percentage uses the full verified total.
        attempt.score = min(score, 25)
        attempt.percentage = round(score / maximum_score * 100) if maximum_score else 0
        attempt.passed = attempt.percentage >= quiz.pass_mark
        attempt.submitted_at = now
        session.pop(timer_key, None)
        if attempt.passed:
            controlled_points = min(25, max(5, round(attempt.percentage / 20) * 5))
            _, created = award_points(
                amount=controlled_points, reason=f"Passed {quiz.title}", source_type="quiz",
                source_id=quiz.id, user_id=current_user.id,
                unique_reward_key=f"quiz:{quiz.id}:user:{current_user.id}:reward",
                awarded_by_id=quiz.creator_id,
            )
            attempt.points_awarded = controlled_points if created else 0
        record_streak_activity(
            current_user, "learning", source_type="quiz_attempt",
            source_id=attempt.id, unique_key=f"learning-quiz:{attempt.id}",
            occurred_at=attempt.submitted_at,
        )
        db.session.commit()
        flash("Quiz submitted.", "success")
        return redirect(
            url_for("family.take_quiz", family_id=family.id, quiz_id=quiz.id, attempt_id=attempt.id)
        )
    try:
        quiz_started_at = datetime.fromisoformat(session.get(timer_key, ""))
    except (TypeError, ValueError):
        quiz_started_at = datetime.utcnow()
        session[timer_key] = quiz_started_at.isoformat()
    return render_template(
        "quiz_take.html",
        family=family,
        quiz=quiz,
        questions=questions,
        attempt=None,
        answers={},
        show_results=False,
        quiz_started_at=quiz_started_at.isoformat(),
    )


@family_bp.route("/family/<int:family_id>/quiz-performance")
@login_required
def quiz_performance(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "create_quiz"):
        flash("Only authorized Family admins can review quiz performance.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    quizzes = family.quizzes.order_by(Quiz.created_at.desc()).all()
    attempts = QuizAttempt.query.join(Quiz).filter(
        Quiz.family_id == family.id, QuizAttempt.submitted_at.isnot(None)
    ).order_by(QuizAttempt.submitted_at.desc()).all()
    return render_template("quiz_performance.html", family=family, quizzes=quizzes, attempts=attempts)


@family_bp.route("/family/<int:family_id>/invite", methods=["POST"])
@login_required
def invite_family_member(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "invite_members"):
        flash("Only Family owners and admins can invite members.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    username = request.form.get("username", "").strip()
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user:
        flash("No user found with that username.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if FamilyMember.query.filter_by(family_id=family.id, user_id=user.id).first():
        flash("This user is already a family member.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id))
    add_family_notification(
        user.id,
        "family_invite",
        f"You have been invited to join the family {family.name}. Open Families to join.",
        url_for("family.family_detail", family_id=family.id),
    )
    db.session.commit()
    flash("Invite sent. The user can join from notifications.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/member/<int:member_id>/<action>", methods=["POST"])
@login_required
def manage_family_member(family_id, member_id, action):
    family = Family.query.get_or_404(family_id)
    actor_member = family_member_for_current_user(family)
    if not family_has_permission(actor_member, "manage_members"):
        flash("You do not have permission to manage Family members.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    target_member = FamilyMember.query.filter_by(id=member_id, family_id=family.id).first_or_404()
    if request.form.get("confirm") != "1":
        flash("Please confirm this member action.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    actor_role = family_role(actor_member)
    target_role = family_role(target_member)
    if target_role == "owner":
        flash("The Family owner cannot be removed, demoted, or reassigned here.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if target_member.user_id == current_user.id:
        flash("You cannot change your own Family role here.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    new_role = None
    if action == "promote":
        new_role = "admin"
    elif action == "make_moderator":
        new_role = "moderator"
    elif action == "demote":
        new_role = "member"
    if new_role:
        if not family_has_permission(actor_member, "manage_roles"):
            flash("Only the Family owner can appoint or remove Family roles.", "danger")
            return redirect(url_for("family.family_detail", family_id=family.id))
        if new_role in {"admin", "moderator"} and user_blocked_or_suspended(target_member.user_id):
            flash("Blocked or suspended users cannot be promoted.", "warning")
            return redirect(url_for("family.family_detail", family_id=family.id))
        if target_role == new_role:
            flash("That member already has this role.", "info")
            return redirect(url_for("family.family_detail", family_id=family.id))
        if new_role in {"admin", "moderator"}:
            role_count = family.members.filter_by(role=new_role).count()
            if role_count >= family_role_limit(family, new_role):
                flash(
                    f"This Family has reached its {FAMILY_ROLE_LABELS[new_role]} limit. "
                    "Earn the role upgrade with Family Points or use Family Premium.",
                    "warning",
                )
                return redirect(url_for("family.family_detail", family_id=family.id))
        previous_role = target_role
        target_member.role = new_role
        log_family_action(
            family,
            "role_changed",
            target_user_id=target_member.user_id,
            previous_role=previous_role,
            new_role=new_role,
        )
        add_family_notification(
            target_member.user_id,
            "family_role",
            f"Your role in {family.name} changed to {FAMILY_ROLE_LABELS[new_role]}.",
            url_for("family.family_detail", family_id=family.id),
        )
        flash("Family role updated.", "success")
    elif action == "remove":
        if actor_role != "owner" and target_role != "member":
            flash("Family admins can only remove regular members.", "danger")
            return redirect(url_for("family.family_detail", family_id=family.id))
        removed_user_id = target_member.user_id
        previous_role = target_role
        db.session.delete(target_member)
        log_family_action(
            family,
            "member_removed",
            target_user_id=removed_user_id,
            previous_role=previous_role,
            reason=request.form.get("reason", "").strip(),
        )
        add_family_notification(
            removed_user_id,
            "family_role",
            f"You were removed from {family.name}.",
            url_for("family.families"),
        )
        flash("Member removed from family.", "info")
    else:
        flash("Invalid member action.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    db.session.commit()
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/member/<int:member_id>/creation-permissions", methods=["POST"])
@login_required
def update_member_creation_permissions(family_id, member_id):
    family = Family.query.get_or_404(family_id)
    actor_member = family_member_for_current_user(family)
    target_member = FamilyMember.query.filter_by(id=member_id, family_id=family.id).first_or_404()
    if not family_has_permission(actor_member, "manage_members"):
        abort(403)
    if (
        target_member.user_id == current_user.id
        or family_role(target_member) == "owner"
        or role_rank(family_role(actor_member)) <= role_rank(family_role(target_member))
    ):
        flash("You cannot change creation rights for that Family role.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-members")
    labels = []
    for permission, field in FAMILY_CREATION_GRANTS.items():
        enabled = request.form.get(permission) == "on"
        setattr(target_member, field, enabled)
        if enabled:
            labels.append(permission.replace("create_", "").replace("_", " "))
    description = ", ".join(labels) if labels else "none"
    log_family_action(
        family,
        "member_creation_permissions_updated",
        target_user_id=target_member.user_id,
        reason=f"Allowed creation: {description}.",
    )
    add_family_notification(
        target_member.user_id,
        "family_role",
        f"Your creation permissions in {family.name} were updated.",
        url_for("family.family_detail", family_id=family.id),
    )
    db.session.commit()
    flash("Member creation permissions updated.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-members")


@family_bp.route("/family/<int:family_id>/member/<int:member_id>/restrict/<action>", methods=["POST"])
@login_required
def restrict_family_member(family_id, member_id, action):
    family = Family.query.get_or_404(family_id)
    actor_member = family_member_for_current_user(family)
    target_member = FamilyMember.query.filter_by(id=member_id, family_id=family.id).first_or_404()
    if action not in {"warn", "mute", "suspend"}:
        flash("Invalid member restriction.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    permission = {
        "warn": "warn_members",
        "mute": "mute_members",
        "suspend": "suspend_members",
    }[action]
    if not family_has_permission(actor_member, permission):
        flash("You do not have permission for this Family action.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if target_member.user_id == current_user.id or family_role(target_member) == "owner":
        flash("You cannot apply this action to that member.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if role_rank(family_role(actor_member)) <= role_rank(family_role(target_member)):
        flash("You cannot restrict a member with an equal or higher Family role.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    reason = request.form.get("reason", "").strip()
    duration_days = 1 if action == "mute" else 7 if action == "suspend" else None
    restriction = FamilyMemberRestriction(
        family_id=family.id,
        user_id=target_member.user_id,
        created_by_id=current_user.id,
        restriction_type=action,
        reason=reason,
        ends_at=datetime.utcnow() + timedelta(days=duration_days) if duration_days else None,
        active=True,
    )
    db.session.add(restriction)
    log_family_action(
        family,
        f"member_{action}",
        target_user_id=target_member.user_id,
        reason=reason,
    )
    add_family_notification(
        target_member.user_id,
        "family_moderation",
        f"You received a Family {action} in {family.name}.",
        url_for("family.family_detail", family_id=family.id),
    )
    db.session.commit()
    flash(f"Member {action} recorded.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))
