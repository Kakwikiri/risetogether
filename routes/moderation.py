import secrets
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, fresh_login_required, login_required

from extensions import db
from feature_flags import (
    FEATURE_FLAG_DEFINITIONS,
    FEATURE_FLAG_DESCRIPTIONS,
    FEATURE_FLAG_GROUPS,
    feature_rollout_families_key,
    feature_rollout_key,
    feature_rollout_users_key,
    get_feature_rollouts,
    feature_flag_key,
    get_feature_flags,
    is_feature_enabled,
    feature_target_type,
)
from family_levels import DEFAULT_FAMILY_LEVELS, DEFAULT_RISING_INTERVAL
from models import (
    AuditLog, Block, ChallengeCompletion, EncouragementRequestReport, Family, FamilyMember, HelpRequest, Notification,
    PointSecurityEvent, PointTransaction, Post, PremiumSubscription, ReferralConversion,
    Report, RiseBadgeAssignment, SiteSetting, User, UserActivityDay, VerificationApplication,
)
from notifications_service import smart_notify
from points import reverse_completion_rewards_for_user, reverse_reward_group
from premium import ECONOMY_DEFAULTS, economy_setting_int, subscription_is_active
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

ECONOMY_FEATURE_FLAGS = (
    "personal_points", "family_points", "family_xp", "family_levels", "family_upgrades",
    "point_transfers", "referral_rewards", "contribution_campaigns", "premium_membership", "premium_families",
    "premium_profiles", "premium_storage", "premium_upload_limits", "premium_themes",
    "premium_analytics", "premium_challenges", "premium_verification_applications",
    "premium_beta_testing",
    "weekly_reports", "daily_checkins", "achievement_posts",
)
PAYMENT_PROVIDER_OPTIONS = {
    "mobile_money": "Mobile Money",
    "card": "Debit or credit card",
    "paypal": "PayPal",
    "bank_transfer": "Bank transfer",
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


@mod_bp.route("/admin/referrals")
@fresh_login_required
def admin_referrals():
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    conversions = ReferralConversion.query.order_by(
        ReferralConversion.joined_at.desc()
    ).limit(200).all()
    user_ids = [conversion.referred_user_id for conversion in conversions]
    activity_counts = {}
    if user_ids:
        rows = db.session.query(
            UserActivityDay.user_id, db.func.count(UserActivityDay.id)
        ).filter(UserActivityDay.user_id.in_(user_ids)).group_by(
            UserActivityDay.user_id
        ).all()
        activity_counts = dict(rows)
    return render_template(
        "admin_referrals.html",
        conversions=conversions,
        activity_counts=activity_counts,
        qualified_count=sum(bool(row.qualified_at) for row in conversions),
        rewarded_count=sum(bool(row.rewarded_at) for row in conversions),
    )


@mod_bp.route("/admin/economy", methods=["GET", "POST"])
@fresh_login_required
def admin_economy():
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    if request.method == "POST":
        for name in ECONOMY_FEATURE_FLAGS:
            enabled = request.form.get(name) == "1"
            setting = SiteSetting.query.get(feature_flag_key(name)) or SiteSetting(
                key=feature_flag_key(name)
            )
            setting.value = "true" if enabled else "false"
            db.session.add(setting)
            rollout_setting = SiteSetting.query.get(feature_rollout_key(name)) or SiteSetting(
                key=feature_rollout_key(name)
            )
            rollout_setting.value = "everyone" if enabled else "off"
            db.session.add(rollout_setting)
        for key, default in ECONOMY_DEFAULTS.items():
            try:
                value = int(request.form.get(key, default))
            except (TypeError, ValueError):
                flash(f"{key.replace('_', ' ').title()} must be a whole number.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            if value < 0 or value > 10_000_000:
                flash("Economy settings must be between 0 and 10,000,000.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            setting = SiteSetting.query.get(f"economy.{key}") or SiteSetting(key=f"economy.{key}")
            setting.value = str(value)
            db.session.add(setting)
        for upgrade_key in request.form.getlist("upgrade_key"):
            try:
                cost = int(request.form.get(f"upgrade_cost_{upgrade_key}", ""))
            except (TypeError, ValueError):
                flash("Every upgrade cost must be a whole number.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            if cost < 1 or cost > 10_000_000:
                flash("Upgrade costs must be between 1 and 10,000,000 points.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            setting = SiteSetting.query.get(f"economy.upgrade_cost.{upgrade_key}") or SiteSetting(
                key=f"economy.upgrade_cost.{upgrade_key}"
            )
            setting.value = str(cost)
            db.session.add(setting)
            try:
                required_level = int(request.form.get(f"upgrade_level_{upgrade_key}", "1"))
            except (TypeError, ValueError):
                flash("Every upgrade level must be a whole number.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            if required_level < 1 or required_level > 100:
                flash("Upgrade levels must be between 1 and 100.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            level_setting = SiteSetting.query.get(
                f"economy.upgrade_level.{upgrade_key}"
            ) or SiteSetting(key=f"economy.upgrade_level.{upgrade_key}")
            level_setting.value = str(required_level)
            db.session.add(level_setting)
        for tier in ("small", "easy", "medium", "hard", "major"):
            try:
                reward = int(request.form.get(f"challenge_reward_{tier}", ""))
            except (TypeError, ValueError):
                flash("Challenge rewards must be whole numbers.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            if reward < 5 or reward > 10_000:
                flash("Challenge rewards must be between 5 and 10,000 points.", "warning")
                return redirect(url_for("moderation.admin_economy"))
            setting = SiteSetting.query.get(f"challenge_reward_{tier}") or SiteSetting(
                key=f"challenge_reward_{tier}"
            )
            setting.value = str(reward)
            db.session.add(setting)
        currency = request.form.get("payment_currency", "USD").strip().upper()
        if currency not in {"UGX", "USD", "KES", "EUR", "GBP"}:
            flash("Choose a supported display currency.", "warning")
            return redirect(url_for("moderation.admin_economy"))
        currency_setting = SiteSetting.query.get("economy.payment_currency") or SiteSetting(
            key="economy.payment_currency"
        )
        currency_setting.value = currency
        db.session.add(currency_setting)
        for provider_key in PAYMENT_PROVIDER_OPTIONS:
            provider_setting = SiteSetting.query.get(
                f"economy.payment_provider.{provider_key}"
            ) or SiteSetting(key=f"economy.payment_provider.{provider_key}")
            provider_setting.value = "true" if request.form.get(
                f"payment_provider_{provider_key}"
            ) == "1" else "false"
            db.session.add(provider_setting)
        record_admin_audit(
            "economy_settings_change",
            reason="Updated RiseTogether economy controls",
            metadata_text="Feature switches, limits, prices, and upgrade costs were updated.",
        )
        db.session.commit()
        flash("Economy controls updated.", "success")
        return redirect(url_for("moderation.admin_economy"))
    from family_upgrades import configured_upgrade_catalog
    from routes.family import challenge_reward_values

    flags = get_feature_flags()
    settings = {key: economy_setting_int(key) for key in ECONOMY_DEFAULTS}
    from premium import economy_setting_text
    payment_currency = economy_setting_text(
        "payment_currency", "USD", {"UGX", "USD", "KES", "EUR", "GBP"}
    )
    payment_providers = {
        key: economy_setting_text(f"payment_provider.{key}", "false") == "true"
        for key in PAYMENT_PROVIDER_OPTIONS
    }
    now = datetime.utcnow()
    archive_cutoff = now - timedelta(days=60)
    subscription_rows = PremiumSubscription.query.filter(
        PremiumSubscription.archived_at == None
    ).all()
    changed_history = False
    for row in subscription_rows:
        if row.status == "active" and row.expires_at and row.expires_at <= now:
            row.status = "expired"
            row.auto_renew = False
            changed_history = True
        inactive_since = row.expires_at or row.updated_at
        if row.status != "active" and inactive_since and inactive_since < archive_cutoff:
            row.archived_at = now
            changed_history = True
    if changed_history:
        db.session.commit()
    subscriptions = PremiumSubscription.query.filter(
        PremiumSubscription.archived_at == None
    ).order_by(
        PremiumSubscription.purchased_at.desc()
    ).limit(100).all()
    verification_applications = VerificationApplication.query.filter_by(status="pending").order_by(
        VerificationApplication.created_at.asc()
    ).all()
    return render_template(
        "admin_economy.html", flags=flags, economy_flags=ECONOMY_FEATURE_FLAGS,
        definitions=FEATURE_FLAG_DEFINITIONS, settings=settings,
        upgrades=configured_upgrade_catalog(), subscriptions=subscriptions,
        challenge_rewards=challenge_reward_values(),
        verification_applications=verification_applications,
        subscription_is_active=subscription_is_active,
        payment_currency=payment_currency,
        payment_provider_options=PAYMENT_PROVIDER_OPTIONS,
        payment_providers=payment_providers,
    )


@mod_bp.route("/admin/economy/subscriptions", methods=["POST"])
@fresh_login_required
def grant_premium_subscription():
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    subject_type = request.form.get("subject_type", "").strip()
    identifier = request.form.get("identifier", "").strip()
    duration_value = request.form.get("duration_days", "30").strip()
    allowed_durations = {"7", "30", "90", "180", "365", "lifetime"}
    if subject_type not in {"personal", "family"} or duration_value not in allowed_durations:
        flash("Choose a valid Premium subject and duration.", "warning")
        return redirect(url_for("moderation.admin_economy"))
    user = None
    family = None
    if subject_type == "personal":
        user = User.query.filter(db.func.lower(User.username) == identifier.lower()).first()
        if not user:
            flash("No user was found with that username.", "warning")
            return redirect(url_for("moderation.admin_economy"))
    else:
        family = Family.query.filter(db.func.lower(Family.name) == identifier.lower()).first()
        if not family:
            flash("No Family was found with that exact name.", "warning")
            return redirect(url_for("moderation.admin_economy"))
    now = datetime.utcnow()
    duration_days = None if duration_value == "lifetime" else int(duration_value)
    period = "lifetime" if duration_days is None else ("yearly" if duration_days == 365 else "monthly")
    expires_at = None if duration_days is None else now + timedelta(days=duration_days)
    query = PremiumSubscription.query.filter_by(plan=subject_type, status="active")
    query = query.filter_by(user_id=user.id) if user else query.filter_by(family_id=family.id)
    for existing in query.all():
        existing.status = "cancelled"
        existing.auto_renew = False
    subscription = PremiumSubscription(
        user_id=user.id if user else None, family_id=family.id if family else None,
        plan=subject_type, billing_period=period, purchased_at=now,
        expires_at=expires_at, status="active", auto_renew=False,
        granted_by_id=current_user.id,
    )
    db.session.add(subscription)
    if user:
        personal_premium_features = (
            "premium_membership", "premium_profiles", "premium_upload_limits",
            "premium_themes", "premium_analytics",
            "premium_verification_applications", "video_notes",
        )
        for feature_name in personal_premium_features:
            rollout_setting = SiteSetting.query.get(feature_rollout_key(feature_name)) or SiteSetting(
                key=feature_rollout_key(feature_name)
            )
            users_setting = SiteSetting.query.get(feature_rollout_users_key(feature_name)) or SiteSetting(
                key=feature_rollout_users_key(feature_name)
            )
            selected_ids = {
                int(value) for value in (users_setting.value or "").split(",")
                if value.strip().isdigit()
            }
            selected_ids.add(user.id)
            if rollout_setting.value != "everyone":
                rollout_setting.value = "selected"
            users_setting.value = ",".join(str(user_id) for user_id in sorted(selected_ids))
            db.session.add_all([rollout_setting, users_setting])
    if family:
        family_feature_names = (
            "premium_membership", "premium_families", "family_upgrades",
            "family_points", "family_xp", "family_levels", "family_leaderboards",
            "premium_challenges", "weekly_reports", "premium_themes",
            "premium_analytics",
        )
        for feature_name in family_feature_names:
            rollout_setting = SiteSetting.query.get(feature_rollout_key(feature_name)) or SiteSetting(
                key=feature_rollout_key(feature_name)
            )
            families_setting = SiteSetting.query.get(
                feature_rollout_families_key(feature_name)
            ) or SiteSetting(key=feature_rollout_families_key(feature_name))
            selected_family_ids = {
                int(value) for value in (families_setting.value or "").split(",")
                if value.strip().isdigit()
            }
            selected_family_ids.add(family.id)
            if rollout_setting.value != "everyone":
                rollout_setting.value = "selected"
            families_setting.value = ",".join(
                str(family_id) for family_id in sorted(selected_family_ids)
            )
            db.session.add_all([rollout_setting, families_setting])
        member_ids = {
            membership.user_id for membership in FamilyMember.query.filter_by(
                family_id=family.id
            ).all()
        }
        for feature_name in ("premium_themes", "premium_analytics", "premium_challenges"):
            rollout_setting = SiteSetting.query.get(feature_rollout_key(feature_name)) or SiteSetting(
                key=feature_rollout_key(feature_name)
            )
            users_setting = SiteSetting.query.get(feature_rollout_users_key(feature_name)) or SiteSetting(
                key=feature_rollout_users_key(feature_name)
            )
            selected_ids = {
                int(value) for value in (users_setting.value or "").split(",")
                if value.strip().isdigit()
            }
            selected_ids.update(member_ids)
            if rollout_setting.value != "everyone":
                rollout_setting.value = "selected"
            users_setting.value = ",".join(str(user_id) for user_id in sorted(selected_ids))
            db.session.add_all([rollout_setting, users_setting])
    record_admin_audit(
        "premium_beta_granted", target_user=user, target_family=family,
        reason=f"Granted {duration_value} day(s) {subject_type} Premium manually.",
    )
    db.session.commit()
    flash("Premium beta access granted. No payment was recorded.", "success")
    return redirect(url_for("moderation.admin_economy"))


@mod_bp.route("/admin/economy/subscriptions/<int:subscription_id>/cancel", methods=["POST"])
@fresh_login_required
def cancel_premium_subscription(subscription_id):
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    subscription = PremiumSubscription.query.get_or_404(subscription_id)
    subscription.status = "cancelled"
    subscription.auto_renew = False
    record_admin_audit(
        "premium_cancelled", target_user=subscription.user, target_family=subscription.family,
        reason="Premium beta access cancelled by the platform owner.",
    )
    db.session.commit()
    flash("Premium access cancelled.", "success")
    return redirect(url_for("moderation.admin_economy"))


@mod_bp.route("/admin/economy/subscriptions/<int:subscription_id>/archive", methods=["POST"])
@fresh_login_required
def archive_premium_subscription(subscription_id):
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    subscription = PremiumSubscription.query.get_or_404(subscription_id)
    if subscription_is_active(subscription):
        flash("Cancel active Premium before archiving it.", "warning")
        return redirect(url_for("moderation.admin_economy"))
    subscription.archived_at = datetime.utcnow()
    record_admin_audit(
        "premium_history_archived", target_user=subscription.user,
        target_family=subscription.family,
        reason="Hidden from the active subscription history; audit record retained.",
    )
    db.session.commit()
    flash("Subscription archived. Its audit record was safely retained.", "success")
    return redirect(url_for("moderation.admin_economy"))


@mod_bp.route("/admin/economy/verification/<int:application_id>/<action>", methods=["POST"])
@fresh_login_required
def review_verification_application(application_id, action):
    if not require_admin_role("super_admin"):
        return redirect(url_for("main.home"))
    application = VerificationApplication.query.filter_by(
        id=application_id, status="pending"
    ).first_or_404()
    if action not in {"approve", "reject"}:
        abort(404)
    review_note = request.form.get("review_note", "").strip()
    if len(review_note) < 10 or len(review_note) > 500:
        flash("Add a review note between 10 and 500 characters.", "warning")
        return redirect(url_for("moderation.admin_economy"))
    now = datetime.utcnow()
    if action == "approve":
        badge_type = {
            "verified_user": "verified_person",
            "official_organization": "official_organization",
            "trusted_family": "trusted_family",
        }[application.application_type]
        assignment = RiseBadgeAssignment.query.filter_by(
            badge_type=badge_type, user_id=application.user_id, family_id=application.family_id
        ).first()
        if not assignment:
            assignment = RiseBadgeAssignment(
                badge_type=badge_type, user_id=application.user_id,
                family_id=application.family_id, verification_note=review_note,
            )
            db.session.add(assignment)
        assignment.status = "active"
        assignment.verification_note = review_note
        assignment.assigned_by_id = current_user.id
        assignment.assigned_at = now
        assignment.expires_at = now + timedelta(days=365)
        assignment.revoked_at = None
        application.status = "approved"
    else:
        application.status = "rejected"
    application.review_note = review_note
    application.reviewed_at = now
    application.reviewed_by_id = current_user.id
    recipient_id = application.submitted_by_id
    record_admin_audit(
        "verification_application_reviewed", target_user=application.user,
        target_family=application.family, reason=review_note,
        metadata_text=f"Decision: {application.status}. Premium did not determine the outcome.",
    )
    if recipient_id:
        smart_notify(
            user_id=recipient_id, category="admin_warning",
            message=f"Your verification application was {application.status} after manual review.",
            action_url=url_for("main.verification_application"),
            dedupe_key=f"verification-application:{application.id}:{application.status}",
        )
    db.session.commit()
    flash(f"Verification application {application.status}.", "success")
    return redirect(url_for("moderation.admin_economy"))


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
    return render_template(
        "admin_reports.html", reports=reports,
        is_platform_owner_view=(
            is_platform_owner(current_user) or current_user.admin_role == "super_admin"
        ),
    )


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


@mod_bp.route("/feedback", methods=["GET", "POST"])
@login_required
def product_feedback():
    if request.method == "POST":
        feedback_type = request.form.get("feedback_type", "experience").strip()
        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()
        allowed_types = {
            "experience": "Experience",
            "feature": "Feature idea",
            "improvement": "Improvement",
            "problem": "Problem",
        }
        if feedback_type not in allowed_types or len(title) < 3 or len(title) > 120 or len(message) < 10 or len(message) > 3000:
            flash("Choose a feedback type and add a clear title and description.", "warning")
            return redirect(url_for("moderation.product_feedback"))
        db.session.add(HelpRequest(
            user_id=current_user.id,
            subject=f"[{allowed_types[feedback_type]}] {title}",
            message=message,
        ))
        db.session.commit()
        flash("Thank you. Your feedback has been sent to the RiseTogether team.", "success")
        return redirect(url_for("main.home"))
    return render_template("feedback.html")


@mod_bp.route("/admin/help")
@login_required
def admin_help_requests():
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    requests = HelpRequest.query.order_by(HelpRequest.created_at.desc()).all()
    return render_template(
        "admin_help.html", requests=requests,
        is_platform_owner_view=(
            is_platform_owner(current_user) or current_user.admin_role == "super_admin"
        ),
    )


@mod_bp.route("/admin/help/<int:request_id>/<action>", methods=["POST"])
@login_required
def manage_help_request(request_id, action):
    if not require_admin_role("moderator"):
        return redirect(url_for("main.home"))
    help_request = HelpRequest.query.get_or_404(request_id)
    if action == "delete":
        if current_user.admin_role != "super_admin" and not is_platform_owner(current_user):
            flash("Only a Super Admin can permanently delete a help request.", "danger")
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
        notify_reviewed = (
            action == "reviewed"
            and help_request.status != "reviewed"
            and help_request.user_id is not None
        )
        help_request.status = action
        if notify_reviewed:
            smart_notify(
                user_id=help_request.user_id,
                category="admin",
                message=f"Your request “{help_request.subject}” was reviewed.",
                action_url=url_for("moderation.help_request"),
                dedupe_key=f"help-reviewed:{help_request.id}",
            )
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
        "family_voice_free_devices",
        "family_voice_expanded_devices",
    ]
    if request.method == "POST":
        try:
            configured_thresholds = [0] + [
                int(request.form.get(f"family_level_{level}_xp", ""))
                for level in range(2, 8)
            ]
            rising_interval = int(request.form.get("family_level_rising_interval", ""))
            voice_free_devices = int(request.form.get("family_voice_free_devices", "3"))
            voice_expanded_devices = int(request.form.get("family_voice_expanded_devices", "8"))
        except (TypeError, ValueError):
            flash("Family level thresholds must be whole numbers.", "warning")
            return redirect(url_for("moderation.admin_settings"))
        if (
            any(configured_thresholds[index] <= configured_thresholds[index - 1] for index in range(1, 7))
            or configured_thresholds[-1] > 10_000_000
            or not 100 <= rising_interval <= 10_000_000
            or not 2 <= voice_free_devices <= 20
            or not max(3, voice_free_devices) <= voice_expanded_devices <= 30
        ):
            flash("Check the Family level and voice-room limits. Expanded voice capacity must be at least the free capacity.", "warning")
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
            mode = request.form.get(f"mode_{name}", "off")
            if mode not in {"off", "selected", "everyone"}:
                mode = "off"
            usernames = {
                value.strip().lstrip("@").lower()
                for value in request.form.get(f"users_{name}", "").split(",")
                if value.strip()
            }
            target_type = feature_target_type(name)
            selected_users = User.query.filter(db.func.lower(User.username).in_(usernames)).all() if usernames else []
            family_ids = {
                int(value) for value in request.form.get(f"families_{name}", "").split(",")
                if value.strip().isdigit()
            }
            selected_families = Family.query.filter(
                Family.id.in_(family_ids), Family.is_active == True
            ).all() if family_ids else []
            if target_type == "user":
                selected_families = []
            else:
                selected_users = []
                usernames = set()
            missing = usernames - {user.username.lower() for user in selected_users}
            if mode == "selected" and missing:
                flash(f"{FEATURE_FLAG_DEFINITIONS[name][0]}: users not found: {', '.join(sorted(missing))}.", "warning")
                return redirect(url_for("moderation.admin_feature_flags"))
            setting = SiteSetting.query.get(feature_flag_key(name)) or SiteSetting(
                key=feature_flag_key(name)
            )
            setting.value = "true" if mode == "everyone" else "false"
            db.session.add(setting)
            rollout_setting = SiteSetting.query.get(feature_rollout_key(name)) or SiteSetting(key=feature_rollout_key(name))
            rollout_setting.value = mode
            db.session.add(rollout_setting)
            users_setting = SiteSetting.query.get(feature_rollout_users_key(name)) or SiteSetting(key=feature_rollout_users_key(name))
            users_setting.value = ",".join(str(user.id) for user in selected_users)
            db.session.add(users_setting)
            families_setting = SiteSetting.query.get(
                feature_rollout_families_key(name)
            ) or SiteSetting(key=feature_rollout_families_key(name))
            families_setting.value = ",".join(str(family.id) for family in selected_families)
            db.session.add(families_setting)

            # A selected video-note tester must have the Premium prerequisites too.
            # This is test access only; it does not record a payment or auto-renew.
            if name == "video_notes" and mode == "selected":
                now = datetime.utcnow()
                family_member_ids = {
                    row.user_id for row in FamilyMember.query.filter(
                        FamilyMember.family_id.in_([family.id for family in selected_families])
                    ).all()
                } if selected_families else set()
                video_test_users = {
                    user.id: user for user in selected_users
                }
                if family_member_ids:
                    video_test_users.update({
                        user.id: user for user in User.query.filter(User.id.in_(family_member_ids)).all()
                    })
                for user in video_test_users.values():
                    if not PremiumSubscription.query.filter_by(
                        user_id=user.id, plan="personal", status="active"
                    ).first():
                        db.session.add(PremiumSubscription(
                            user_id=user.id, plan="personal", billing_period="lifetime",
                            purchased_at=now, status="active", auto_renew=False,
                            granted_by_id=current_user.id,
                        ))
                for dependency in ("premium_membership", "premium_profiles", "premium_upload_limits"):
                    dependency_mode = SiteSetting.query.get(feature_rollout_key(dependency)) or SiteSetting(
                        key=feature_rollout_key(dependency)
                    )
                    dependency_users = SiteSetting.query.get(feature_rollout_users_key(dependency)) or SiteSetting(
                        key=feature_rollout_users_key(dependency)
                    )
                    ids = {
                        int(value) for value in (dependency_users.value or "").split(",")
                        if value.strip().isdigit()
                    }
                    ids.update(video_test_users)
                    if dependency_mode.value != "everyone":
                        dependency_mode.value = "selected"
                    dependency_users.value = ",".join(str(user_id) for user_id in sorted(ids))
                    db.session.add_all([dependency_mode, dependency_users])
            if name == "premium_families" and mode == "selected":
                now = datetime.utcnow()
                for family in selected_families:
                    if not PremiumSubscription.query.filter_by(
                        family_id=family.id, plan="family", status="active"
                    ).first():
                        db.session.add(PremiumSubscription(
                            family_id=family.id, plan="family", billing_period="lifetime",
                            purchased_at=now, status="active", auto_renew=False,
                            granted_by_id=current_user.id,
                        ))
                membership_families = SiteSetting.query.get(
                    feature_rollout_families_key("premium_membership")
                ) or SiteSetting(key=feature_rollout_families_key("premium_membership"))
                membership_family_ids = {
                    int(value) for value in (membership_families.value or "").split(",")
                    if value.strip().isdigit()
                }
                membership_family_ids.update(family.id for family in selected_families)
                membership_families.value = ",".join(
                    str(family_id) for family_id in sorted(membership_family_ids)
                )
                membership_mode = SiteSetting.query.get(
                    feature_rollout_key("premium_membership")
                ) or SiteSetting(key=feature_rollout_key("premium_membership"))
                if membership_mode.value != "everyone":
                    membership_mode.value = "selected"
                db.session.add_all([membership_mode, membership_families])
        if request.form.get("mode_premium_families") == "selected":
            premium_family_setting = SiteSetting.query.get(
                feature_rollout_families_key("premium_families")
            )
            premium_family_ids = {
                int(value) for value in (premium_family_setting.value or "").split(",")
                if value.strip().isdigit()
            } if premium_family_setting else set()
            for dependency in (
                "premium_membership", "family_upgrades", "premium_challenges",
                "weekly_reports", "premium_themes", "premium_analytics",
            ):
                dependency_mode = SiteSetting.query.get(
                    feature_rollout_key(dependency)
                ) or SiteSetting(key=feature_rollout_key(dependency))
                dependency_families = SiteSetting.query.get(
                    feature_rollout_families_key(dependency)
                ) or SiteSetting(key=feature_rollout_families_key(dependency))
                ids = {
                    int(value) for value in (dependency_families.value or "").split(",")
                    if value.strip().isdigit()
                }
                ids.update(premium_family_ids)
                if dependency_mode.value != "everyone":
                    dependency_mode.value = "selected"
                dependency_families.value = ",".join(
                    str(family_id) for family_id in sorted(ids)
                )
                db.session.add_all([dependency_mode, dependency_families])
        enabled_names = [
            name for name in FEATURE_FLAG_DEFINITIONS
            if request.form.get(f"mode_{name}") != "off"
        ]
        record_admin_audit(
            "feature_flags_change",
            reason="Updated platform feature flags",
            metadata_text=f"Enabled: {', '.join(enabled_names) or 'none'}",
        )
        db.session.commit()
        flash("Feature flags updated safely.", "success")
        return redirect(url_for("moderation.admin_feature_flags"))
    rollouts = get_feature_rollouts()
    rollout_user_ids = {
        user_id for rollout in rollouts.values() for user_id in rollout["user_ids"]
    }
    rollout_users = {
        user.id: user.username
        for user in User.query.filter(User.id.in_(rollout_user_ids)).all()
    } if rollout_user_ids else {}
    rollout_family_ids = {
        family_id for rollout in rollouts.values() for family_id in rollout["family_ids"]
    }
    rollout_families = {
        family.id: family.name
        for family in Family.query.filter(Family.id.in_(rollout_family_ids)).all()
    } if rollout_family_ids else {}
    return render_template(
        "admin_feature_flags.html",
        definitions=FEATURE_FLAG_DEFINITIONS,
        descriptions=FEATURE_FLAG_DESCRIPTIONS,
        feature_groups=FEATURE_FLAG_GROUPS,
        flags=get_feature_flags(),
        rollouts=rollouts,
        rollout_usernames={
            name: ", ".join(
                rollout_users[user_id]
                for user_id in sorted(rollout["user_ids"])
                if user_id in rollout_users
            )
            for name, rollout in rollouts.items()
        },
        rollout_families=rollout_families,
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
        if current_user.admin_role != "super_admin" and not is_platform_owner(current_user):
            flash("Only a Super Admin can permanently delete a report record.", "danger")
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
        was_reviewed = report.status == "reviewed"
        report.status = "reviewed"
        record_admin_audit("report_reviewed", target_user=report.reported_user, target_content_id=report.post_id)
        if not was_reviewed:
            smart_notify(
                user_id=report.reporter_id,
                category="admin",
                message="Your report was reviewed by the RiseTogether safety team.",
                action_url=url_for("main.notifications"),
                dedupe_key=f"report-reviewed:{report.id}",
                important=False,
            )
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
