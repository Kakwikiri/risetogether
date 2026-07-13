from datetime import datetime

from models import ChallengeCompletion, FamilyChallenge, Post, SiteSetting
from points import family_lifetime_xp, family_point_balance


DEFAULT_FAMILY_LEVELS = {
    1: ("Seed", 0),
    2: ("Sprout", 100),
    3: ("Growing", 300),
    4: ("Strong", 750),
    5: ("Flourishing", 1500),
    6: ("Inspiring", 3000),
    7: ("Rising Family", 5000),
}
DEFAULT_RISING_INTERVAL = 2500


def family_level_thresholds():
    thresholds = {level: xp for level, (_, xp) in DEFAULT_FAMILY_LEVELS.items()}
    keys = [f"family_level_{level}_xp" for level in range(2, 8)]
    keys.append("family_level_rising_interval")
    settings = {item.key: item.value for item in SiteSetting.query.filter(SiteSetting.key.in_(keys)).all()}
    configured = {1: 0}
    try:
        for level in range(2, 8):
            configured[level] = int(settings.get(
                f"family_level_{level}_xp", thresholds[level]
            ))
        interval = int(settings.get("family_level_rising_interval", DEFAULT_RISING_INTERVAL))
    except (TypeError, ValueError):
        return thresholds, DEFAULT_RISING_INTERVAL
    if (
        any(configured[level] <= configured[level - 1] for level in range(2, 8))
        or configured[7] > 10_000_000
        or interval < 100
        or interval > 10_000_000
    ):
        return thresholds, DEFAULT_RISING_INTERVAL
    return configured, interval


def family_level_for_xp(xp):
    xp = max(0, int(xp or 0))
    thresholds, rising_interval = family_level_thresholds()
    level = 1
    for candidate in range(2, 8):
        if xp >= thresholds[candidate]:
            level = candidate
    if level >= 7:
        level = 7 + ((xp - thresholds[7]) // rising_interval)
        current_threshold = thresholds[7] + ((level - 7) * rising_interval)
        next_threshold = current_threshold + rising_interval
        name = "Rising Family"
    else:
        current_threshold = thresholds[level]
        next_threshold = thresholds[level + 1]
        name = DEFAULT_FAMILY_LEVELS[level][0]
    progress = int(((xp - current_threshold) / (next_threshold - current_threshold)) * 100)
    return {
        "level": level,
        "name": name,
        "current_threshold": current_threshold,
        "next_threshold": next_threshold,
        "progress_percent": max(0, min(100, progress)),
        "xp_to_next": max(0, next_threshold - xp),
    }


def family_level_summary(family):
    lifetime_xp = family_lifetime_xp(family.id)
    summary = family_level_for_xp(lifetime_xp)
    summary.update({
        "lifetime_xp": lifetime_xp,
        "available_points": family_point_balance(family.id),
        "challenges_completed": ChallengeCompletion.query.join(FamilyChallenge).filter(
            FamilyChallenge.family_id == family.id,
            ChallengeCompletion.verification_status == "completed",
        ).count(),
        "goals_achieved": Post.query.filter_by(
            family_id=family.id, post_type="achievement", achievement_type="goal_achieved"
        ).count(),
        "encouragement_milestones": Post.query.filter_by(
            family_id=family.id, post_type="achievement", achievement_type="encouragement_milestone"
        ).count(),
        "age_days": max(0, (datetime.utcnow().date() - (family.created_at or datetime.utcnow()).date()).days),
    })
    return summary
