import secrets
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Block, HelpRequest, Post, Report, SiteSetting, User

mod_bp = Blueprint("moderation", __name__)


@mod_bp.route("/report/user/<int:user_id>", methods=["POST"])
@login_required
def report_user(user_id):
    target = User.query.get_or_404(user_id)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please explain why you are reporting this user.", "warning")
        return redirect(url_for("main.profile", username=target.username))
    report = Report(
        reporter_id=current_user.id, reported_user_id=target.id, reason=reason
    )
    db.session.add(report)
    db.session.commit()
    flash("Your report has been submitted for review.", "success")
    return redirect(url_for("main.profile", username=target.username))


@mod_bp.route("/report/post/<int:post_id>", methods=["POST"])
@login_required
def report_post(post_id):
    post = Post.query.get_or_404(post_id)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please explain why you are reporting this post.", "warning")
        return redirect(url_for("main.post_detail", post_id=post.id))
    report = Report(
        reporter_id=current_user.id,
        reported_user_id=post.user_id,
        post_id=post.id,
        reason=reason,
    )
    db.session.add(report)
    db.session.commit()
    flash("The post has been reported and will be reviewed.", "success")
    return redirect(url_for("main.post_detail", post_id=post.id))


@mod_bp.route("/block/<int:user_id>", methods=["POST"])
@login_required
def block_user(user_id):
    target = User.query.get_or_404(user_id)
    existing = Block.query.filter_by(
        blocker_id=current_user.id, blocked_id=target.id
    ).first()
    if existing:
        flash("This user is already blocked.", "info")
        return redirect(url_for("main.profile", username=target.username))
    block = Block(blocker_id=current_user.id, blocked_id=target.id)
    db.session.add(block)
    db.session.commit()
    flash("User blocked. You will no longer see their posts or messages.", "success")
    return redirect(url_for("main.profile", username=target.username))


@mod_bp.route("/admin/reports")
@login_required
def admin_reports():
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    reports = Report.query.order_by(Report.created_at.desc()).all()
    return render_template("admin_reports.html", reports=reports)


@mod_bp.route("/admin/users")
@login_required
def admin_users():
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    users = User.query.order_by(User.created_at.desc()).all()
    location_counts = Counter(user.country or "Unknown" for user in users)
    temp_password = request.args.get("temp_password", "")
    temp_user = request.args.get("temp_user", "")
    return render_template(
        "admin_users.html",
        users=users,
        location_counts=location_counts.most_common(),
        temp_password=temp_password,
        temp_user=temp_user,
    )


@mod_bp.route("/help", methods=["GET", "POST"])
@login_required
def help_request():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not subject or not message:
            flash("Please add a subject and message.", "warning")
            return redirect(url_for("moderation.help_request"))
        db.session.add(
            HelpRequest(user_id=current_user.id, subject=subject, message=message)
        )
        db.session.commit()
        flash("Your help request has been sent to admin.", "success")
        return redirect(url_for("main.home"))
    return render_template("help_request.html")


@mod_bp.route("/admin/help")
@login_required
def admin_help_requests():
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    requests = HelpRequest.query.order_by(HelpRequest.created_at.desc()).all()
    return render_template("admin_help.html", requests=requests)


@mod_bp.route("/admin/help/<int:request_id>/<action>", methods=["POST"])
@login_required
def manage_help_request(request_id, action):
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    help_request = HelpRequest.query.get_or_404(request_id)
    if action in {"open", "reviewed", "closed"}:
        help_request.status = action
        db.session.commit()
        flash("Help request updated.", "success")
    return redirect(url_for("moderation.admin_help_requests"))


@mod_bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    keys = ["google_client_id", "google_client_secret", "smtp_host", "smtp_from", "smtp_username", "smtp_password"]
    if request.method == "POST":
        for key in keys:
            value = request.form.get(key, "").strip()
            if not value and (key.endswith("secret") or key.endswith("password")):
                continue
            setting = SiteSetting.query.get(key) or SiteSetting(key=key)
            setting.value = value
            db.session.merge(setting)
        db.session.commit()
        flash("Admin settings saved.", "success")
        return redirect(url_for("moderation.admin_settings"))
    settings = {}
    for key in keys:
        setting = SiteSetting.query.get(key)
        settings[key] = setting.value if setting else ""
    return render_template("admin_settings.html", settings=settings)


@mod_bp.route("/admin/users/<int:user_id>/toggle-admin", methods=["POST"])
@login_required
def toggle_admin(user_id):
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot remove your own admin access.", "warning")
        return redirect(url_for("moderation.admin_users"))
    user.is_admin = not user.is_admin
    db.session.commit()
    flash("Admin privileges updated.", "success")
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/ban", methods=["POST"])
@login_required
def toggle_ban_user(user_id):
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot ban your own account.", "warning")
        return redirect(request.referrer or url_for("moderation.admin_users"))
    user.is_banned = not user.is_banned
    if not user.is_banned:
        user.ban_until = None
    db.session.commit()
    flash("Account status updated.", "success")
    return redirect(request.referrer or url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@login_required
def admin_user_action(user_id, action):
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id and action in {"temp_ban", "perm_ban", "delete"}:
        flash("You cannot perform that action on your own account.", "warning")
        return redirect(url_for("moderation.admin_users"))
    if action == "warn":
        user.warning_count += 1
        flash("Warning added.", "success")
    elif action == "temp_ban":
        user.is_banned = True
        user.ban_until = datetime.utcnow() + timedelta(days=30)
        flash("User banned for 1 month.", "success")
    elif action == "perm_ban":
        user.is_banned = True
        user.ban_until = None
        flash("User permanently banned.", "success")
    elif action == "unban":
        user.is_banned = False
        user.ban_until = None
        flash("User unbanned.", "success")
    elif action == "verify":
        user.is_verified = not user.is_verified
        flash("Verification badge updated.", "success")
    elif action == "hide_directory":
        user.is_hidden_from_directory = not user.is_hidden_from_directory
        flash("Directory visibility updated.", "success")
    elif action == "reset_password":
        temp_password = f"RT-{secrets.randbelow(900000) + 100000}"
        user.set_password(temp_password)
        db.session.commit()
        flash("Temporary password generated. Share it with the user securely.", "success")
        return redirect(
            url_for(
                "moderation.admin_users",
                temp_user=user.username,
                temp_password=temp_password,
            )
        )
    else:
        flash("Unknown admin action.", "warning")
    db.session.commit()
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "warning")
        return redirect(request.referrer or url_for("moderation.admin_users"))
    db.session.delete(user)
    db.session.commit()
    flash("Account deleted.", "info")
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/reports/<int:report_id>/<action>", methods=["POST"])
@login_required
def manage_report(report_id, action):
    if not current_user.is_admin:
        flash("Admin access required.", "danger")
        return redirect(url_for("main.home"))
    report = Report.query.get_or_404(report_id)
    if action == "reviewed":
        report.status = "reviewed"
        flash("Report marked reviewed.", "success")
    elif action == "delete_post" and report.post:
        db.session.delete(report.post)
        report.status = "actioned"
        flash("Reported post deleted.", "info")
    elif action == "ban_user" and report.reported_user:
        if report.reported_user_id == current_user.id:
            flash("You cannot ban your own account.", "warning")
            return redirect(url_for("moderation.admin_reports"))
        report.reported_user.is_banned = True
        report.status = "actioned"
        flash("Reported account banned.", "success")
    elif action == "delete_user" and report.reported_user:
        if report.reported_user_id == current_user.id:
            flash("You cannot delete your own account.", "warning")
            return redirect(url_for("moderation.admin_reports"))
        db.session.delete(report.reported_user)
        report.status = "actioned"
        flash("Reported account deleted.", "info")
    else:
        flash("That admin action is unavailable for this report.", "warning")
        return redirect(url_for("moderation.admin_reports"))
    db.session.commit()
    return redirect(url_for("moderation.admin_reports"))
