import os

from models import FamilyMember, RiseBadgeAssignment


BADGE_DEFINITIONS = {
    "verified_person": {"label": "Verified Person", "symbol": "✓", "tone": "verified", "explanation": "Verified by RiseTogether."},
    "official_organization": {"label": "Official Organization", "symbol": "◆", "tone": "family", "explanation": "This organization was verified by RiseTogether."},
    "trusted_family": {"label": "Trusted Family", "symbol": "♡", "tone": "family", "explanation": "This Family was reviewed and trusted by RiseTogether."},
    "family_admin": {"label": "Family Admin", "symbol": "A", "tone": "admin", "explanation": "An owner or admin in this Family."},
    "platform_admin": {"label": "Platform Admin", "symbol": "A", "tone": "platform", "explanation": "Authorized to help administer the RiseTogether platform."},
    "platform_moderator": {"label": "Platform Moderator", "symbol": "M", "tone": "moderator", "explanation": "Authorized to help moderate the RiseTogether platform."},
    "founder_owner": {"label": "Founder/Owner", "symbol": "✦", "tone": "founder", "explanation": "Founder and platform owner of RiseTogether."},
}


def _badge_payload(badge_type):
    return {"type": badge_type, **BADGE_DEFINITIONS[badge_type]}


def user_badges(user, family=None):
    if not user:
        return []
    result = []
    assignments = RiseBadgeAssignment.query.filter_by(user_id=user.id, status="active").all()
    assigned_types = {assignment.badge_type for assignment in assignments}
    for badge_type in ("verified_person", "official_organization"):
        if badge_type in assigned_types:
            result.append(_badge_payload(badge_type))
    owner_username = os.getenv("PLATFORM_SUPER_ADMIN_USERNAME", "Kakwikiri").strip().lower()
    if user.admin_role == "super_admin" and user.username.lower() == owner_username:
        result.append(_badge_payload("founder_owner"))
    elif user.admin_role == "admin" and "platform_moderator" in assigned_types:
        result.append(_badge_payload("platform_admin"))
    elif user.admin_role == "moderator" and "platform_moderator" in assigned_types:
        result.append(_badge_payload("platform_moderator"))
    if family is not None:
        membership = FamilyMember.query.filter_by(family_id=family.id, user_id=user.id).first()
        if membership and membership.role in {"owner", "admin"}:
            result.append(_badge_payload("family_admin"))
    return result


def family_badges(family):
    if not family:
        return []
    trusted = RiseBadgeAssignment.query.filter_by(
        family_id=family.id, badge_type="trusted_family", status="active"
    ).first()
    return [_badge_payload("trusted_family")] if trusted else []
