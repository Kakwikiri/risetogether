import os
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from helpers import get_media_type, save_media, send_device_push, validate_upload
from models import (
    ChallengeCompletion,
    Block,
    Family,
    FamilyChallenge,
    FamilyMember,
    FamilyMemberRestriction,
    FamilyModerationLog,
    MediaAsset,
    Message,
    Notification,
    Post,
    Profile,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizChoice,
    QuizQuestion,
    User,
)

family_bp = Blueprint("family", __name__)

FAMILY_CATEGORIES = {
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

CHALLENGE_TYPES = {
    "task": "Task",
    "daily_check_in": "Daily check-in",
    "learning_lesson": "Learning lesson",
    "habit": "Habit",
    "quiz": "Quiz",
}

CHALLENGE_STATUSES = {"active", "draft", "closed"}

QUIZ_CAPABLE_CATEGORIES = {
    "learning",
    "quiz_and_trivia",
    "coding",
    "books",
    "language_learning",
}

QUIZ_STATUSES = {"open", "draft", "closed"}

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
    "create_challenge": {"owner", "admin"},
    "create_quiz": {"owner", "admin"},
    "invite_members": {"owner", "admin"},
    "delete_family": {"owner"},
}


def default_family_member_limit():
    try:
        return max(2, int(current_app.config.get("DEFAULT_FAMILY_MEMBER_LIMIT", 50)))
    except (TypeError, ValueError):
        return 50


def effective_family_member_limit(family):
    return family.member_limit or default_family_member_limit()


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
        "remaining_slots": max(0, limit - count),
        "is_full": count >= limit,
    }


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


def add_family_notification(user_id, category, message, action_url):
    notification = Notification(
        user_id=user_id,
        category=category,
        message=message,
        action_url=action_url,
    )
    db.session.add(notification)
    db.session.flush()
    send_device_push(notification)
    return notification


def normalize_family_role(role):
    return role if role in FAMILY_ROLES else "member"


def family_role(member):
    return normalize_family_role(member.role) if member else None


def family_has_permission(member, permission):
    return family_role(member) in FAMILY_PERMISSIONS.get(permission, set())


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
    active_challenges = [challenge for challenge in challenges if challenge_is_current(challenge)]
    completions = ChallengeCompletion.query.join(FamilyChallenge).filter(
        FamilyChallenge.family_id == family.id
    ).all()
    completed_challenge_ids = {
        completion.challenge_id
        for completion in completions
        if completion.user_id == current_user.id
    }
    completion_counts = {}
    member_points = {membership.user_id: 0 for membership in members}
    member_completed = {membership.user_id: 0 for membership in members}
    challenge_points = {challenge.id: challenge.points for challenge in challenges}
    for completion in completions:
        completion_counts[completion.challenge_id] = completion_counts.get(completion.challenge_id, 0) + 1
        if completion.user_id in member_points:
            member_points[completion.user_id] += challenge_points.get(completion.challenge_id, 0)
            member_completed[completion.user_id] += 1
    total_possible = len(active_challenges) * max(len(members), 1)
    completed_total = sum(
        completion_counts.get(challenge.id, 0) for challenge in active_challenges
    )
    family_progress = round((completed_total / total_possible) * 100) if total_possible else None
    return {
        "challenges": challenges,
        "active_challenges": active_challenges,
        "completed_challenge_ids": completed_challenge_ids,
        "completion_counts": completion_counts,
        "member_points": member_points,
        "member_completed": member_completed,
        "family_progress": family_progress,
        "can_create_challenges": family_has_permission(current_member, "create_challenge"),
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
            quiz_points[attempt.user_id] += attempt.score
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
            family_has_permission(current_member, "create_quiz") and family_supports_quizzes(family)
        ),
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


def family_home_dashboard(family, members):
    now = datetime.utcnow()
    week_start = now - timedelta(days=7)
    active_challenges = [challenge for challenge in family.challenges.all() if challenge_is_current(challenge)]
    challenge_ids = [challenge.id for challenge in family.challenges.all()]
    active_challenge_ids = {challenge.id for challenge in active_challenges}
    completions = (
        ChallengeCompletion.query.filter(ChallengeCompletion.challenge_id.in_(challenge_ids)).all()
        if challenge_ids
        else []
    )
    attempts = QuizAttempt.query.join(Quiz).filter(
        Quiz.family_id == family.id,
        QuizAttempt.submitted_at != None,
    ).all()
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
    challenge_points = {challenge.id: challenge.points for challenge in family.challenges.all()}
    completed_active_by_user = {membership.user_id: set() for membership in members}
    weekly_achievements = []
    for completion in completions:
        if completion.user_id not in stats_by_user:
            continue
        stats = stats_by_user[completion.user_id]
        stats["completed_challenges"] += 1
        stats["challenge_points"] += challenge_points.get(completion.challenge_id, 0)
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
        stats["quiz_points"] += attempt.score
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
    return {
        "dashboard_active_challenges": active_challenges[:5],
        "dashboard_member_progress": member_progress,
        "dashboard_recent_posts": family_posts[:4],
        "dashboard_recent_messages": family_messages[:5],
        "dashboard_upcoming_quiz": upcoming_quiz,
        "dashboard_weekly_achievements": weekly_achievements[:6],
    }


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
    families = family_query.order_by(Family.created_at.desc()).all()
    capacity_by_family = {family.id: family_capacity_status(family) for family in families}
    return render_template(
        "families.html",
        families=families,
        query=query,
        capacity_by_family=capacity_by_family,
    )


@family_bp.route("/family/create", methods=["GET", "POST"])
@login_required
def create_family():
    if request.method == "POST":
        payload, error = validate_family_payload(request.form)
        if error:
            flash(error, "warning")
            return render_template("create_family.html", **family_form_context(form=request.form))
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
    members = FamilyMember.query.filter_by(family_id=family.id).all()
    capacity = family_capacity_status(family)
    posts = (
        family.posts.filter(or_(Post.is_hidden == False, Post.user_id == current_user.id))
        .order_by(Post.created_at.desc())
        .all()
    )
    return render_template(
        "family_detail.html",
        family=family,
        member=member,
        members=members,
        posts=posts,
        categories=FAMILY_CATEGORIES,
        challenge_types=CHALLENGE_TYPES,
        role_labels=FAMILY_ROLE_LABELS,
        can_edit_family=family_has_permission(member, "edit_family"),
        can_change_family_image=family_has_permission(member, "change_family_image"),
        can_manage_roles=family_has_permission(member, "manage_roles"),
        can_manage_members=family_has_permission(member, "manage_members"),
        can_warn_members=family_has_permission(member, "warn_members"),
        can_suspend_members=family_has_permission(member, "suspend_members"),
        can_invite_members=family_has_permission(member, "invite_members"),
        active_member_count=capacity["member_count"],
        effective_member_limit=capacity["member_limit"],
        family_is_full=capacity["is_full"],
        remaining_slots=capacity["remaining_slots"],
        **family_home_dashboard(family, members),
        **challenge_dashboard(family, members, member),
        **quiz_dashboard(family, members, member),
    )


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
        active_count = active_family_member_count(family)
        if payload["member_limit"] < active_count:
            flash(
                f"Member limit cannot be below the current active member count ({active_count}).",
                "warning",
            )
            return redirect(url_for("family.edit_family", family_id=family.id))
        for key, value in payload.items():
            setattr(family, key, value)
        family.is_active = request.form.get("is_active", "1") == "1"
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


@family_bp.route("/family/<int:family_id>/challenge/create", methods=["POST"])
@login_required
def create_challenge(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "create_challenge"):
        flash("Only Family owners and admins can create official challenges.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    challenge_type = request.form.get("challenge_type", "task").strip()
    status = request.form.get("status", "active").strip()
    points = parse_points(request.form.get("points"))
    starts_at = parse_family_date(request.form.get("starts_at"))
    ends_at = parse_family_date(request.form.get("ends_at"))
    if ends_at:
        ends_at = ends_at.replace(hour=23, minute=59, second=59)
    if not title:
        flash("Challenge title is required.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if challenge_type not in CHALLENGE_TYPES:
        flash("Choose a valid challenge type.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    if status not in CHALLENGE_STATUSES:
        status = "active"
    if points is None:
        flash("Challenge points must be a positive number.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    challenge = FamilyChallenge(
        family_id=family.id,
        creator_id=current_user.id,
        title=title,
        description=description,
        challenge_type=challenge_type,
        points=points,
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
    )
    db.session.add(challenge)
    db.session.flush()
    if challenge.status == "active":
        for membership in family.members:
            if membership.user_id != current_user.id:
                add_family_notification(
                    membership.user_id,
                    "challenge_created",
                    f"New challenge in {family.name}: {challenge.title}",
                    url_for("family.family_detail", family_id=family.id) + "#family-challenges",
                )
    db.session.commit()
    flash("Challenge created.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")


@family_bp.route("/family/<int:family_id>/challenge/<int:challenge_id>/complete", methods=["POST"])
@login_required
def complete_challenge(family_id, challenge_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not member:
        flash("Join this Family before completing challenges.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    challenge = FamilyChallenge.query.filter_by(
        id=challenge_id, family_id=family.id
    ).first_or_404()
    if not challenge_is_current(challenge):
        flash("This challenge is not active.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    existing = ChallengeCompletion.query.filter_by(
        challenge_id=challenge.id, user_id=current_user.id
    ).first()
    if existing:
        flash("You already completed this challenge.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")
    evidence_text = request.form.get("evidence_text", "").strip()
    evidence_file = request.files.get("evidence_media")
    evidence_media_url = ""
    if evidence_file and evidence_file.filename:
        filename = save_media(evidence_file)
        if filename:
            evidence_media_url = filename
    completion = ChallengeCompletion(
        challenge_id=challenge.id,
        user_id=current_user.id,
        evidence_text=evidence_text,
        evidence_media_url=evidence_media_url,
        verification_status="completed",
    )
    db.session.add(completion)
    db.session.commit()
    flash("Challenge marked complete.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id) + "#family-challenges")


@family_bp.route("/family/<int:family_id>/quiz/create", methods=["POST"])
@login_required
def create_quiz(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "create_quiz"):
        flash("Only Family owners and admins can create quizzes.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if not family_supports_quizzes(family):
        flash("This Family type does not use quizzes.", "warning")
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
    quiz = Quiz(
        family_id=family.id,
        creator_id=current_user.id,
        title=title,
        description=description,
        opens_at=opens_at,
        closes_at=closes_at,
        time_limit_seconds=time_limit_seconds,
        status=status,
        allow_multiple_attempts=request.form.get("allow_multiple_attempts") == "1",
        show_correct_answers=request.form.get("show_correct_answers", "1") == "1",
    )
    db.session.add(quiz)
    db.session.flush()
    question_count = 0
    for position in range(1, 6):
        question_text = request.form.get(f"question_{position}", "").strip()
        if not question_text:
            continue
        points = parse_points(request.form.get(f"question_{position}_points")) or 1
        correct_choice = request.form.get(f"question_{position}_correct", "").strip()
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_text=question_text,
            question_type="multiple_choice",
            points=points,
            position=position,
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
    existing_attempt = QuizAttempt.query.filter_by(
        quiz_id=quiz.id, user_id=current_user.id
    ).filter(
        QuizAttempt.submitted_at != None
    ).order_by(QuizAttempt.submitted_at.desc()).first()
    attempt_id = request.args.get("attempt_id")
    selected_attempt = None
    if attempt_id:
        selected_attempt = QuizAttempt.query.filter_by(
            id=attempt_id, quiz_id=quiz.id, user_id=current_user.id
        ).first()
    if selected_attempt or (existing_attempt and not quiz.allow_multiple_attempts):
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
    if request.method == "POST":
        attempt = QuizAttempt(quiz_id=quiz.id, user_id=current_user.id)
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
        attempt.score = score
        attempt.submitted_at = datetime.utcnow()
        db.session.commit()
        flash("Quiz submitted.", "success")
        return redirect(
            url_for("family.take_quiz", family_id=family.id, quiz_id=quiz.id, attempt_id=attempt.id)
        )
    return render_template(
        "quiz_take.html",
        family=family,
        quiz=quiz,
        questions=questions,
        attempt=None,
        answers={},
        show_results=False,
    )


@family_bp.route("/family/<int:family_id>/invite", methods=["POST"])
@login_required
def invite_family_member(family_id):
    family = Family.query.get_or_404(family_id)
    member = family_member_for_current_user(family)
    if not family_has_permission(member, "invite_members"):
        flash("Only Family owners and admins can invite members.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    username = request.form.get("username", "").strip()
    user = User.query.filter_by(username=username).first()
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
