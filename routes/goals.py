from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from feature_flags import is_feature_enabled
from helpers import get_media_type, save_media, validate_upload
from models import (
    Family, FamilyMember, Goal, GoalActivity, GoalEncouragement, GoalMilestone,
    GoalParticipant, GoalProgress, Notification, PointTransaction, Post, User,
)
from points import PointLimitExceeded, award_points
from notifications_service import smart_notify

goals_bp = Blueprint("goals", __name__)

GOAL_CATEGORIES = {
    "wellbeing": "Wellbeing", "habit": "Habit", "learning": "Learning", "fitness": "Fitness",
    "saving": "Saving", "career": "Career or work", "relationships": "Relationships",
    "creative": "Creative", "family_growth": "Family growth", "other": "Other",
}
MEASUREMENT_TYPES = {"number": "Number", "percentage": "Percentage", "binary": "Done / not done"}
GOAL_REACTIONS = {"support": "Support", "understand": "I Understand", "keep_going": "Keep Going", "inspire": "You Inspire Me"}


def family_membership(family_id, user_id=None):
    return FamilyMember.query.filter_by(family_id=family_id, user_id=user_id or current_user.id).first()


def family_goal_admin(goal):
    membership = family_membership(goal.family_id) if goal.family_id else None
    return bool(membership and membership.role in {"owner", "admin"})


def goal_can_view(goal):
    if goal.owner_user_id == current_user.id:
        return True
    if goal.visibility == "public":
        return True
    if goal.scope == "family" and family_membership(goal.family_id):
        return goal.visibility == "family" or family_goal_admin(goal)
    return False


def goal_can_update(goal):
    if goal.owner_user_id == current_user.id or family_goal_admin(goal):
        return True
    return GoalParticipant.query.filter_by(goal_id=goal.id, user_id=current_user.id).first() is not None


def parse_date(value, required=False):
    if not value:
        return None if not required else False
    try:
        return date.fromisoformat(value)
    except ValueError:
        return False


def queue_notification(user_id, message, action_url):
    smart_notify(user_id=user_id, category="goal_progress", message=message, action_url=action_url)


@goals_bp.route("/goals")
@login_required
def goals_dashboard():
    family_ids = [membership.family_id for membership in current_user.family_memberships]
    goals = Goal.query.filter(or_(
        Goal.owner_user_id == current_user.id,
        (Goal.scope == "family") & (Goal.family_id.in_(family_ids or [-1])) & (Goal.visibility.in_(["family", "public"])),
        Goal.visibility == "public",
    )).order_by(Goal.status.asc(), Goal.created_at.desc()).limit(150).all()
    goals = [goal for goal in goals if goal_can_view(goal)]
    return render_template("goals.html", goals=goals, today=date.today())


@goals_bp.route("/goals/create", methods=["GET", "POST"])
@login_required
def create_goal():
    memberships = current_user.family_memberships.all()
    if request.method == "POST":
        scope = request.form.get("scope", "personal").strip()
        family_id = request.form.get("family_id", type=int)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        measurement = request.form.get("measurement_type", "number").strip()
        visibility = request.form.get("visibility", "private").strip()
        start_date = parse_date(request.form.get("start_date"), required=True)
        target_date = parse_date(request.form.get("target_date"))
        try:
            target_amount = float(request.form.get("target_amount", "1"))
        except ValueError:
            target_amount = 0
        if scope not in {"personal", "family"} or category not in GOAL_CATEGORIES or measurement not in MEASUREMENT_TYPES:
            flash("Choose valid goal options.", "warning")
            return redirect(request.url)
        if len(title) < 3 or len(title) > 160 or len(description) > 3000:
            flash("Add a clear title and keep the description under 3,000 characters.", "warning")
            return redirect(request.url)
        if start_date is False or target_date is False or (target_date and target_date < start_date):
            flash("Choose valid goal dates. The target date cannot be before the start date.", "warning")
            return redirect(request.url)
        if measurement == "binary":
            target_amount = 1
        elif measurement == "percentage":
            target_amount = 100
        if target_amount <= 0 or target_amount > 1_000_000_000:
            flash("Target amount must be greater than zero and within a safe range.", "warning")
            return redirect(request.url)
        family = None
        if scope == "family":
            family = Family.query.get(family_id) if family_id else None
            membership = family_membership(family_id) if family else None
            if not membership or membership.role not in {"owner", "admin"}:
                flash("Only Family owners and admins can create Family goals.", "danger")
                return redirect(request.url)
            if visibility == "private":
                visibility = "family"
        else:
            family_id = None
            if visibility == "family":
                visibility = "private"
        if visibility not in {"private", "family", "public"}:
            visibility = "private"
        active_query = Goal.query.filter_by(scope=scope, status="active")
        active_query = active_query.filter_by(family_id=family_id) if scope == "family" else active_query.filter_by(owner_user_id=current_user.id)
        if active_query.count() >= 10:
            flash("Finish or archive an active goal before creating another. Ten active goals is the current limit.", "warning")
            return redirect(request.url)
        goal = Goal(
            scope=scope, owner_user_id=current_user.id, family_id=family_id,
            title=title, description=description, category=category,
            start_date=start_date, target_date=target_date,
            measurement_type=measurement, target_amount=target_amount,
            visibility=visibility,
        )
        db.session.add(goal)
        db.session.flush()
        db.session.add(GoalParticipant(goal_id=goal.id, user_id=current_user.id))
        milestone_lines = request.form.get("milestones", "").splitlines()
        targets = set()
        for line in milestone_lines[:20]:
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) != 2:
                db.session.rollback(); flash("Write milestones as Title | target amount.", "warning"); return redirect(request.url)
            try:
                milestone_target = float(parts[1])
            except ValueError:
                milestone_target = 0
            if len(parts[0]) < 2 or milestone_target <= 0 or milestone_target >= target_amount or milestone_target in targets:
                db.session.rollback(); flash("Milestone targets must be unique, positive, and below the goal target.", "warning"); return redirect(request.url)
            targets.add(milestone_target)
            db.session.add(GoalMilestone(goal_id=goal.id, title=parts[0][:160], target_amount=milestone_target))
        selected_ids = {int(value) for value in request.form.getlist("participant_ids") if value.isdigit()}
        if family:
            valid_ids = {member.user_id for member in family.members.filter(FamilyMember.user_id.in_(selected_ids)).all()} if selected_ids else set()
            for user_id in valid_ids - {current_user.id}:
                db.session.add(GoalParticipant(goal_id=goal.id, user_id=user_id))
        db.session.add(GoalActivity(goal_id=goal.id, user_id=current_user.id, event_type="created", message="Goal created."))
        db.session.commit()
        flash("Goal created. Small progress still counts.", "success")
        return redirect(url_for("goals.goal_detail", goal_id=goal.id))
    return render_template("goal_create.html", categories=GOAL_CATEGORIES, measurements=MEASUREMENT_TYPES, memberships=memberships, today=date.today())


@goals_bp.route("/goals/<int:goal_id>")
@login_required
def goal_detail(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    if not goal_can_view(goal):
        abort(404)
    percentage = min(100, round((goal.current_progress / goal.target_amount) * 100))
    return render_template(
        "goal_detail.html", goal=goal, percentage=percentage, today=date.today(),
        can_update=goal_can_update(goal), reactions=GOAL_REACTIONS,
        GoalMilestone=GoalMilestone, GoalProgress=GoalProgress,
        is_participant=GoalParticipant.query.filter_by(goal_id=goal.id, user_id=current_user.id).first() is not None,
    )


@goals_bp.route("/goals/<int:goal_id>/participate/<action>", methods=["POST"])
@login_required
def goal_participate(goal_id, action):
    goal = Goal.query.get_or_404(goal_id)
    if goal.scope != "family" or not family_membership(goal.family_id) or not goal_can_view(goal):
        abort(403)
    participant = GoalParticipant.query.filter_by(goal_id=goal.id, user_id=current_user.id).first()
    if action == "join" and not participant:
        db.session.add(GoalParticipant(goal_id=goal.id, user_id=current_user.id))
        db.session.add(GoalActivity(goal_id=goal.id, user_id=current_user.id, event_type="participant_joined", message=f"{current_user.username} joined the goal."))
    elif action == "leave" and participant and goal.owner_user_id != current_user.id:
        db.session.delete(participant)
    else:
        flash("That participation change is not available.", "info")
        return redirect(url_for("goals.goal_detail", goal_id=goal.id))
    db.session.commit()
    return redirect(url_for("goals.goal_detail", goal_id=goal.id))


def award_goal_event(goal, actor_id, event_type, source_id):
    if not goal.created_at or goal.created_at > datetime.utcnow() - timedelta(hours=24):
        return
    amount = 5 if event_type == "milestone" else 10
    if goal.scope == "personal" and is_feature_enabled("personal_points"):
        award_points(amount=amount, reason=f"Goal {event_type}: {goal.title}", source_type=f"goal_{event_type}", source_id=source_id, unique_reward_key=f"goal:{goal.id}:{event_type}:{source_id}:user", user_id=goal.owner_user_id, repeatable=True, daily_limit=25)
    elif goal.scope == "family" and is_feature_enabled("family_points"):
        start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        family_goal_points_today = db.session.query(func.coalesce(func.sum(PointTransaction.amount), 0)).filter(
            PointTransaction.family_id == goal.family_id,
            PointTransaction.source_type.in_(["goal_milestone", "goal_completed"]),
            PointTransaction.reversed == False,
            PointTransaction.created_at >= start_of_day,
        ).scalar()
        if family_goal_points_today + amount > 50:
            return
        award_points(amount=amount, reason=f"Family goal {event_type}: {goal.title}", source_type=f"goal_{event_type}", source_id=source_id, unique_reward_key=f"goal:{goal.id}:{event_type}:{source_id}:family", family_id=goal.family_id, awarded_by_id=actor_id)


@goals_bp.route("/goals/<int:goal_id>/progress", methods=["POST"])
@login_required
def add_goal_progress(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    if not goal_can_view(goal) or not goal_can_update(goal) or goal.status != "active":
        abort(403)
    note = request.form.get("note", "").strip()
    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        amount = 0
    if goal.measurement_type == "binary":
        amount = goal.target_amount - goal.current_progress
    if amount <= 0 or amount > goal.target_amount - goal.current_progress or len(note) > 500:
        flash("Progress must be positive, within the remaining target, and use a short note.", "warning")
        return redirect(url_for("goals.goal_detail", goal_id=goal.id))
    evidence_url = ""; evidence_type = ""
    evidence = request.files.get("evidence")
    if evidence and evidence.filename:
        valid, message = validate_upload(evidence)
        if not valid:
            flash(message, "warning"); return redirect(url_for("goals.goal_detail", goal_id=goal.id))
        evidence_url = save_media(evidence)
        if not evidence_url:
            flash("Evidence could not be saved.", "warning"); return redirect(url_for("goals.goal_detail", goal_id=goal.id))
        evidence_type = get_media_type(evidence.filename)
    entry = GoalProgress(goal_id=goal.id, user_id=current_user.id, amount=amount, note=note, evidence_url=evidence_url, evidence_type=evidence_type)
    db.session.add(entry); db.session.flush()
    goal.current_progress = min(goal.target_amount, goal.current_progress + amount)
    db.session.add(GoalActivity(goal_id=goal.id, user_id=current_user.id, event_type="progress", message=f"Progress increased by {amount:g}."))
    now = datetime.utcnow()
    for milestone in goal.milestones.filter(GoalMilestone.completed_at == None, GoalMilestone.target_amount <= goal.current_progress).all():
        milestone.completed_at = now
        db.session.add(GoalActivity(goal_id=goal.id, user_id=current_user.id, event_type="milestone", message=f"Milestone achieved: {milestone.title}."))
        milestone_percent = round((milestone.target_amount / goal.target_amount) * 100)
        if milestone_percent in {25, 50, 75}:
            try:
                award_goal_event(goal, current_user.id, "milestone", milestone.id)
            except PointLimitExceeded:
                pass
    completed_now = goal.current_progress >= goal.target_amount
    if completed_now:
        goal.status = "completed"; goal.completed_at = now
        db.session.add(GoalActivity(goal_id=goal.id, user_id=current_user.id, event_type="completed", message="Goal achieved."))
        try:
            award_goal_event(goal, current_user.id, "completed", goal.id)
        except PointLimitExceeded:
            pass
    if goal.owner_user_id and goal.owner_user_id != current_user.id:
        smart_notify(
            user_id=goal.owner_user_id, category="goal_progress",
            message=f"{current_user.username} added {amount:g} toward {goal.title}.",
            action_url=url_for("goals.goal_detail", goal_id=goal.id) + f"#progress-{entry.id}",
            group_key=f"goal-progress:{goal.id}", dedupe_key=f"goal-progress:{entry.id}:{goal.owner_user_id}",
        )
    db.session.commit()
    flash("Goal achieved! Choose whether you want to share it." if completed_now else "Progress added. Every honest step matters.", "success")
    return redirect(url_for("goals.share_goal_achievement", goal_id=goal.id) if completed_now else url_for("goals.goal_detail", goal_id=goal.id))


@goals_bp.route("/goals/<int:goal_id>/encourage", methods=["POST"])
@login_required
def encourage_goal(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    if not goal_can_view(goal): abort(404)
    reaction = request.form.get("reaction", "").strip(); message = request.form.get("message", "").strip()
    if reaction not in GOAL_REACTIONS or len(message) > 500:
        flash("Choose a supportive reaction and a short message.", "warning"); return redirect(url_for("goals.goal_detail", goal_id=goal.id))
    encouragement = GoalEncouragement.query.filter_by(goal_id=goal.id, user_id=current_user.id).first()
    if encouragement: encouragement.reaction, encouragement.message = reaction, message
    else: db.session.add(GoalEncouragement(goal_id=goal.id, user_id=current_user.id, reaction=reaction, message=message))
    if goal.owner_user_id != current_user.id:
        queue_notification(goal.owner_user_id, f"{current_user.username} encouraged your goal.", url_for("goals.goal_detail", goal_id=goal.id))
    db.session.commit(); flash("Encouragement sent.", "success")
    return redirect(url_for("goals.goal_detail", goal_id=goal.id))


@goals_bp.route("/goals/<int:goal_id>/share", methods=["GET", "POST"])
@login_required
def share_goal_achievement(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    completion_event = goal.activities.filter_by(event_type="completed").order_by(GoalActivity.created_at.desc()).first()
    may_share = goal.owner_user_id == current_user.id or (
        goal.scope == "family" and completion_event and completion_event.user_id == current_user.id
    )
    if not may_share or goal.status != "completed": abort(403)
    if goal.achievement_post:
        return redirect(url_for("main.post_detail", post_id=goal.achievement_post.id))
    if request.method == "POST":
        audience = request.form.get("audience", "private")
        if audience not in {"private", "family", "public"}: abort(400)
        if audience == "family" and goal.scope != "family":
            flash("Personal goals can be kept private or shared publicly.", "warning"); return redirect(request.url)
        if audience == "public" and goal.scope == "family" and goal.family.privacy != "public":
            flash("A private Family goal cannot be shared publicly.", "warning"); return redirect(request.url)
        post = Post(user_id=current_user.id, family_id=goal.family_id if audience == "family" else None, content=f"Goal achieved: {goal.title}", audience=audience, post_type="achievement", achievement_type="goal_achieved", goal_id=goal.id, encouraging_message="Progress is worth celebrating, at your own pace.")
        db.session.add(post)
        try: db.session.commit()
        except IntegrityError:
            db.session.rollback(); post = Post.query.filter_by(goal_id=goal.id).first()
        flash("Achievement kept private." if audience == "private" else "Achievement shared.", "success")
        return redirect(url_for("main.post_detail", post_id=post.id))
    return render_template("goal_share.html", goal=goal)
