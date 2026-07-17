from models import FamilyUpgradePurchase, SiteSetting


UPGRADE_CATALOG = {
    "custom_banner": {"name": "Custom family banner", "cost": 180, "required_level": 2, "description": "Add a welcoming banner across the Family page."},
    "pinned_announcements": {"name": "Additional pinned announcements", "cost": 120, "required_level": 2, "description": "Keep up to three Family chat announcements pinned."},
    "challenge_slots": {"name": "Additional active challenge slots", "cost": 200, "required_level": 2, "description": "Increase active challenge capacity from 3 to 8."},
    "family_gallery": {"name": "Family gallery", "cost": 250, "required_level": 3, "description": "Share a dedicated gallery of Family memories."},
    "quiz_slots": {"name": "Extra quiz slots", "cost": 180, "required_level": 2, "description": "Increase open quiz capacity from 2 to 5."},
    "extra_themes": {"name": "Extra family themes", "cost": 220, "required_level": 3, "description": "Unlock sunrise, ocean, and forest Family themes."},
    "advanced_statistics": {"name": "Advanced family statistics", "cost": 400, "required_level": 4, "description": "See deeper participation and completion insights."},
    "custom_badge_frame": {"name": "Custom family badge frame", "cost": 300, "required_level": 3, "description": "Give the Family avatar a distinctive growth frame."},
    "celebration_certificates": {"name": "Growth certificate", "cost": 100, "required_level": 2, "description": "A calm teal-and-gold certificate for Family challenge achievements."},
    "certificate_sunrise": {"name": "Sunrise certificate", "cost": 160, "required_level": 2, "description": "A hopeful orange certificate celebrating a new beginning."},
    "certificate_unity": {"name": "Unity certificate", "cost": 240, "required_level": 3, "description": "A warm joined-hands certificate for teamwork and shared progress."},
    "certificate_excellence": {"name": "Excellence certificate", "cost": 350, "required_level": 4, "description": "A polished purple certificate for outstanding achievement."},
    "certificate_legacy": {"name": "Legacy certificate", "cost": 500, "required_level": 5, "description": "A distinguished dark-and-gold certificate for major milestones."},
    "extra_admins": {"name": "More Family admins", "cost": 350, "required_level": 4, "description": "Add two more Family admin places."},
    "extra_moderators": {"name": "More Family moderators", "cost": 300, "required_level": 3, "description": "Add five more Family moderator places."},
    "family_calendar": {"name": "Family calendar", "cost": 750, "description": "Shared planning calendar.", "implemented": False},
    "resource_library": {"name": "Resource library", "cost": 900, "description": "Space for pinned Family resources.", "implemented": False},
    "capacity_75": {"name": "Capacity: 75 members", "cost": 400, "required_level": 2, "description": "Increase member capacity from 50 to 75.", "capacity": 75},
    "capacity_100": {"name": "Capacity: 100 members", "cost": 700, "required_level": 3, "description": "Increase member capacity from 75 to 100.", "capacity": 100},
    "capacity_150": {"name": "Capacity: 150 members", "cost": 1100, "required_level": 4, "description": "Increase member capacity from 100 to 150.", "capacity": 150},
    "capacity_250": {"name": "Capacity: 250 members", "cost": 1700, "required_level": 5, "description": "Increase member capacity from 150 to 250.", "capacity": 250},
    "capacity_500": {"name": "Capacity: 500 members", "cost": 2500, "required_level": 6, "description": "Increase member capacity from 250 to 500.", "capacity": 500},
}

CERTIFICATE_STYLES = {
    "growth": ("celebration_certificates", "Growth · teal and gold"),
    "sunrise": ("certificate_sunrise", "Sunrise · hopeful orange"),
    "unity": ("certificate_unity", "Unity · warm community"),
    "excellence": ("certificate_excellence", "Excellence · polished purple"),
    "legacy": ("certificate_legacy", "Legacy · dark and gold"),
}

PREMIUM_FAMILY_UPGRADES = frozenset(UPGRADE_CATALOG)
PREMIUM_UPGRADE_FLAGS = {
    "extra_themes": "premium_themes",
    "advanced_statistics": "premium_analytics",
    "challenge_slots": "premium_challenges",
}


def configured_upgrade_catalog():
    keys = [f"economy.upgrade_cost.{key}" for key in UPGRADE_CATALOG] + [
        f"economy.upgrade_level.{key}" for key in UPGRADE_CATALOG
    ]
    settings = {row.key: row.value for row in SiteSetting.query.filter(SiteSetting.key.in_(keys)).all()}
    catalog = {}
    for key, definition in UPGRADE_CATALOG.items():
        item = dict(definition)
        try:
            configured_cost = int(settings.get(f"economy.upgrade_cost.{key}", item["cost"]))
        except (TypeError, ValueError):
            configured_cost = item["cost"]
        item["cost"] = max(1, min(10_000_000, configured_cost))
        try:
            configured_level = int(settings.get(
                f"economy.upgrade_level.{key}", item.get("required_level", 1)
            ))
        except (TypeError, ValueError):
            configured_level = item.get("required_level", 1)
        item["required_level"] = max(1, min(100, configured_level))
        catalog[key] = item
    return catalog


def upgrade_definition(upgrade_key):
    return configured_upgrade_catalog().get(upgrade_key)


def purchased_upgrade_keys(family_id):
    return {
        row.upgrade_key for row in FamilyUpgradePurchase.query.filter_by(family_id=family_id).all()
    }


def family_has_upgrade(family_id, upgrade_key):
    purchased = FamilyUpgradePurchase.query.filter_by(
        family_id=family_id, upgrade_key=upgrade_key
    ).first() is not None
    if purchased:
        return True
    if upgrade_key not in PREMIUM_FAMILY_UPGRADES:
        return False
    from feature_flags import is_feature_enabled
    required_flag = PREMIUM_UPGRADE_FLAGS.get(upgrade_key)
    if required_flag and not is_feature_enabled(required_flag):
        return False
    from models import Family
    from premium import family_has_premium

    return family_has_premium(Family.query.get(family_id))


def active_challenge_limit(family_id):
    return 8 if family_has_upgrade(family_id, "challenge_slots") else 3


def open_quiz_limit(family_id):
    return 5 if family_has_upgrade(family_id, "quiz_slots") else 2


def pinned_announcement_limit(family_id):
    return 3 if family_has_upgrade(family_id, "pinned_announcements") else 1


def next_capacity_target(current_capacity):
    return next((value for value in (75, 100, 150, 250, 500) if value > current_capacity), None)


def upgrade_is_available(family, upgrade_key):
    definition = upgrade_definition(upgrade_key)
    if not definition or not definition.get("implemented", True) or family_has_upgrade(family.id, upgrade_key):
        return False
    from family_levels import family_level_for_xp
    from points import family_lifetime_xp
    if family_level_for_xp(family_lifetime_xp(family.id))["level"] < definition.get("required_level", 1):
        return False
    capacity = definition.get("capacity")
    return capacity is None or capacity == next_capacity_target(family.member_limit or 50)


def campaign_contributed_points(campaign):
    return sum(
        contribution.amount
        for contribution in campaign.contributions.all()
        if not contribution.refunded
    )
