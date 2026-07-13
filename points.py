from sqlalchemy import func

from extensions import db
from models import PointTransaction


MAX_CONTROLLED_REWARD = 10000


def award_points(*, amount, reason, source_type, source_id, unique_reward_key,
                 user_id=None, family_id=None, awarded_by_id=None):
    """Queue one idempotent, server-controlled point award in the active transaction."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError("Point amount must be a whole number.")
    if amount <= 0 or amount > MAX_CONTROLLED_REWARD:
        raise ValueError("Point amount is outside the controlled reward range.")
    if (user_id is None) == (family_id is None):
        raise ValueError("A point award must have exactly one recipient.")
    reason = (reason or "").strip()
    source_type = (source_type or "").strip()
    unique_reward_key = (unique_reward_key or "").strip()
    if not reason or len(reason) > 240:
        raise ValueError("A concise point reason is required.")
    if not source_type or len(source_type) > 64:
        raise ValueError("A valid point source type is required.")
    if not unique_reward_key or len(unique_reward_key) > 180:
        raise ValueError("A valid unique reward key is required.")

    existing = PointTransaction.query.filter_by(
        unique_reward_key=unique_reward_key
    ).first()
    if existing:
        return existing, False
    transaction = PointTransaction(
        user_id=user_id,
        family_id=family_id,
        amount=amount,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        unique_reward_key=unique_reward_key,
        awarded_by_id=awarded_by_id,
    )
    db.session.add(transaction)
    return transaction, True


def award_challenge_completion_points(completion, awarded_by_id=None):
    if completion.verification_status != "completed" or completion.points_awarded <= 0:
        return []
    source_key = f"challenge_completion:{completion.id}"
    reason = f"Completed {completion.challenge.title}"
    return [
        award_points(
            amount=completion.points_awarded,
            reason=reason,
            source_type="challenge_completion",
            source_id=completion.id,
            unique_reward_key=f"{source_key}:personal",
            user_id=completion.user_id,
            awarded_by_id=awarded_by_id,
        ),
        award_points(
            amount=completion.points_awarded,
            reason=reason,
            source_type="challenge_completion",
            source_id=completion.id,
            unique_reward_key=f"{source_key}:family",
            family_id=completion.challenge.family_id,
            awarded_by_id=awarded_by_id,
        ),
    ]


def personal_point_balance(user_id):
    return db.session.query(func.coalesce(func.sum(PointTransaction.amount), 0)).filter(
        PointTransaction.user_id == user_id,
        PointTransaction.reversed.is_(False),
    ).scalar()


def family_point_balance(family_id):
    return db.session.query(func.coalesce(func.sum(PointTransaction.amount), 0)).filter(
        PointTransaction.family_id == family_id,
        PointTransaction.reversed.is_(False),
    ).scalar()
