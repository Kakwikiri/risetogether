import secrets
from datetime import datetime

from extensions import db
from feature_flags import is_feature_enabled
from models import (
    FamilyMember, ReferralCode, ReferralConversion, User, UserActivityDay,
)
from notifications_service import smart_notify
from points import award_points
from premium import economy_setting_int


def get_or_create_referral_code(inviter_id, family_id=None):
    code = ReferralCode.query.filter_by(
        inviter_id=inviter_id, family_id=family_id, active=True
    ).first()
    if code:
        return code
    code = ReferralCode(
        inviter_id=inviter_id,
        family_id=family_id,
        token=secrets.token_urlsafe(24),
        active=True,
    )
    db.session.add(code)
    db.session.flush()
    return code


def register_referral_signup(user, token):
    if not is_feature_enabled("referral_rewards") or not token:
        return None
    code = ReferralCode.query.filter_by(token=token, active=True).first()
    if not code or code.inviter_id == user.id or ReferralConversion.query.filter_by(
        referred_user_id=user.id
    ).first():
        return None
    conversion = ReferralConversion(
        referral_code_id=code.id,
        referred_user_id=user.id,
        joined_at=datetime.utcnow(),
    )
    db.session.add(conversion)
    if code.family and code.family.is_active and not FamilyMember.query.filter_by(
        family_id=code.family_id, user_id=user.id
    ).first():
        active_members = FamilyMember.query.filter_by(family_id=code.family_id).count()
        if active_members < (code.family.member_limit or 50):
            db.session.add(FamilyMember(
                family_id=code.family_id, user_id=user.id, role="member"
            ))
    return conversion


def process_referral_qualification(user_id):
    if not is_feature_enabled("referral_rewards"):
        return False
    conversion = ReferralConversion.query.filter_by(
        referred_user_id=user_id, rewarded_at=None
    ).with_for_update().first()
    if not conversion:
        return False
    required_days = economy_setting_int(
        "referral_required_active_days", 3, minimum=1, maximum=30
    )
    active_days = UserActivityDay.query.filter_by(user_id=user_id).count()
    if active_days < required_days:
        return False
    now = datetime.utcnow()
    conversion.qualified_at = conversion.qualified_at or now
    reward = economy_setting_int("referral_reward_points", 20, minimum=1, maximum=100)
    code = conversion.referral_code
    personal_created = False
    family_created = False
    if is_feature_enabled("personal_points"):
        _transaction, personal_created = award_points(
            amount=reward,
            reason=f"Referral became active for {required_days} days",
            source_type="qualified_referral",
            source_id=conversion.id,
            unique_reward_key=f"referral:{conversion.id}:personal",
            user_id=code.inviter_id,
            repeatable=False,
        )
    joined_referred_family = bool(
        code.family_id and FamilyMember.query.filter_by(
            family_id=code.family_id, user_id=user_id
        ).first()
    )
    if joined_referred_family and is_feature_enabled("family_points"):
        _transaction, family_created = award_points(
            amount=reward,
            reason=f"A referred member stayed active for {required_days} days",
            source_type="qualified_family_referral",
            source_id=conversion.id,
            unique_reward_key=f"referral:{conversion.id}:family",
            family_id=code.family_id,
            repeatable=False,
        )
    if not is_feature_enabled("personal_points") and not (
        joined_referred_family and is_feature_enabled("family_points")
    ):
        return False
    conversion.rewarded_at = now
    if personal_created:
        smart_notify(
            user_id=code.inviter_id,
            category="point_gift",
            message=f"Your invitation helped someone join and stay active for {required_days} days. You earned {reward} Personal Points.",
            action_url="/invites",
            dedupe_key=f"referral-reward:{conversion.id}:inviter",
            push=False,
            important=False,
        )
    reviewer_ids = {
        row.id for row in User.query.filter(
            User.admin_role.in_(["super_admin", "admin", "moderator"])
        ).all()
    }
    if code.family_id:
        reviewer_ids.update(
            row.user_id for row in FamilyMember.query.filter(
                FamilyMember.family_id == code.family_id,
                FamilyMember.role.in_(["owner", "admin", "moderator"]),
            ).all()
        )
    reviewers = {
        reviewer.id: reviewer for reviewer in User.query.filter(
            User.id.in_(reviewer_ids - {code.inviter_id})
        ).all()
    }
    for reviewer_id, reviewer in reviewers.items():
        smart_notify(
            user_id=reviewer_id,
            category="admin_warning" if reviewer.admin_role else "families",
            message=(
                f"A referral from {code.inviter.username} qualified after {required_days} active days. "
                f"{reward} Personal Points{' and ' + str(reward) + ' Family Points' if family_created else ''} were recorded."
            ),
            action_url="/admin/referrals" if reviewer.admin_role else "/invites",
            dedupe_key=f"referral-qualified:{conversion.id}:{reviewer_id}",
            push=False,
            important=False,
        )
    return personal_created or family_created
