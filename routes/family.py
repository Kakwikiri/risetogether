from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from extensions import db
from helpers import save_media
from models import ChallengeCompletion, Family, FamilyChallenge, FamilyMember, Notification, Post, User

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


def family_admin_required(family):
    return FamilyMember.query.filter(
        FamilyMember.family_id == family.id,
        FamilyMember.user_id == current_user.id,
        FamilyMember.role == "admin",
    ).first()


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


def challenge_is_current(challenge):
    now = datetime.utcnow()
    if challenge.status != "active":
        return False
    if challenge.starts_at and challenge.starts_at > now:
        return False
    if challenge.ends_at and challenge.ends_at < now:
        return False
    return True


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
        "can_create_challenges": bool(current_member and current_member.role == "admin"),
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
    member_limit = None
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
    return render_template("families.html", families=families, query=query)


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
            family_id=family.id, user_id=current_user.id, role="admin"
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
        **challenge_dashboard(family, members, member),
    )


@family_bp.route("/family/<int:family_id>/edit", methods=["GET", "POST"])
@login_required
def edit_family(family_id):
    family = Family.query.get_or_404(family_id)
    if not family_admin_required(family):
        flash("Only family admins can edit family details.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if request.method == "POST":
        payload, error = validate_family_payload(request.form)
        if error:
            flash(error, "warning")
            return redirect(url_for("family.edit_family", family_id=family.id))
        for key, value in payload.items():
            setattr(family, key, value)
        family.is_active = request.form.get("is_active", "1") == "1"
        db.session.commit()
        flash("Family updated.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id))
    return render_template("edit_family.html", family=family, **family_form_context())


@family_bp.route("/family/<int:family_id>/join", methods=["POST"])
@login_required
def join_family(family_id):
    family = Family.query.get_or_404(family_id)
    existing = FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()
    if existing:
        flash("You are already a part of this family.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if not family.is_active:
        flash("This Family is currently paused.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if family.member_limit and family.members.count() >= family.member_limit:
        flash("This Family has reached its member limit.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    member = FamilyMember(family_id=family.id, user_id=current_user.id, role="member")
    db.session.add(member)
    if family.owner_id and family.owner_id != current_user.id:
        notification = Notification(
            user_id=family.owner_id,
            category="family",
            message=f"{current_user.username} joined your family {family.name}.",
        )
        db.session.add(notification)
    db.session.commit()
    flash("You have joined the family.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/challenge/create", methods=["POST"])
@login_required
def create_challenge(family_id):
    family = Family.query.get_or_404(family_id)
    if not family_admin_required(family):
        flash("Only family admins can create official challenges.", "danger")
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


@family_bp.route("/family/<int:family_id>/invite", methods=["POST"])
@login_required
def invite_family_member(family_id):
    family = Family.query.get_or_404(family_id)
    if not family_admin_required(family):
        flash("Only family admins can invite members.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    username = request.form.get("username", "").strip()
    user = User.query.filter_by(username=username).first()
    if not user:
        flash("No user found with that username.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if FamilyMember.query.filter_by(family_id=family.id, user_id=user.id).first():
        flash("This user is already a family member.", "info")
        return redirect(url_for("family.family_detail", family_id=family.id))
    notification = Notification(
        user_id=user.id,
        category="family_invite",
        message=f"You have been invited to join the family {family.name}. Open Families to join.",
        action_url=url_for("family.family_detail", family_id=family.id),
    )
    db.session.add(notification)
    db.session.commit()
    flash("Invite sent. The user can join from notifications.", "success")
    return redirect(url_for("family.family_detail", family_id=family.id))


@family_bp.route("/family/<int:family_id>/member/<int:member_id>/<action>", methods=["POST"])
@login_required
def manage_family_member(family_id, member_id, action):
    family = Family.query.get_or_404(family_id)
    admin_member = family_admin_required(family)
    if not admin_member:
        flash("Only family admins can manage members.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    member = FamilyMember.query.filter_by(id=member_id, family_id=family.id).first_or_404()
    if member.user_id == family.owner_id and action in {"remove", "demote"}:
        flash("The family owner cannot be removed or demoted.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if action == "promote":
        member.role = "admin"
        flash("Member promoted to admin.", "success")
    elif action == "demote":
        member.role = "member"
        flash("Admin privileges removed.", "info")
    elif action == "remove":
        db.session.delete(member)
        flash("Member removed from family.", "info")
    else:
        flash("Invalid member action.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    db.session.commit()
    return redirect(url_for("family.family_detail", family_id=family.id))
