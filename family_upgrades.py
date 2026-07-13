from models import FamilyUpgradePurchase


UPGRADE_CATALOG = {
    "custom_banner": {"name": "Custom family banner", "cost": 500, "description": "Add a welcoming banner across the Family page."},
    "pinned_announcements": {"name": "Additional pinned announcements", "cost": 300, "description": "Keep up to three Family chat announcements pinned."},
    "challenge_slots": {"name": "Additional active challenge slots", "cost": 400, "description": "Increase active challenge capacity from 3 to 8."},
    "family_gallery": {"name": "Family gallery", "cost": 600, "description": "Share a dedicated gallery of Family memories."},
    "quiz_slots": {"name": "Extra quiz slots", "cost": 350, "description": "Increase open quiz capacity from 2 to 5."},
    "extra_themes": {"name": "Extra family themes", "cost": 450, "description": "Unlock sunrise, ocean, and forest Family themes."},
    "advanced_statistics": {"name": "Advanced family statistics", "cost": 800, "description": "See deeper participation and completion insights."},
    "custom_badge_frame": {"name": "Custom family badge frame", "cost": 500, "description": "Give the Family avatar a distinctive growth frame."},
    "capacity_75": {"name": "Capacity: 75 members", "cost": 1000, "description": "Increase member capacity from 50 to 75.", "capacity": 75},
    "capacity_100": {"name": "Capacity: 100 members", "cost": 1800, "description": "Increase member capacity from 75 to 100.", "capacity": 100},
    "capacity_150": {"name": "Capacity: 150 members", "cost": 3000, "description": "Increase member capacity from 100 to 150.", "capacity": 150},
    "capacity_250": {"name": "Capacity: 250 members", "cost": 5000, "description": "Increase member capacity from 150 to 250.", "capacity": 250},
}


def purchased_upgrade_keys(family_id):
    return {
        row.upgrade_key for row in FamilyUpgradePurchase.query.filter_by(family_id=family_id).all()
    }


def family_has_upgrade(family_id, upgrade_key):
    return FamilyUpgradePurchase.query.filter_by(
        family_id=family_id, upgrade_key=upgrade_key
    ).first() is not None


def active_challenge_limit(family_id):
    return 8 if family_has_upgrade(family_id, "challenge_slots") else 3


def open_quiz_limit(family_id):
    return 5 if family_has_upgrade(family_id, "quiz_slots") else 2


def pinned_announcement_limit(family_id):
    return 3 if family_has_upgrade(family_id, "pinned_announcements") else 1


def next_capacity_target(current_capacity):
    return next((value for value in (75, 100, 150, 250) if value > current_capacity), None)


def upgrade_is_available(family, upgrade_key):
    definition = UPGRADE_CATALOG.get(upgrade_key)
    if not definition or family_has_upgrade(family.id, upgrade_key):
        return False
    capacity = definition.get("capacity")
    return capacity is None or capacity == next_capacity_target(family.member_limit or 50)


def campaign_contributed_points(campaign):
    return sum(
        contribution.amount
        for contribution in campaign.contributions.all()
        if not contribution.refunded
    )
