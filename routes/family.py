from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from extensions import db
from models import Family, FamilyMember, Notification, Post, User

family_bp = Blueprint("family", __name__)


def family_admin_required(family):
    return FamilyMember.query.filter(
        FamilyMember.family_id == family.id,
        FamilyMember.user_id == current_user.id,
        FamilyMember.role == "admin",
    ).first()


@family_bp.route("/families")
@login_required
def families():
    query = request.args.get("q", "").strip()
    family_query = Family.query
    if query:
        search = f"%{query}%"
        family_query = family_query.filter(
            or_(Family.name.ilike(search), Family.description.ilike(search))
        )
    families = family_query.order_by(Family.created_at.desc()).all()
    return render_template("families.html", families=families, query=query)


@family_bp.route("/family/create", methods=["GET", "POST"])
@login_required
def create_family():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        privacy = request.form.get("privacy", "public")
        if privacy not in {"public", "private"}:
            privacy = "public"
        if not name:
            flash("Family name is required.", "warning")
            return render_template("create_family.html")
        family = Family(
            name=name,
            description=description,
            owner_id=current_user.id,
            privacy=privacy,
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
    return render_template("create_family.html")


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
        "family_detail.html", family=family, member=member, members=members, posts=posts
    )


@family_bp.route("/family/<int:family_id>/edit", methods=["GET", "POST"])
@login_required
def edit_family(family_id):
    family = Family.query.get_or_404(family_id)
    if not family_admin_required(family):
        flash("Only family admins can edit family details.", "danger")
        return redirect(url_for("family.family_detail", family_id=family.id))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        privacy = request.form.get("privacy", "public")
        if privacy not in {"public", "private"}:
            privacy = "public"
        if not name:
            flash("Family name is required.", "warning")
            return redirect(url_for("family.edit_family", family_id=family.id))
        family.name = name
        family.description = description
        family.privacy = privacy
        db.session.commit()
        flash("Family updated.", "success")
        return redirect(url_for("family.family_detail", family_id=family.id))
    return render_template("edit_family.html", family=family)


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
