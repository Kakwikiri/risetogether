from datetime import datetime

from sqlalchemy import case, func

from extensions import db
from models import ChallengeCompletion, PointTransaction


MAX_CONTROLLED_REWARD = 10000
DAILY_REPEATABLE_PERSONAL_LIMIT = 100


class PointLimitExceeded(ValueError):
    pass


def award_points(*, amount, reason, source_type, source_id, unique_reward_key,
                 user_id=None, family_id=None, awarded_by_id=None,
                 repeatable=False, daily_limit=None):
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
    if repeatable and user_id is not None:
        limit = daily_limit or DAILY_REPEATABLE_PERSONAL_LIMIT
        start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        earned_today = db.session.query(
            func.coalesce(func.sum(PointTransaction.amount), 0)
        ).filter(
            PointTransaction.user_id == user_id,
            PointTransaction.reversed.is_(False),
            PointTransaction.created_at >= start_of_day,
        ).scalar()
        if earned_today + amount > limit:
            raise PointLimitExceeded(
                f"Daily repeatable reward limit of {limit} Personal Points reached."
            )
    transaction = PointTransaction(
        user_id=user_id,
        family_id=family_id,
        amount=amount,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        transaction_kind="award",
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
            repeatable=completion.challenge.completion_frequency != "one_time",
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


def reverse_reward_group(transaction, *, reversed_by_id, reason):
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 500:
        raise ValueError("A reversal reason between 10 and 500 characters is required.")
    transactions = PointTransaction.query.filter_by(
        source_type=transaction.source_type,
        source_id=transaction.source_id,
        reversed=False,
    ).with_for_update().all()
    reversed_at = datetime.utcnow()
    for item in transactions:
        item.reversed = True
        item.reversed_at = reversed_at
        item.reversed_by_id = reversed_by_id
        item.reversal_reason = reason
    return transactions


def reverse_completion_rewards_for_user(user_id, *, reversed_by_id=None):
    completions = ChallengeCompletion.query.filter_by(
        user_id=user_id, verification_status="completed"
    ).all()
    reversed_transactions = []
    for completion in completions:
        transaction = PointTransaction.query.filter_by(
            source_type="challenge_completion",
            source_id=completion.id,
            reversed=False,
        ).first()
        if transaction:
            reversed_transactions.extend(reverse_reward_group(
                transaction,
                reversed_by_id=reversed_by_id,
                reason="Automatic reversal because the related completion was deleted.",
            ))
        completion.verification_status = "invalidated"
    return reversed_transactions


def personal_point_balance(user_id):
    return db.session.query(func.coalesce(func.sum(PointTransaction.amount), 0)).filter(
        PointTransaction.user_id == user_id,
        PointTransaction.reversed.is_(False),
    ).scalar()


def family_point_balance(family_id):
    signed_amount = case(
        (PointTransaction.transaction_kind == "spend", -PointTransaction.amount),
        else_=PointTransaction.amount,
    )
    balance = db.session.query(func.coalesce(func.sum(signed_amount), 0)).filter(
        PointTransaction.family_id == family_id,
        PointTransaction.reversed.is_(False),
    ).scalar()
    return max(0, balance)


def family_lifetime_xp(family_id):
    return db.session.query(func.coalesce(func.sum(PointTransaction.amount), 0)).filter(
        PointTransaction.family_id == family_id,
        PointTransaction.transaction_kind == "award",
        PointTransaction.reversed.is_(False),
    ).scalar()
