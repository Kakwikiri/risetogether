import secrets
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, fresh_login_required, login_required

from extensions import db
from feature_flags import (
    FEATURE_FLAG_DEFINITIONS,
    feature_flag_key,
    get_feature_flags,
    is_feature_enabled,
)
from family_levels import DEFAULT_FAMILY_LEVELS, DEFAULT_RISING_INTERVAL
from models import (
    AuditLog, Block, ChallengeCompletion, EncouragementRequestReport, Family, HelpRequest, Notification,
    PointSecurityEvent, PointTransaction, Post, Report, RiseBadgeAssignment, SiteSetting, User,
)
from points import reverse_completion_rewards_for_user, reverse_reward_group
from ownership import is_platform_owner, platform_owner_username

mod_bp = Blueprint("moderation", __name__)

ADMIN_ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "moderator": "Moderator",
    "": "Member",
}
ADMIN_ROLE_RANK = {
    "super_admin": 3,
    "admin": 2,
    "moderator": 1,
    "": 0,
}


def website_role(user):
    role = getattr(user, "admin_role", "") or ""
    if role in ADMIN_ROLE_RANK and role:
        return role
    return "admin" if getattr(user, "is_admin", False) else ""


def role_rank(user):
    return ADMIN_ROLE_RANK.get(website_role(user), 0)


def has_admin_role(minimum_role="moderator"):
    return current_user.is_authenticated and role_rank(current_user) >= ADMIN_ROLE_RANK[minimum_role]


def require_admin_role(minimum_role="moderator"):
    if has_admin_role(minimum_role):
        return True
    flash("Admin access required.", "danger")
    return False


def sync_admin_flag(user):
    # admin_role is the source of truth while changing roles. Reading the
    # legacy flag here would turn an intended Member demotion back into Admin.
    user.is_admin = (getattr(user, "admin_role", "") or "") in {
        "super_admin", "admin", "moderator"
    }


def active_super_admin_count():
    return User.query.filter(
        User.is_admin == True,
        User.admin_role == "super_admin",
        User.is_banned == False,
    ).count()


def can_act_on(target, action="manage"):
    if is_platform_owner(target):
        flash("The platform owner account is protected from role, ban, and deletion actions.", "warning")
        return False
    if target.id == current_user.id and action in {"temp_ban", "perm_ban", "delete", "demote", "role"}:
        flash("You cannot perform that action on your own account.", "warning")
        return False
    if is_platform_owner(current_user):
        return True
    if role_rank(target) and role_rank(current_user) <= role_rank(target):
        flash("You cannot manage an account with an equal or higher website role.", "danger")
        return False
    return True


def record_admin_audit(
    action_type,
    target_user=None,
    target_family=None,
    target_content_id=None,
    reason="",
    metadata_text="",
):
    db.session.add(
        AuditLog(
            actor_user_id=current_user.id if current_user.is_authenticated else None,
            actor_role=website_role(current_user) if current_user.is_authenticated else "",
            action_type=action_type,
            target_user_id=target_user.id if target_user else None,
            target_family_id=target_family.id if target_family else None,
            target_content_id=target_content_id,
            reason=reason,
            metadata_text=metadata_text,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
        )
    )


@mod_bp.route("/admin")
@login_required
def admin_dashboard():
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    current_role = website_role(current_user)
    stats = {
        "total_users": User.query.count(),
        "active_users": User.query.filter(User.is_banned == False).count(),
        "suspended_users": User.query.filter(
            User.is_banned == True,
            User.ban_until != None,
        ).count(),
        "banned_users": User.query.filter(
            User.is_banned == True,
            User.ban_until == None,
        ).count(),
        "total_families": Family.query.count(),
        "active_families": Family.query.filter(Family.is_active == True).count(),
        "suspended_families": Family.query.filter(Family.is_active == False).count(),
        "pending_reports": Report.query.filter_by(status="open").count(),
        "new_registrations": User.query.filter(User.created_at >= seven_days_ago).count(),
        "open_help_requests": HelpRequest.query.filter_by(status="open").count(),
    }
    if current_role == "super_admin":
        stats["website_admins"] = User.query.filter(
            User.admin_role.in_(["super_admin", "admin", "moderator"])
        ).count()
        stats["audit_logs"] = AuditLog.query.count()
    recent_reports = Report.query.order_by(Report.created_at.desc()).limit(5).all()
    recent_help_requests = HelpRequest.query.order_by(HelpRequest.created_at.desc()).limit(5).all()
    return render_template(
        "admin_dashboard.html",
        stats=stats,
        current_admin_role=current_role,
        recent_reports=recent_reports,
        recent_help_requests=recent_help_requests,
    )


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
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    reports = Report.query.order_by(Report.created_at.desc()).all()
    return render_template("admin_reports.html", reports=reports, is_platform_owner_view=is_platform_owner(current_user))


@mod_bp.route("/admin/encouragement-reports")
@login_required
def admin_encouragement_reports():
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    reports = EncouragementRequestReport.query.order_by(
        EncouragementRequestReport.status.asc(), EncouragementRequestReport.created_at.desc()
    ).limit(200).all()
    return render_template("admin_encouragement_reports.html", reports=reports, is_platform_owner_view=is_platform_owner(current_user))


@mod_bp.route("/admin/encouragement-reports/<int:report_id>/<action>", methods=["POST"])
@login_required
def review_encouragement_report(report_id, action):
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    report = EncouragementRequestReport.query.get_or_404(report_id)
    if action == "delete":
        if not is_platform_owner(current_user):
            flash("Only the platform owner can permanently delete a report record.", "danger")
            return redirect(url_for("moderation.admin_encouragement_reports"))
        record_admin_audit(
            "encouragement_report_deleted", target_user=report.request.requester,
            target_family=report.request.family, target_content_id=report.request_id,
            reason=report.reason,
        )
        db.session.delete(report)
        db.session.commit()
        flash("Report record deleted. The original request was not changed.", "success")
        return redirect(url_for("moderation.admin_encouragement_reports"))
    if action == "remove":
        report.request.status = "removed"
        report.status = "removed"
        outcome = "Encouragement request removed."
    elif action == "dismiss":
        report.status = "dismissed"
        outcome = "Report dismissed."
    else:
        flash("Unknown review action.", "warning")
        return redirect(url_for("moderation.admin_encouragement_reports"))
    record_admin_audit(
        "encouragement_report_review", target_user=report.request.requester,
        target_family=report.request.family, target_content_id=report.request_id,
        reason=report.reason, metadata_text=f"Outcome: {action}",
    )
    db.session.commit()
    flash(outcome, "success")
    return redirect(url_for("moderation.admin_encouragement_reports"))


@mod_bp.route("/admin/users")
@login_required
def admin_users():
    if not require_admin_role("admin"):
        return redirect(url_for("main.home"))
    query_text = request.args.get("q", "").strip()
    users_query = User.query
    if website_role(current_user) != "super_admin":
        users_query = users_query.filter(
            (User.admin_role == None) | (User.admin_role == ""),
            User.is_admin == False,
        )
    if query_text:
        like = f"%{query_text}%"
        search_filter = (User.username.ilike(like)) | (User.email.ilike(like))
        if query_text.isdigit():
            search_filter = search_filter | (User.id == int(query_text))
        users_query = users_query.filter(search_filter)
    users = users_query.order_by(User.created_at.desc()).all()
    location_counts = Counter(user.country or "Unknown" for user in users)
    temp_password = request.args.get("temp_password", "")
    temp_user = request.args.get("temp_user", "")
    return render_template(
        "admin_users.html",
        users=users,
        location_counts=location_counts.most_common(),
        temp_password=temp_password,
        temp_user=temp_user,
        query_text=query_text,
        role_labels=ADMIN_ROLE_LABELS,
        current_admin_role=website_role(current_user),
        is_platform_owner_view=is_platform_owner(current_user),
        platform_owner_name=platform_owner_username(),
    )


@mod_bp.route("/admin/families")
@login_required
def admin_families():
    if not require_admin_role("admin"):
        return redirect(url_for("main.home"))
    query_text = request.args.get("q", "").strip()
    status = request.args.get("status", "all").strip()
    families_query = Family.query
    if query_text:
        like = f"%{query_text}%"
        owner_ids = [
            user.id
            for user in User.query.filter(
                (User.username.ilike(like)) | (User.email.ilike(like))
            ).all()
        ]
        search_filter = (Family.name.ilike(like)) | (Family.category.ilike(like))
        if owner_ids:
            search_filter = search_filter | Family.owner_id.in_(owner_ids)
        families_query = families_query.filter(search_filter)
    if status == "active":
        families_query = families_query.filter(Family.is_active == True)
    elif status == "suspended":
        families_query = families_query.filter(Family.is_active == False)
    families = families_query.order_by(Family.created_at.desc()).all()
    owner_ids = {family.owner_id for family in families if family.owner_id}
    owners = {
        user.id: user
        for user in User.query.filter(User.id.in_(owner_ids)).all()
    } if owner_ids else {}
    member_counts = {
        family.id: family.members.count()
        for family in families
    }
    return render_template(
        "admin_families.html",
        families=families,
        owners=owners,
        member_counts=member_counts,
        query_text=query_text,
        status=status,
        current_admin_role=website_role(current_user),
        is_platform_owner_view=is_platform_owner(current_user),
        platform_owner_name=platform_owner_username(),
    )


@mod_bp.route("/admin/families/<int:family_id>/<action>", methods=["POST"])
@fresh_login_required
def admin_family_action(family_id, action):
    if not require_admin_role("admin"):
        return redirect(url_for("main.home"))
    family = Family.query.get_or_404(family_id)
    if action == "suspend":
        family.is_active = False
        record_admin_audit("family_suspension", target_family=family, reason="Suspended from admin Families page")
        flash("Family suspended. Existing data has been preserved for review.", "success")
    elif action == "restore":
        family.is_active = True
        record_admin_audit("family_restore", target_family=family, reason="Restored from admin Families page")
        flash("Family restored.", "success")
    else:
        flash("That Family action is unavailable.", "warning")
    db.session.commit()
    return redirect(url_for("moderation.admin_families"))


@mod_bp.route("/admin/families/<int:family_id>/badge", methods=["POST"])
@fresh_login_required
def set_family_badge(family_id):
    if not is_platform_owner(current_user):
        flash("Only the platform owner can verify a Family.", "danger")
        return redirect(url_for("main.home"))
    family = Family.query.get_or_404(family_id)
    badge_action = request.form.get("badge_action", "").strip()
    note = request.form.get("verification_note", "").strip()
    duration_days = request.form.get("verification_duration", "365").strip()
    if badge_action not in {"assign", "revoke"} or duration_days not in {"30", "180", "365"}:
        flash("Choose a valid badge action and verification period.", "warning")
        return redirect(url_for("moderation.admin_families"))
    if badge_action == "assign" and (len(note) < 10 or len(note) > 500):
        flash("Record the verification evidence in 10–500 characters.", "warning")
        return redirect(url_for("moderation.admin_families"))
    if badge_action == "revoke" and not note:
        note = "Revoked by the platform owner."
    assignment = RiseBadgeAssignment.query.filter_by(
        family_id=family.id, badge_type="trusted_family"
    ).with_for_update().first()
    if badge_action == "assign":
        if not assignment:
            assignment = RiseBadgeAssignment(family_id=family.id, badge_type="trusted_family", verification_note=note)
            db.session.add(assignment)
        assignment.status = "active"
        assignment.verification_note = note
        assignment.assigned_by_id = current_user.id
        assignment.assigned_at = datetime.utcnow()
        assignment.expires_at = datetime.utcnow() + timedelta(days=int(duration_days))
        assignment.revoked_at = None
    elif assignment:
        assignment.status = "revoked"
        assignment.revoked_at = datetime.utcnow()
        assignment.verification_note = note
    record_admin_audit("rise_badge_family_" + badge_action, target_family=family, reason=note, metadata_text="trusted_family")
    db.session.commit()
    flash("Trusted Family badge updated.", "success")
    return redirect(url_for("moderation.admin_families"))


@mod_bp.route("/admin/audit-log")
@login_required
def admin_audit_log():
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return render_template("admin_audit_log.html", logs=logs)


@mod_bp.route("/admin/point-transactions")
@login_required
def admin_point_transactions():
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    page = max(1, request.args.get("page", 1, type=int))
    transactions = PointTransaction.query.order_by(
        PointTransaction.created_at.desc(), PointTransaction.id.desc()
    ).paginate(page=page, per_page=50, error_out=False)
    security_events = PointSecurityEvent.query.filter_by(resolved=False).order_by(
        PointSecurityEvent.created_at.desc()
    ).limit(100).all()
    return render_template(
        "admin_point_transactions.html",
        transactions=transactions,
        security_events=security_events,
    )


@mod_bp.route("/admin/point-transactions/<int:transaction_id>/reverse", methods=["POST"])
@fresh_login_required
def reverse_point_transaction(transaction_id):
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    transaction = PointTransaction.query.with_for_update().get_or_404(transaction_id)
    if transaction.reversed:
        flash("That reward has already been reversed.", "info")
        return redirect(url_for("moderation.admin_point_transactions"))
    if transaction.transaction_kind != "award":
        flash("Upgrade spending cannot be reversed from the reward moderation screen.", "warning")
        return redirect(url_for("moderation.admin_point_transactions"))
    if transaction.user_id == current_user.id and website_role(current_user) != "super_admin":
        flash("Moderators cannot reverse their own points.", "warning")
        return redirect(url_for("moderation.admin_point_transactions"))
    reason = request.form.get("reason", "").strip()
    try:
        reversed_transactions = reverse_reward_group(
            transaction, reversed_by_id=current_user.id, reason=reason
        )
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("moderation.admin_point_transactions"))
    completion = None
    if transaction.source_type == "challenge_completion" and transaction.source_id:
        completion = ChallengeCompletion.query.get(transaction.source_id)
        if completion:
            completion.verification_status = "invalidated"
    record_admin_audit(
        "point_reward_reversal",
        target_user=transaction.user,
        target_family=transaction.family,
        target_content_id=transaction.source_id,
        reason=reason,
        metadata_text=f"transactions={','.join(str(item.id) for item in reversed_transactions)};source={transaction.source_type}",
    )
    if transaction.user_id:
        db.session.add(Notification(
            user_id=transaction.user_id,
            category="points_reversed",
            message=f"A point reward was reversed after moderator review: {reason}",
            action_url=url_for("main.point_history"),
        ))
    elif completion:
        db.session.add(Notification(
            user_id=completion.user_id,
            category="points_reversed",
            message=f"Points for {completion.challenge.title} were reversed after moderator review.",
            action_url=url_for("main.point_history"),
        ))
    db.session.commit()
    flash("The linked Personal and Family rewards were reversed and audited.", "success")
    return redirect(url_for("moderation.admin_point_transactions"))


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
    if not require_admin_role("admin"):
        return redirect(url_for("main.home"))
    requests = HelpRequest.query.order_by(HelpRequest.created_at.desc()).all()
    return render_template("admin_help.html", requests=requests, is_platform_owner_view=is_platform_owner(current_user))


@mod_bp.route("/admin/help/<int:request_id>/<action>", methods=["POST"])
@login_required
def manage_help_request(request_id, action):
    if not require_admin_role("admin"):
        return redirect(url_for("main.home"))
    help_request = HelpRequest.query.get_or_404(request_id)
    if action == "delete":
        if not is_platform_owner(current_user):
            flash("Only the platform owner can permanently delete a help request.", "danger")
            return redirect(url_for("moderation.admin_help_requests"))
        record_admin_audit(
            "help_request_deleted", target_user=help_request.user,
            target_content_id=help_request.id, reason=help_request.subject,
        )
        db.session.delete(help_request)
        db.session.commit()
        flash("Help request deleted.", "success")
        return redirect(url_for("moderation.admin_help_requests"))
    if action in {"open", "reviewed", "closed"}:
        help_request.status = action
        db.session.commit()
        flash("Help request updated.", "success")
    return redirect(url_for("moderation.admin_help_requests"))


@mod_bp.route("/admin/settings", methods=["GET", "POST"])
@fresh_login_required
def admin_settings():
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    from routes.auth import public_url_for

    keys = [
        "google_client_id",
        "google_client_secret",
        "smtp_host",
        "smtp_from",
        "smtp_port",
        "smtp_username",
        "smtp_password",
        "smtp_use_ssl",
        "family_level_2_xp",
        "family_level_3_xp",
        "family_level_4_xp",
        "family_level_5_xp",
        "family_level_6_xp",
        "family_level_7_xp",
        "family_level_rising_interval",
    ]
    if request.method == "POST":
        try:
            configured_thresholds = [0] + [
                int(request.form.get(f"family_level_{level}_xp", ""))
                for level in range(2, 8)
            ]
            rising_interval = int(request.form.get("family_level_rising_interval", ""))
        except (TypeError, ValueError):
            flash("Family level thresholds must be whole numbers.", "warning")
            return redirect(url_for("moderation.admin_settings"))
        if (
            any(configured_thresholds[index] <= configured_thresholds[index - 1] for index in range(1, 7))
            or configured_thresholds[-1] > 10_000_000
            or not 100 <= rising_interval <= 10_000_000
        ):
            flash("Family level thresholds must increase at every level, and the rising interval must be at least 100 XP.", "warning")
            return redirect(url_for("moderation.admin_settings"))
        for key in keys:
            if key == "smtp_use_ssl":
                value = "true" if request.form.get(key) == "1" else ""
            else:
                value = request.form.get(key, "").strip()
            if not value and (key.endswith("secret") or key.endswith("password")):
                continue
            setting = SiteSetting.query.get(key) or SiteSetting(key=key)
            setting.value = value
            db.session.merge(setting)
        record_admin_audit(
            "settings_change",
            reason="Updated platform and Family level settings",
            metadata_text="Sensitive values are not recorded. Family XP thresholds were validated as increasing.",
        )
        db.session.commit()
        flash("Admin settings saved.", "success")
        return redirect(url_for("moderation.admin_settings"))
    settings = {}
    for key in keys:
        setting = SiteSetting.query.get(key)
        settings[key] = setting.value if setting else ""
    for level in range(2, 8):
        settings[f"family_level_{level}_xp"] = settings[f"family_level_{level}_xp"] or str(DEFAULT_FAMILY_LEVELS[level][1])
    settings["family_level_rising_interval"] = settings["family_level_rising_interval"] or str(DEFAULT_RISING_INTERVAL)
    return render_template(
        "admin_settings.html",
        settings=settings,
        google_redirect_uri=public_url_for("auth.google_callback"),
    )


@mod_bp.route("/admin/feature-flags", methods=["GET", "POST"])
@fresh_login_required
def admin_feature_flags():
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    if request.method == "POST":
        for name in FEATURE_FLAG_DEFINITIONS:
            setting = SiteSetting.query.get(feature_flag_key(name)) or SiteSetting(
                key=feature_flag_key(name)
            )
            setting.value = "true" if request.form.get(name) == "1" else "false"
            db.session.add(setting)
        enabled_names = [
            name for name in FEATURE_FLAG_DEFINITIONS if request.form.get(name) == "1"
        ]
        record_admin_audit(
            "feature_flags_change",
            reason="Updated platform feature flags",
            metadata_text=f"Enabled: {', '.join(enabled_names) or 'none'}",
        )
        db.session.commit()
        flash("Feature flags updated safely.", "success")
        return redirect(url_for("moderation.admin_feature_flags"))
    return render_template(
        "admin_feature_flags.html",
        definitions=FEATURE_FLAG_DEFINITIONS,
        flags=get_feature_flags(),
    )


@mod_bp.route("/admin/users/<int:user_id>/toggle-admin", methods=["POST"])
@fresh_login_required
def toggle_admin(user_id):
    if not is_platform_owner(current_user):
        flash("Only the platform owner can change website roles.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if not can_act_on(user, "role"):
        return redirect(url_for("moderation.admin_users"))
    user.admin_role = "" if website_role(user) else "admin"
    sync_admin_flag(user)
    record_admin_audit(
        "admin_role_toggle",
        target_user=user,
        reason="Toggled website admin access",
        metadata_text=f"New role: {website_role(user) or 'member'}",
    )
    db.session.commit()
    flash("Admin privileges updated.", "success")
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@fresh_login_required
def set_website_role(user_id):
    if not is_platform_owner(current_user):
        flash("Only the platform owner can promote or demote website roles.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    new_role = request.form.get("admin_role", "").strip()
    if new_role not in {"", "moderator", "admin"}:
        flash("Choose a valid website role.", "warning")
        return redirect(url_for("moderation.admin_users"))
    old_role = website_role(user)
    if user.id == current_user.id and new_role != old_role:
        flash("You cannot change your own website role here.", "warning")
        return redirect(url_for("moderation.admin_users"))
    if not can_act_on(user, "role") and old_role:
        return redirect(url_for("moderation.admin_users"))
    user.admin_role = new_role
    sync_admin_flag(user)
    if new_role:
        user.is_banned = False
        user.ban_until = None
    moderator_badge = RiseBadgeAssignment.query.filter_by(
        user_id=user.id, badge_type="platform_moderator"
    ).with_for_update().first()
    if new_role in {"moderator", "admin"}:
        if not moderator_badge:
            moderator_badge = RiseBadgeAssignment(
                user_id=user.id, badge_type="platform_moderator",
                verification_note="Website moderation role assigned by the platform owner.",
            )
            db.session.add(moderator_badge)
        moderator_badge.status = "active"
        moderator_badge.assigned_by_id = current_user.id
        moderator_badge.assigned_at = datetime.utcnow()
        moderator_badge.revoked_at = None
    elif moderator_badge:
        moderator_badge.status = "revoked"
        moderator_badge.revoked_at = datetime.utcnow()
    record_admin_audit(
        "admin_role_change",
        target_user=user,
        reason="Changed website role",
        metadata_text=f"{old_role or 'member'} -> {new_role or 'member'}",
    )
    db.session.commit()
    flash("Website role updated.", "success")
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/badge", methods=["POST"])
@fresh_login_required
def set_user_badge(user_id):
    if not is_platform_owner(current_user):
        flash("Only the platform owner can update verification badges.", "danger")
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    badge_type = request.form.get("badge_type", "").strip()
    note = request.form.get("verification_note", "").strip()
    duration_days = request.form.get("verification_duration", "365").strip()
    if badge_type not in {"", "verified_person", "official_organization"}:
        flash("Choose a valid RiseTogether verification badge.", "warning")
        return redirect(url_for("moderation.admin_users"))
    if duration_days not in {"30", "180", "365"}:
        flash("Choose a verification period of 1, 6, or 12 months.", "warning")
        return redirect(url_for("moderation.admin_users"))
    if badge_type and (len(note) < 10 or len(note) > 500):
        flash("Record the verification evidence in 10–500 characters.", "warning")
        return redirect(url_for("moderation.admin_users"))
    if not badge_type and not note:
        note = "Verification removed by the platform owner."
    assignments = RiseBadgeAssignment.query.filter(
        RiseBadgeAssignment.user_id == user.id,
        RiseBadgeAssignment.badge_type.in_(["verified_person", "official_organization"]),
    ).with_for_update().all()
    by_type = {assignment.badge_type: assignment for assignment in assignments}
    for assignment in assignments:
        assignment.status = "revoked"
        assignment.revoked_at = datetime.utcnow()
    if badge_type:
        assignment = by_type.get(badge_type)
        if not assignment:
            assignment = RiseBadgeAssignment(user_id=user.id, badge_type=badge_type, verification_note=note)
            db.session.add(assignment)
        assignment.status = "active"
        assignment.verification_note = note
        assignment.assigned_by_id = current_user.id
        assignment.assigned_at = datetime.utcnow()
        assignment.expires_at = datetime.utcnow() + timedelta(days=int(duration_days))
        assignment.revoked_at = None
    user.is_verified = bool(badge_type)
    record_admin_audit("rise_badge_user_update", target_user=user, reason=note, metadata_text=badge_type or "revoked")
    db.session.commit()
    flash("RiseTogether verification badge updated.", "success")
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/ban", methods=["POST"])
@login_required
def toggle_ban_user(user_id):
    if not require_admin_role("admin"):
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if not can_act_on(user, "temp_ban"):
        return redirect(request.referrer or url_for("moderation.admin_users"))
    user.is_banned = not user.is_banned
    if not user.is_banned:
        user.ban_until = None
    record_admin_audit(
        "user_ban_toggle",
        target_user=user,
        reason="Toggled account ban status",
        metadata_text=f"is_banned={user.is_banned}",
    )
    db.session.commit()
    flash("Account status updated.", "success")
    return redirect(request.referrer or url_for("moderation.admin_users"))


@mod_bp.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@fresh_login_required
def admin_user_action(user_id, action):
    minimum_role = "moderator" if action == "warn" else "super_admin" if action == "reset_password" else "admin"
    if not require_admin_role(minimum_role):
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if not can_act_on(user, action):
        return redirect(url_for("moderation.admin_users"))
    if action == "warn":
        user.warning_count += 1
        record_admin_audit("warning", target_user=user, reason="User warning added")
        flash("Warning added.", "success")
    elif action == "temp_ban":
        user.is_banned = True
        user.ban_until = datetime.utcnow() + timedelta(days=30)
        record_admin_audit("suspension", target_user=user, reason="Temporary 30 day ban")
        flash("User banned for 1 month.", "success")
    elif action == "perm_ban":
        user.is_banned = True
        user.ban_until = None
        record_admin_audit("ban", target_user=user, reason="Permanent ban")
        flash("User permanently banned.", "success")
    elif action == "unban":
        user.is_banned = False
        user.ban_until = None
        record_admin_audit("unban", target_user=user, reason="Account unbanned")
        flash("User unbanned.", "success")
    elif action == "verify":
        flash("Use the audited RiseTogether badge form to verify an account.", "warning")
        return redirect(url_for("moderation.admin_users"))
    elif action == "hide_directory":
        user.is_hidden_from_directory = not user.is_hidden_from_directory
        record_admin_audit(
            "directory_visibility_toggle",
            target_user=user,
            metadata_text=f"is_hidden_from_directory={user.is_hidden_from_directory}",
        )
        flash("Directory visibility updated.", "success")
    elif action == "reset_password":
        temp_password = f"RT-{secrets.randbelow(900000) + 100000}"
        user.set_password(temp_password)
        record_admin_audit(
            "password_reset",
            target_user=user,
            reason="Generated temporary password",
            metadata_text="Temporary password value not recorded.",
        )
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
@fresh_login_required
def admin_delete_user(user_id):
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    user = User.query.get_or_404(user_id)
    if is_platform_owner(user):
        flash("The platform owner account cannot be deleted.", "warning")
        return redirect(request.referrer or url_for("moderation.admin_users"))
    if website_role(user) == "super_admin" and active_super_admin_count() <= 1:
        flash("You cannot delete the last active Super Admin.", "warning")
        return redirect(request.referrer or url_for("moderation.admin_users"))
    if not can_act_on(user, "delete"):
        return redirect(request.referrer or url_for("moderation.admin_users"))
    record_admin_audit("user_delete", target_user=user, reason="Deleted from admin Users page")
    reverse_completion_rewards_for_user(user.id, reversed_by_id=current_user.id)
    db.session.flush()
    db.session.delete(user)
    db.session.commit()
    flash("Account deleted.", "info")
    return redirect(url_for("moderation.admin_users"))


@mod_bp.route("/admin/reports/<int:report_id>/<action>", methods=["POST"])
@login_required
def manage_report(report_id, action):
    minimum_role = "admin" if action in {"ban_user", "delete_user", "delete_report"} else "moderator"
    if not require_admin_role(minimum_role):
        return redirect(url_for("main.home"))
    report = Report.query.get_or_404(report_id)
    if action == "delete_report":
        if not is_platform_owner(current_user):
            flash("Only the platform owner can permanently delete a report record.", "danger")
            return redirect(url_for("moderation.admin_reports"))
        record_admin_audit(
            "report_record_deleted", target_user=report.reported_user,
            target_content_id=report.post_id or report.id, reason=report.reason,
        )
        db.session.delete(report)
        db.session.commit()
        flash("Report record deleted. Its reported content was not changed.", "success")
        return redirect(url_for("moderation.admin_reports"))
    if action == "reviewed":
        report.status = "reviewed"
        record_admin_audit("report_reviewed", target_user=report.reported_user, target_content_id=report.post_id)
        flash("Report marked reviewed.", "success")
    elif action == "delete_post" and report.post:
        post_id = report.post.id
        reported_user = report.reported_user
        db.session.delete(report.post)
        report.status = "actioned"
        record_admin_audit(
            "content_deletion",
            target_user=reported_user,
            target_content_id=post_id,
            reason="Deleted reported post",
        )
        flash("Reported post deleted.", "info")
    elif action == "ban_user" and report.reported_user:
        if not can_act_on(report.reported_user, "perm_ban"):
            return redirect(url_for("moderation.admin_reports"))
        report.reported_user.is_banned = True
        report.status = "actioned"
        record_admin_audit("ban", target_user=report.reported_user, reason="Banned from report review")
        flash("Reported account banned.", "success")
    elif action == "delete_user" and report.reported_user:
        if not require_admin_role("super_admin"):
            return redirect(url_for("main.home"))
        if is_platform_owner(report.reported_user):
            flash("The platform owner account cannot be deleted.", "warning")
            return redirect(url_for("moderation.admin_reports"))
        if website_role(report.reported_user) == "super_admin" and active_super_admin_count() <= 1:
            flash("You cannot delete the last active Super Admin.", "warning")
            return redirect(url_for("moderation.admin_reports"))
        if not can_act_on(report.reported_user, "delete"):
            return redirect(url_for("moderation.admin_reports"))
        record_admin_audit("user_delete", target_user=report.reported_user, reason="Deleted from report review")
        reverse_completion_rewards_for_user(
            report.reported_user.id, reversed_by_id=current_user.id
        )
        db.session.flush()
        db.session.delete(report.reported_user)
        report.status = "actioned"
        flash("Reported account deleted.", "info")
    else:
        flash("That admin action is unavailable for this report.", "warning")
        return redirect(url_for("moderation.admin_reports"))
    db.session.commit()
    return redirect(url_for("moderation.admin_reports"))
