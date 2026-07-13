from collections import Counter
from datetime import datetime, time, timedelta

from extensions import db
from family_upgrades import UPGRADE_CATALOG, campaign_contributed_points
from models import (
    ChallengeCompletion, ChallengeParticipant, CheckInResponse, DailyCheckIn, EncouragementRequest,
    EncouragementResponse, FamilyCampaignContribution, FamilyChallenge,
    FamilyContributionCampaign, FamilyMember, FamilyPoll, FamilyPollVote,
    FamilyWeeklyReport, Goal, GoalProgress, PointTransaction, Post, Quiz, QuizAttempt,
)
from points import family_point_balance


def completed_week_bounds(now=None):
    now = now or datetime.utcnow()
    this_monday = (now - timedelta(days=now.weekday())).date()
    week_start = this_monday - timedelta(days=7)
    week_end = this_monday - timedelta(days=1)
    return week_start, week_end, datetime.combine(week_start, time.min), datetime.combine(this_monday, time.min)


def _activity_counts(family_id, start, end):
    counts = Counter()
    participants = ChallengeParticipant.query.join(FamilyChallenge).filter(
        FamilyChallenge.family_id == family_id,
        ChallengeParticipant.joined_at >= start, ChallengeParticipant.joined_at < end,
    ).all()
    completions = ChallengeCompletion.query.join(FamilyChallenge).filter(
        FamilyChallenge.family_id == family_id,
        ChallengeCompletion.completed_at >= start, ChallengeCompletion.completed_at < end,
        ChallengeCompletion.verification_status == "completed",
    ).all()
    quiz_attempts = QuizAttempt.query.join(Quiz).filter(
        Quiz.family_id == family_id,
        QuizAttempt.submitted_at >= start, QuizAttempt.submitted_at < end,
    ).all()
    goal_progress = GoalProgress.query.join(Goal).filter(
        Goal.family_id == family_id,
        GoalProgress.created_at >= start, GoalProgress.created_at < end,
    ).all()
    encouragement = EncouragementResponse.query.join(EncouragementRequest).filter(
        EncouragementRequest.family_id == family_id,
        EncouragementResponse.created_at >= start, EncouragementResponse.created_at < end,
    ).all()
    for row in participants + completions + quiz_attempts + goal_progress + encouragement:
        if row.user_id:
            counts[row.user_id] += 1
    return counts, participants, completions, quiz_attempts, goal_progress, encouragement


def build_weekly_snapshot(family, week_start, week_end, start, end):
    members = family.members.all()
    member_ids = {member.user_id for member in members}
    current, participants, completions, attempts, goal_progress, encouragement = _activity_counts(family.id, start, end)
    previous, *_ = _activity_counts(family.id, start - timedelta(days=7), start)

    poll_votes = FamilyPollVote.query.join(FamilyPoll).filter(
        FamilyPoll.family_id == family.id,
        FamilyPollVote.created_at >= start, FamilyPollVote.created_at < end,
    ).all()
    checkin_support = CheckInResponse.query.join(DailyCheckIn).filter(
        CheckInResponse.created_at >= start, CheckInResponse.created_at < end,
        CheckInResponse.user_id.in_(member_ids), DailyCheckIn.family_id == family.id,
    ).all() if member_ids else []
    supportive = Counter(row.user_id for row in encouragement)
    supportive.update(row.user_id for row in checkin_support)
    for row in checkin_support:
        current[row.user_id] += 1

    active_ids = set(current) | {vote.user_id for vote in poll_votes}
    new_members = [member for member in members if member.joined_at and start <= member.joined_at < end]

    challenge_completion_ids = {row.id for row in completions}
    quiz_ids = {row.quiz_id for row in attempts}
    goal_ids = {row.goal_id for row in goal_progress}
    personal_points = 0
    transactions = PointTransaction.query.filter(
        PointTransaction.user_id.in_(member_ids), PointTransaction.created_at >= start,
        PointTransaction.created_at < end, PointTransaction.transaction_kind == "award",
        PointTransaction.reversed.is_(False),
    ).all() if member_ids else []
    for transaction in transactions:
        belongs = (
            (transaction.source_type == "challenge_completion" and transaction.source_id in challenge_completion_ids)
            or (transaction.source_type == "quiz" and transaction.source_id in quiz_ids)
            or (transaction.source_type == "goal" and transaction.source_id in goal_ids)
        )
        if belongs:
            personal_points += transaction.amount
    family_points = sum(
        row.amount for row in PointTransaction.query.filter(
            PointTransaction.family_id == family.id, PointTransaction.created_at >= start,
            PointTransaction.created_at < end, PointTransaction.transaction_kind == "award",
            PointTransaction.reversed.is_(False),
        ).all()
    )

    supporter_id = supportive.most_common(1)[0][0] if supportive else None
    improvement = {user_id: current[user_id] - previous[user_id] for user_id in member_ids}
    improved_id = max(improvement, key=improvement.get) if improvement and max(improvement.values()) > 0 else None
    learner = max(attempts, key=lambda row: (row.percentage or 0, row.score or 0), default=None)
    users = {member.user_id: member.user.username for member in members}
    recognitions = []
    if supporter_id:
        recognitions.append({"title": "Supporter of the Week", "username": users.get(supporter_id, "A caring member")})
    if improved_id and improved_id != supporter_id:
        recognitions.append({"title": "Most Improved", "username": users.get(improved_id, "A growing member")})
    if learner and learner.user_id not in {supporter_id, improved_id}:
        recognitions.append({"title": "Learning Champion", "username": users.get(learner.user_id, "A learning member")})

    campaign = FamilyContributionCampaign.query.filter(
        FamilyContributionCampaign.family_id == family.id,
        FamilyContributionCampaign.status.in_(["active", "reached"]),
    ).order_by(FamilyContributionCampaign.created_at.desc()).first()
    upgrade = None
    if campaign:
        available = family_point_balance(family.id)
        contributed = campaign_contributed_points(campaign)
        upgrade = {
            "name": UPGRADE_CATALOG.get(campaign.upgrade_key, {}).get("name", "Family upgrade"),
            "current": available + contributed,
            "required": campaign.points_required,
            "percentage": min(100, round((available + contributed) / campaign.points_required * 100)),
        }
    goal_amount = round(sum(row.amount for row in goal_progress), 2)
    milestone = (
        f"Together, the Family completed {len(completions)} challenges."
        if completions else
        (f"Together, the Family added {goal_amount:g} toward shared goals." if goal_amount else
         f"Together, {len(active_ids)} members showed up in meaningful ways.")
    )
    return {
        "week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
        "active_members": len(active_ids), "new_members": len(new_members),
        "challenges_joined": len(participants), "challenges_completed": len(completions),
        "goal_progress": goal_amount, "poll_participants": len({row.user_id for row in poll_votes}),
        "quiz_participants": len({row.user_id for row in attempts}),
        "encouragement_activity": len(encouragement) + len(checkin_support),
        "personal_points": personal_points, "family_points": family_points,
        "upgrade": upgrade, "recognitions": recognitions, "milestone": milestone,
    }


def get_or_create_weekly_report(family):
    week_start, week_end, start, end = completed_week_bounds()
    report = FamilyWeeklyReport.query.filter_by(
        family_id=family.id, week_start=week_start
    ).with_for_update().first()
    if report:
        return report, False
    report = FamilyWeeklyReport(
        family_id=family.id, week_start=week_start, week_end=week_end,
        snapshot=build_weekly_snapshot(family, week_start, week_end, start, end),
    )
    db.session.add(report)
    db.session.flush()
    return report, True


def report_post_content(family, report):
    data = report.snapshot
    lines = [
        f"{family.name} · Weekly Growth Report",
        f"{data['active_members']} active members · {data['new_members']} new members",
        f"{data['challenges_completed']} challenges completed · {data['quiz_participants']} quiz participants",
        f"{data['encouragement_activity']} moments of encouragement",
    ]
    lines.extend(f"{item['title']}: @{item['username']}" for item in data.get("recognitions", []))
    lines.append(data["milestone"])
    lines.append("Every contribution mattered. A new week is a fresh place to grow together.")
    return "\n\n".join(lines)
