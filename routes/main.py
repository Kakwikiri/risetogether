import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, fresh_login_required, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from markupsafe import Markup, escape

from extensions import db, socketio
from helpers import (
    REACTION_LABELS,
    get_media_type,
    save_media,
    user_avatar_url,
    validate_upload,
)
from feature_flags import feature_required, is_feature_enabled
from models import (
    Appreciation,
    AuditLog,
    Block,
    Comment,
    CommentReaction,
    CheckInResponse,
    ChallengeCompletion,
    ChallengeParticipant,
    DailyCheckIn,
    EncouragementRequest,
    EncouragementResponse,
    Family,
    FamilyChallenge,
    FamilyMember,
    FamilyMemberRestriction,
    Follow,
    FriendRequest,
    Goal,
    Notification,
    Message,
    PointTransaction,
    Post,
    PostSupportResponse,
    PostShare,
    Profile,
    Reaction,
    ReturnCheckIn,
    ReturnSuggestionDismissal,
    StreakMilestone,
    StreakActivity,
    UserStreak,
    User,
)
from points import family_point_balance, personal_point_balance, reverse_completion_rewards_for_user
from notifications_service import (
    NOTIFICATION_CATEGORIES, notification_allowed, preference_map,
    save_notification_preferences, smart_notify,
)
from streaks import STREAK_DEFINITIONS, local_activity_date, queue_expiring_streak_warning, record_streak_activity

main_bp = Blueprint("main", __name__)
POST_AUDIENCES = {"public", "friends", "family", "private"}
POST_PURPOSES = {
    "normal": "Normal",
    "encouragement": "I need encouragement",
    "advice": "I need advice",
    "listen": "I need someone to listen",
    "practical_help": "I need practical help",
    "celebrating": "I'm celebrating",
}
RETURN_CHECKIN_MESSAGES = (
    "We miss you here.",
    "I hope you are doing okay.",
    "Just checking on you.",
    "Your RiseTogether family is thinking about you.",
    "No pressure to reply. I only wanted you to know that you matter.",
)
FEED_FILTERS = {"all", "videos", "families", "highlights", "kindness", "trending"}
SUPPORTIVE_PROMPTS = (
    "What’s on your heart today?",
    "Share a small win.",
    "Does anyone need encouragement?",
    "What did you learn today?",
    "Write something uplifting or honest.",
)
MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{2,80})")
CHECKIN_MOODS = {
    "happy": "Happy", "peaceful": "Peaceful", "motivated": "Motivated",
    "okay": "Okay", "tired": "Tired", "worried": "Worried",
    "struggling": "Struggling", "prefer_not_to_say": "Prefer not to say",
}
CHECKIN_PRIVACY = {"private", "family", "all_families", "public"}
CHECKIN_REACTIONS = {
    "support": "Support", "understand": "I Understand",
    "keep_going": "Keep Going", "inspire": "You Inspire Me",
}


def emit_notification(notification):
    socketio.emit(
        "notification_received",
        {
            "id": notification.id,
            "category": notification.category,
            "message": notification.message,
            "action_url": notification.action_url,
            "created_at": notification.created_at.strftime("%Y-%m-%d %H:%M"),
        },
        room=f"user-{notification.user_id}",
    )


def has_active_family_restriction(family_id, user_id, *restriction_types):
    query = FamilyMemberRestriction.query.filter_by(
        family_id=family_id,
        user_id=user_id,
        active=True,
    ).filter(
        (FamilyMemberRestriction.ends_at == None)
        | (FamilyMemberRestriction.ends_at > datetime.utcnow())
    )
    if restriction_types:
        query = query.filter(FamilyMemberRestriction.restriction_type.in_(restriction_types))
    return query.first() is not None


def validate_profile_avatar_upload(file):
    is_valid, message = validate_upload(file)
    if not is_valid:
        return False, message
    if get_media_type(file.filename) != "image":
        return False, "Profile picture must be an image."
    try:
        from PIL import Image
    except ImportError:
        file.stream.seek(0)
        return True, ""
    try:
        file.stream.seek(0)
        with Image.open(file.stream) as image:
            image.verify()
    except OSError:
        file.stream.seek(0)
        return False, "Profile picture file is not a valid image."
    file.stream.seek(0)
    return True, ""


def add_notification(user_id, category, message, action_url="", important=None):
    notification, _ = smart_notify(
        user_id=user_id, category=category, message=message, action_url=action_url,
        important=important,
    )
    return notification


def mentioned_usernames(content):
    return {match.group(1).lower() for match in MENTION_RE.finditer(content or "")}


def link_mentions(content):
    text = content or ""
    pieces = []
    last = 0
    found_names = mentioned_usernames(text)
    users = {}
    if found_names:
        users = {
            user.username.lower(): user
            for user in User.query.filter(
                db.func.lower(User.username).in_(found_names),
                User.is_hidden_from_directory == False,
            ).all()
        }
    for match in MENTION_RE.finditer(text):
        pieces.append(escape(text[last : match.start()]))
        username = match.group(1)
        user = users.get(username.lower())
        if user:
            pieces.append(
                Markup(
                    '<a class="mention-link" href="{}">@{}</a>'
                ).format(url_for("main.profile", username=user.username), user.username)
            )
        else:
            pieces.append(escape(match.group(0)))
        last = match.end()
    pieces.append(escape(text[last:]))
    return Markup("").join(pieces)


@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        if queue_expiring_streak_warning(current_user):
            db.session.commit()
        requested_filter = request.args.get("filter", "").strip().lower()
        if request.args.get("type") == "videos":
            requested_filter = "videos"
        feed_filter = requested_filter if requested_filter in FEED_FILTERS else "all"
        video_only = feed_filter == "videos"
        query = request.args.get("q", "").strip()
        blocked_by = [
            block.blocker_id
            for block in Block.query.filter_by(blocked_id=current_user.id).all()
        ]
        blocked = [
            block.blocked_id
            for block in Block.query.filter_by(blocker_id=current_user.id).all()
        ]
        hidden_ids = set(blocked + blocked_by)
        return_suggestions = return_suggestions_for(current_user, hidden_ids)
        return_summary = welcome_back_summary(current_user)
        memberships = user_family_ids(current_user)
        shared_post_ids = [
            share.post_id
            for share in PostShare.query.filter_by(recipient_id=current_user.id).all()
        ]
        visibility_filters = [
            Post.audience == "public",
            Post.user_id == current_user.id,
            (
                (Post.audience == "family")
                & (Post.family_id != None)
                & (Post.family_id.in_(memberships))
            ),
            Post.audience == "friends",
        ]
        if website_moderator_role(current_user) == "super_admin":
            # Super admins moderate website content across Family boundaries. This
            # deliberately does not affect any chat query or chat authorization.
            visibility_filters.append(Post.id != None)
        if shared_post_ids:
            visibility_filters.append(Post.id.in_(shared_post_ids))
        posts = (
            Post.query.filter(
                Post.user_id.notin_(hidden_ids),
                or_(*visibility_filters),
            )
            .order_by(Post.created_at.desc())
            .all()
        )
        posts = [post for post in posts if can_view_post(post)]
        families = [membership.family for membership in current_user.family_memberships]
        encouragement_suggestions = []
        if memberships and is_feature_enabled("anonymous_support_posts"):
            suggestion_pool = (
                EncouragementRequest.query.filter(
                    EncouragementRequest.family_id.in_(memberships),
                    EncouragementRequest.user_id != current_user.id,
                    EncouragementRequest.status == "active",
                    EncouragementRequest.visibility == "identity",
                    ~EncouragementRequest.responses.any(),
                )
                .order_by(EncouragementRequest.created_at.desc())
                .limit(12)
                .all()
            )
            if suggestion_pool:
                start = datetime.utcnow().date().toordinal() % len(suggestion_pool)
                encouragement_suggestions = (suggestion_pool[start:] + suggestion_pool[:start])[:3]
        daily_reasons = [
            {"text": item.message, "url": item.action_url or url_for("main.notifications")}
            for item in Notification.query.filter_by(user_id=current_user.id, seen=False)
            .order_by(Notification.updated_at.desc()).limit(3).all()
        ]
        if not daily_reasons and encouragement_suggestions:
            daily_reasons.append({
                "text": "Someone in your Family asked for encouragement.",
                "url": url_for("family.family_encouragement", family_id=encouragement_suggestions[0].family_id),
            })
        available_families = Family.query.order_by(Family.created_at.desc()).limit(8).all()
        all_visible_posts = list(posts)
        trending_posts = [
            post for post in sorted(all_visible_posts, key=trend_score, reverse=True)
            if trend_score(post) > 0
        ][:5]
        if query:
            posts = [
                post
                for post in posts
                if query.lower() in (post.content or "").lower()
                or query.lower() in post.author.username.lower()
                or query.lower() in post.author.profile.display_name.lower()
            ]
        if feed_filter == "videos":
            posts = [post for post in posts if post.media_type == "video"]
        elif feed_filter == "families":
            posts = [post for post in posts if post.family_id is not None]
        elif feed_filter == "highlights":
            posts = [post for post in posts if trend_score(post) >= 2]
            posts.sort(key=trend_score, reverse=True)
        elif feed_filter == "kindness":
            posts = [
                post for post in posts
                if any(
                    post.reactions.filter_by(type=reaction_type).count()
                    for reaction_type in ("support", "understand", "keep-going", "inspire")
                )
            ]
            posts.sort(key=trend_score, reverse=True)
        elif feed_filter == "trending":
            posts = [post for post in posts if trend_score(post) > 0]
            posts.sort(key=trend_score, reverse=True)
        return render_template(
            "feed.html",
            posts=posts,
            reactions=REACTION_LABELS,
            families=families,
            available_families=available_families,
            trending_posts=trending_posts,
            video_only=video_only,
            feed_filter=feed_filter,
            query=query,
            supportive_prompts=SUPPORTIVE_PROMPTS,
            encouragement_suggestions=encouragement_suggestions,
            daily_reasons=daily_reasons,
            return_suggestions=return_suggestions,
            return_summary=return_summary,
        )
    return render_template("landing.html")


@main_bp.route("/offline")
def offline():
    return render_template("offline.html")


def get_reaction_counts(post):
    return {
        key: Reaction.query.filter_by(post_id=post.id, type=key).count()
        for key in REACTION_LABELS
    }


def grouped_reaction_message(post):
    reactor_count = Reaction.query.filter(
        Reaction.post_id == post.id,
        Reaction.user_id != post.user_id,
    ).count()
    if reactor_count == 1:
        return "Someone supported your post."
    return f"{reactor_count} people supported your post."


def notify_post_author_about_reactions(post, allow_create=True):
    if post.user_id == current_user.id:
        return
    if not notification_allowed(post.user_id, "reaction"):
        return
    action_url = url_for("main.post_detail", post_id=post.id)
    message = grouped_reaction_message(post)
    existing_notifications = Notification.query.filter_by(
        user_id=post.user_id,
        category="reaction",
        action_url=action_url,
        seen=False,
    ).order_by(Notification.created_at.desc()).all()
    existing = existing_notifications[0] if existing_notifications else None
    for duplicate in existing_notifications[1:]:
        db.session.delete(duplicate)
    reactor_count = Reaction.query.filter(
        Reaction.post_id == post.id,
        Reaction.user_id != post.user_id,
    ).count()
    if existing and reactor_count == 0:
        db.session.delete(existing)
        return
    if existing:
        existing.message = message
        existing.updated_at = datetime.utcnow()
        existing.event_count = max(1, reactor_count)
        db.session.flush()
        emit_notification(existing)
        return
    if allow_create and reactor_count:
        add_notification(post.user_id, "reaction", message, action_url)


def wants_json_response():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )


def trend_score(post):
    return (post.reactions.count() * 2) + post.comments.count() + (post.shares.count() * 3)


def user_family_ids(user):
    if not user.is_authenticated:
        return []
    return [membership.family_id for membership in user.family_memberships]


def website_moderator_role(user):
    """Resolve current and legacy website roles without granting chat access."""
    role = (getattr(user, "admin_role", "") or "").strip()
    if role in {"super_admin", "admin", "moderator"}:
        return role
    return "admin" if getattr(user, "is_admin", False) else ""


def users_are_friends(user_id, other_id):
    return (
        FriendRequest.query.filter(
            FriendRequest.status == "accepted",
            or_(
                (FriendRequest.sender_id == user_id)
                & (FriendRequest.receiver_id == other_id),
                (FriendRequest.sender_id == other_id)
                & (FriendRequest.receiver_id == user_id),
            ),
        ).first()
        is not None
    )


def trusted_connection_ids(user_id):
    ids = set()
    for friendship in FriendRequest.query.filter(
        FriendRequest.status == "accepted",
        or_(FriendRequest.sender_id == user_id, FriendRequest.receiver_id == user_id),
    ).all():
        ids.add(friendship.receiver_id if friendship.sender_id == user_id else friendship.sender_id)
    family_ids = [row.family_id for row in FamilyMember.query.filter_by(user_id=user_id).all()]
    if family_ids:
        ids.update(
            row.user_id for row in FamilyMember.query.filter(
                FamilyMember.family_id.in_(family_ids), FamilyMember.user_id != user_id,
            ).all()
        )
    recent_cutoff = datetime.utcnow() - timedelta(days=90)
    for message in Message.query.filter(
        Message.family_id == None,
        Message.created_at >= recent_cutoff,
        or_(Message.sender_id == user_id, Message.recipient_id == user_id),
    ).all():
        ids.add(message.recipient_id if message.sender_id == user_id else message.sender_id)
    return {candidate_id for candidate_id in ids if candidate_id}


def can_send_return_checkin(sender_id, recipient_id):
    if sender_id == recipient_id or recipient_id not in trusted_connection_ids(sender_id):
        return False
    return not Block.query.filter(or_(
        (Block.blocker_id == sender_id) & (Block.blocked_id == recipient_id),
        (Block.blocker_id == recipient_id) & (Block.blocked_id == sender_id),
    )).first()


def return_suggestions_for(user, hidden_ids):
    if not user.profile.checkin_suggestions_enabled:
        return []
    now = datetime.utcnow()
    candidate_ids = trusted_connection_ids(user.id) - set(hidden_ids) - {user.id}
    if not candidate_ids:
        return []
    dismissed_ids = {
        row.inactive_user_id for row in ReturnSuggestionDismissal.query.filter(
            ReturnSuggestionDismissal.viewer_id == user.id,
            ReturnSuggestionDismissal.dismissed_until > now,
        ).all()
    }
    recent_sent_ids = {
        row.recipient_id for row in ReturnCheckIn.query.filter(
            ReturnCheckIn.sender_id == user.id,
            ReturnCheckIn.created_at >= now - timedelta(days=7),
        ).all()
    }
    candidates = User.query.join(Profile).filter(
        User.id.in_(candidate_ids - dismissed_ids - recent_sent_ids),
        User.last_active_at != None,
        User.last_active_at <= now - timedelta(days=3),
        Profile.checkin_suggestions_enabled == True,
        Profile.miss_you_notifications_enabled == True,
    ).order_by(User.last_active_at.asc()).limit(12).all()
    if not candidates:
        return []
    start = now.date().toordinal() % len(candidates)
    return (candidates[start:] + candidates[:start])[:3]


def welcome_back_summary(user):
    since = user.return_summary_since
    if not since or not user.profile.return_summaries_enabled:
        return None
    checkins = ReturnCheckIn.query.filter(
        ReturnCheckIn.recipient_id == user.id,
        ReturnCheckIn.created_at >= since,
    ).order_by(ReturnCheckIn.created_at.desc()).all()
    family_ids = [row.family_id for row in user.family_memberships]
    family_activity = []
    if family_ids:
        completions = ChallengeCompletion.query.join(FamilyChallenge).filter(
            FamilyChallenge.family_id.in_(family_ids),
            ChallengeCompletion.completed_at >= since,
            ChallengeCompletion.verification_status == "completed",
        ).all()
        counts = {}
        for completion in completions:
            counts[completion.challenge.family_id] = counts.get(completion.challenge.family_id, 0) + 1
        family_activity = [
            {"family": Family.query.get(family_id), "count": count}
            for family_id, count in counts.items()
        ]
    encouragement_count = EncouragementResponse.query.join(EncouragementRequest).filter(
        EncouragementRequest.user_id == user.id,
        EncouragementResponse.created_at >= since,
    ).count()
    return {
        "since": since,
        "checkins": checkins,
        "family_activity": [row for row in family_activity if row["family"]],
        "encouragement_count": encouragement_count,
    }


def can_view_post(post):
    if post.original_post is not None and not can_view_post(post.original_post):
        return False
    if current_user.is_authenticated and website_moderator_role(current_user) == "super_admin":
        return True
    if post.is_hidden and (
        not current_user.is_authenticated or post.user_id != current_user.id
    ):
        return False
    if post.audience == "public":
        return True
    if not current_user.is_authenticated:
        return False
    if post.user_id == current_user.id:
        return True
    if PostShare.query.filter_by(post_id=post.id, recipient_id=current_user.id).first():
        return True
    if post.audience == "private":
        return False
    if post.audience == "friends":
        return users_are_friends(current_user.id, post.user_id)
    if post.audience == "family":
        return bool(
            post.family_id and post.family_id in set(user_family_ids(current_user))
        )
    return False


def checkin_today():
    return (datetime.utcnow() + timedelta(hours=3)).date()


def can_view_checkin(checkin, viewer, family_id=None):
    if not viewer.is_authenticated:
        return checkin.privacy == "public"
    if checkin.user_id == viewer.id or checkin.privacy == "public":
        return True
    viewer_families = set(user_family_ids(viewer))
    if family_id and family_id not in viewer_families:
        return False
    if checkin.privacy == "family":
        return bool(checkin.family_id and checkin.family_id in viewer_families)
    if checkin.privacy == "all_families":
        owner_families = {membership.family_id for membership in checkin.user.family_memberships}
        return bool(viewer_families & owner_families)
    return False


@main_bp.route("/check-ins", methods=["GET", "POST"])
@login_required
@feature_required("daily_checkins")
def daily_checkins():
    memberships = current_user.family_memberships.all()
    family_ids = {membership.family_id for membership in memberships}
    today = checkin_today()
    existing = DailyCheckIn.query.filter_by(user_id=current_user.id, checkin_date=today).first()
    if request.method == "POST":
        mood = request.form.get("mood", "").strip()
        note = request.form.get("note", "").strip()
        privacy = request.form.get("privacy", "private").strip()
        family_id = request.form.get("family_id", type=int)
        if mood not in CHECKIN_MOODS:
            flash("Choose how you are feeling today.", "warning")
            return redirect(url_for("main.daily_checkins"))
        if len(note) > 500:
            flash("Your optional note must be 500 characters or fewer.", "warning")
            return redirect(url_for("main.daily_checkins"))
        if privacy not in CHECKIN_PRIVACY:
            privacy = "private"
        if privacy == "family" and family_id not in family_ids:
            flash("Choose a Family you currently belong to.", "warning")
            return redirect(url_for("main.daily_checkins"))
        if privacy != "family":
            family_id = None
        if privacy == "public" and request.form.get("public_consent") != "yes":
            flash("Please confirm before sharing emotional information publicly.", "warning")
            return redirect(url_for("main.daily_checkins"))
        checkin = existing or DailyCheckIn(user_id=current_user.id, checkin_date=today)
        checkin.mood = mood
        checkin.note = note
        checkin.privacy = privacy
        checkin.family_id = family_id
        db.session.add(checkin)
        if len(note) >= 10:
            db.session.flush()
            record_streak_activity(
                current_user, "reflection", source_type="daily_checkin",
                source_id=checkin.id, unique_key=f"reflection-checkin:{checkin.id}",
                occurred_at=checkin.created_at,
            )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Today’s check-in was already saved. Refresh to update it.", "info")
            return redirect(url_for("main.daily_checkins"))
        flash("Your check-in is saved with the privacy you chose.", "success")
        return redirect(url_for("main.daily_checkins"))

    selected_family_id = request.args.get("family_id", type=int)
    if selected_family_id not in family_ids:
        selected_family_id = None
    candidates = DailyCheckIn.query.filter(
        DailyCheckIn.checkin_date >= today - timedelta(days=7)
    ).order_by(DailyCheckIn.created_at.desc()).limit(150).all()
    visible_checkins = [
        checkin for checkin in candidates
        if can_view_checkin(checkin, current_user, selected_family_id)
        and (
            not selected_family_id
            or checkin.family_id == selected_family_id
            or (
                checkin.privacy == "all_families"
                and selected_family_id in {m.family_id for m in checkin.user.family_memberships}
            )
        )
    ]
    return render_template(
        "daily_checkins.html", moods=CHECKIN_MOODS, privacy_options=CHECKIN_PRIVACY,
        reactions=CHECKIN_REACTIONS, memberships=memberships, existing=existing,
        visible_checkins=visible_checkins, selected_family_id=selected_family_id,
    )


@main_bp.route("/check-ins/<int:checkin_id>/respond", methods=["POST"])
@login_required
@feature_required("daily_checkins")
def respond_to_checkin(checkin_id):
    checkin = DailyCheckIn.query.get_or_404(checkin_id)
    if not can_view_checkin(checkin, current_user) or checkin.user_id == current_user.id:
        abort(403)
    reaction = request.form.get("reaction", "").strip()
    message = request.form.get("message", "").strip()
    if reaction not in CHECKIN_REACTIONS or len(message) > 500:
        flash("Choose a supportive response and keep the message under 500 characters.", "warning")
        return redirect(url_for("main.daily_checkins"))
    response = CheckInResponse.query.filter_by(checkin_id=checkin.id, user_id=current_user.id).first()
    is_new_response = response is None
    if response:
        response.reaction = reaction
        response.message = message
    else:
        response = CheckInResponse(
            checkin_id=checkin.id, user_id=current_user.id,
            reaction=reaction, message=message,
        )
        db.session.add(response)
    if is_new_response:
        add_notification(
            checkin.user_id, "checkin_support",
            f"{current_user.username} sent support for your check-in.",
            url_for("main.daily_checkins"),
        )
    db.session.commit()
    flash("Your support was shared gently.", "success")
    return redirect(request.referrer or url_for("main.daily_checkins"))


@main_bp.route("/profile/<username>")
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    if (
        user.is_hidden_from_directory
        and (not current_user.is_authenticated or current_user.id != user.id)
        and not (current_user.is_authenticated and current_user.is_admin)
    ):
        abort(404)
    profile = user.profile
    is_owner_view = current_user.is_authenticated and current_user.id == user.id
    viewer_family_ids = set(user_family_ids(current_user)) if current_user.is_authenticated else set()
    all_memberships = FamilyMember.query.filter_by(user_id=user.id).all()
    family_memberships = []
    if is_owner_view or profile.show_family_memberships:
        family_memberships = [
            membership for membership in all_memberships
            if is_owner_view or membership.family.privacy == "public"
            or membership.family_id in viewer_family_ids
        ]
    friend_status = None
    friend_request = None
    is_following = False
    if current_user.is_authenticated and current_user.id != user.id:
        friend_request = FriendRequest.query.filter(
            or_(
                (FriendRequest.sender_id == current_user.id)
                & (FriendRequest.receiver_id == user.id),
                (FriendRequest.sender_id == user.id)
                & (FriendRequest.receiver_id == current_user.id),
            )
        ).first()
        friend_status = friend_request.status if friend_request else None
        is_following = (
            Follow.query.filter_by(
                follower_id=current_user.id, followed_id=user.id
            ).first()
            is not None
        )
    posts_query = Post.query.filter_by(user_id=user.id)
    if not is_owner_view:
        posts_query = posts_query.filter_by(audience="public", is_hidden=False)
        if not profile.show_achievements:
            posts_query = posts_query.filter(Post.post_type != "achievement")
    posts = posts_query.order_by(Post.created_at.desc()).all()
    following = [
        follow.followed
        for follow in Follow.query.filter_by(follower_id=user.id)
        .order_by(Follow.created_at.desc())
        .limit(12)
        .all()
    ]
    can_message_user = False
    if current_user.is_authenticated and current_user.id != user.id:
        can_message_user = (
            not current_user.is_banned
            and not user.is_banned
            and Block.query.filter_by(blocker_id=current_user.id, blocked_id=user.id).first() is None
            and Block.query.filter_by(blocker_id=user.id, blocked_id=current_user.id).first() is None
        )
    show_achievements = is_owner_view or profile.show_achievements
    show_streaks = is_owner_view or profile.show_streaks
    show_points = is_owner_view or profile.show_point_balance
    show_goals = is_owner_view or profile.show_goal_progress
    show_checkins = is_owner_view or profile.show_checkins
    completed_challenges = ChallengeCompletion.query.filter_by(
        user_id=user.id, verification_status="completed"
    ).count() if show_achievements else None
    achievements_query = Post.query.filter_by(user_id=user.id, post_type="achievement", is_hidden=False)
    if not is_owner_view:
        achievements_query = achievements_query.filter_by(audience="public")
    achievement_posts = achievements_query.order_by(Post.created_at.desc()).limit(6).all() if show_achievements else []
    goals_query = Goal.query.filter_by(owner_user_id=user.id)
    if not is_owner_view:
        goals_query = goals_query.filter_by(visibility="public")
    goals_achieved = goals_query.filter_by(status="completed").count() if show_goals else None
    visible_goals = goals_query.order_by(Goal.created_at.desc()).limit(6).all() if show_goals else []
    streak_rows = []
    if show_streaks and is_feature_enabled("streaks"):
        today = local_activity_date(user)
        for streak in user.streaks.all():
            protected = streak.grace_days_available > 0 and streak.last_activity_date == today - timedelta(days=2)
            expired = streak.last_activity_date and streak.last_activity_date < today - timedelta(days=1) and not protected
            definition = STREAK_DEFINITIONS.get(streak.streak_type)
            streak_rows.append({"record": streak, "current": 0 if expired else streak.current_count,
                                "label": definition[0] if definition else streak.streak_type.replace("_", " ").title()})
    supported_ids = {
        row.request.user_id for row in EncouragementResponse.query.filter_by(user_id=user.id).all()
        if row.request and row.request.user_id != user.id
    }
    supported_ids.update(
        row.checkin.user_id for row in CheckInResponse.query.filter_by(user_id=user.id).all()
        if row.checkin and row.checkin.user_id != user.id
    )
    badges = []
    if show_achievements:
        badges.extend(sorted({post.achievement_type.replace("_", " ").title() for post in achievement_posts if post.achievement_type}))
        badges.extend(row.badge_name for row in StreakMilestone.query.join(UserStreak).filter(UserStreak.user_id == user.id).all())
        badges = list(dict.fromkeys(badges))[:8]
    recent_checkins = []
    if show_checkins and is_feature_enabled("daily_checkins"):
        checkin_query = DailyCheckIn.query.filter_by(user_id=user.id)
        if not is_owner_view:
            checkin_query = checkin_query.filter_by(privacy="public")
        recent_checkins = checkin_query.order_by(DailyCheckIn.checkin_date.desc()).limit(3).all()
    return render_template(
        "profile.html",
        user=user,
        profile=profile,
        memberships=family_memberships,
        show_families=is_owner_view or profile.show_family_memberships,
        friend_status=friend_status,
        friend_request=friend_request,
        is_following=is_following,
        posts=posts,
        follower_count=Follow.query.filter_by(followed_id=user.id).count(),
        following_count=Follow.query.filter_by(follower_id=user.id).count(),
        following=following,
        can_message_user=can_message_user,
        is_owner_view=is_owner_view,
        show_achievements=show_achievements,
        show_streaks=show_streaks,
        show_points=show_points and is_feature_enabled("personal_points"),
        show_goals=show_goals,
        show_checkins=show_checkins and is_feature_enabled("daily_checkins"),
        completed_challenges=completed_challenges,
        achievement_posts=achievement_posts,
        visible_goals=visible_goals,
        goals_achieved=goals_achieved,
        streak_rows=streak_rows,
        people_supported=len(supported_ids),
        profile_badges=badges,
        recent_checkins=recent_checkins,
        profile_appreciations=(user.appreciations_received.order_by(Appreciation.created_at.desc()).limit(10).all() if is_owner_view else []),
        personal_balance=personal_point_balance(user.id) if show_points and is_feature_enabled("personal_points") else None,
    )


@main_bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    profile = current_user.profile
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        bio = request.form.get("bio", "").strip()
        privacy = request.form.get("privacy_posts", "public")
        wants_password_change = request.form.get("change_password") == "1"
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        reset_phrase = request.form.get("reset_phrase", "").strip()
        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename:
            is_valid, upload_message = validate_profile_avatar_upload(avatar_file)
            if not is_valid:
                flash(upload_message, "warning")
                return redirect(url_for("main.edit_profile"))
            filename = save_media(avatar_file)
            if filename:
                profile.avatar = filename
        profile.display_name = display_name or profile.display_name
        profile.bio = bio
        profile.privacy_posts = privacy if privacy in POST_AUDIENCES else "public"
        profile.show_point_balance = request.form.get("show_point_balance") == "1"
        profile.show_streaks = request.form.get("show_streaks") == "1"
        profile.show_achievements = request.form.get("show_achievements") == "1"
        profile.show_family_memberships = request.form.get("show_family_memberships") == "1"
        profile.show_checkins = request.form.get("show_checkins") == "1"
        profile.show_goal_progress = request.form.get("show_goal_progress") == "1"
        if wants_password_change:
            if not current_user.check_password(current_password):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("main.edit_profile"))
            if len(new_password) < 8:
                flash("New password must be at least 8 characters.", "warning")
                return redirect(url_for("main.edit_profile"))
            if new_password != confirm_password:
                flash("New passwords do not match.", "warning")
                return redirect(url_for("main.edit_profile"))
            current_user.set_password(new_password)
        if reset_phrase:
            if len(reset_phrase) < 8:
                flash("Reset word must be at least 8 characters.", "warning")
                return redirect(url_for("main.edit_profile"))
            current_user.set_reset_phrase(reset_phrase)
        db.session.commit()
        flash("Your profile is updated.", "success")
        return redirect(url_for("main.profile", username=current_user.username))
    return render_template("edit_profile.html", profile=profile)


@main_bp.route("/post/create", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    family_id = request.form.get("family_id")
    audience = request.form.get("audience", current_user.profile.privacy_posts)
    purpose = request.form.get("purpose", "normal").strip()
    if purpose not in POST_PURPOSES:
        purpose = "normal"
    if audience not in POST_AUDIENCES:
        audience = "public"
    media_file = request.files.get("media")
    if not content and not media_file:
        flash("Please add text, image, or audio/video to your post.", "warning")
        return redirect(url_for("main.home"))
    media_url = ""
    media_type = "text"
    if media_file and media_file.filename:
        is_valid, upload_message = validate_upload(media_file)
        if not is_valid:
            flash(upload_message, "warning")
            return redirect(url_for("main.home"))
        filename = save_media(media_file)
        if filename:
            media_url = filename
            media_type = get_media_type(filename)
    post = Post(
        user_id=current_user.id,
        content=content,
        media_url=media_url,
        media_type=media_type,
        audience=audience,
        purpose=purpose,
    )
    if family_id:
        try:
            selected_family_id = int(family_id)
        except ValueError:
            selected_family_id = None
        if selected_family_id:
            membership = FamilyMember.query.filter_by(
                family_id=selected_family_id, user_id=current_user.id
            ).first()
            if not membership:
                flash("Join that family before posting there.", "warning")
                return redirect(url_for("main.home"))
            if has_active_family_restriction(selected_family_id, current_user.id, "suspend"):
                flash("You are temporarily suspended from posting in that Family.", "warning")
                return redirect(url_for("main.home"))
            post.family_id = selected_family_id
            if post.audience == "public":
                post.audience = "family"
    if post.audience == "family" and not post.family_id:
        flash("Choose a family before posting to family only.", "warning")
        return redirect(url_for("main.home"))
    db.session.add(post)
    db.session.commit()
    for follow in Follow.query.filter_by(followed_id=current_user.id).all():
        if follow.follower_id != current_user.id:
            add_notification(
                follow.follower_id,
                "followed_post",
                f"{current_user.username} shared a new post.",
                url_for("main.post_detail", post_id=post.id),
            )
    db.session.commit()
    flash("Post shared with RiseTogether.", "success")
    return redirect(url_for("main.home"))


@main_bp.route("/post/<int:post_id>/support", methods=["POST"])
@login_required
def support_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post) or post.user_id == current_user.id:
        abort(403)
    action = request.form.get("action", "").strip()
    explanation = request.form.get("explanation", "").strip()
    visibility = request.form.get("visibility", "private").strip()
    if action not in {"idea", "may_help", "listen"}:
        abort(400)
    required_purpose = {"idea": "advice", "may_help": "practical_help", "listen": "listen"}[action]
    if post.purpose != required_purpose:
        abort(400)
    if action in {"idea", "may_help"} and not explanation:
        flash("Please explain your idea or how you may help.", "warning")
        return redirect(request.referrer or url_for("main.post_detail", post_id=post.id))
    if len(explanation) > 1000 or visibility not in {"public", "private"}:
        flash("Keep your response under 1,000 characters and choose where to share it.", "warning")
        return redirect(request.referrer or url_for("main.post_detail", post_id=post.id))
    response = PostSupportResponse.query.filter_by(post_id=post.id, user_id=current_user.id, action=action).first()
    is_new = response is None
    if response and action == "listen" and response.status == "accepted":
        flash("This listening invitation has already been accepted.", "info")
        return redirect(request.referrer or url_for("main.post_detail", post_id=post.id))
    if not response:
        response = PostSupportResponse(post_id=post.id, user_id=current_user.id, action=action)
        db.session.add(response)
    response.explanation = explanation
    response.visibility = visibility
    response.status = "pending" if action == "listen" else "shared"
    notification_text = {
        "idea": f"{current_user.username} shared an idea that may help.",
        "may_help": f"{current_user.username} offered practical help.",
        "listen": f"{current_user.username} offered to listen.",
    }[action]
    if is_new:
        add_notification(post.user_id, "post_support", notification_text, url_for("main.post_detail", post_id=post.id))
    if is_new and action == "idea":
        add_notification(current_user.id, "post_support", "Your advice was shared.", url_for("main.impact"), important=False)
    db.session.commit()
    flash("Your response was shared." if action != "listen" else "Your invitation was sent. A chat will open only if it is accepted.", "success")
    return redirect(request.referrer or url_for("main.post_detail", post_id=post.id))


@main_bp.route("/post-support/<int:response_id>/<decision>", methods=["POST"])
@login_required
def decide_listen_invitation(response_id, decision):
    response = PostSupportResponse.query.get_or_404(response_id)
    if response.post.user_id != current_user.id or response.action != "listen" or response.status != "pending":
        abort(403)
    if decision not in {"accept", "decline"}:
        abort(400)
    response.status = "accepted" if decision == "accept" else "declined"
    if decision == "accept":
        add_notification(
            response.user_id, "listen_accepted",
            f"{current_user.username} accepted your offer to listen.",
            url_for("chat.direct_chat", user_id=current_user.id),
        )
    db.session.commit()
    flash("Invitation accepted." if decision == "accept" else "Invitation declined privately.", "success")
    return redirect(url_for("main.post_detail", post_id=response.post_id))


@main_bp.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def toggle_follow(user_id):
    target = User.query.get_or_404(user_id)
    if target.id == current_user.id:
        flash("You cannot follow yourself.", "warning")
        return redirect(url_for("main.profile", username=target.username))
    existing = Follow.query.filter_by(
        follower_id=current_user.id, followed_id=target.id
    ).first()
    if existing:
        db.session.delete(existing)
        flash("Unfollowed.", "info")
    else:
        db.session.add(Follow(follower_id=current_user.id, followed_id=target.id))
        add_notification(
            target.id,
            "follow",
            f"{current_user.username} followed you.",
            url_for("main.profile", username=current_user.username),
        )
        flash("Following.", "success")
    db.session.commit()
    return redirect(request.referrer or url_for("main.profile", username=target.username))


@main_bp.route("/post/<int:post_id>/react", methods=["POST"])
@login_required
def react_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post):
        abort(404)
    reaction_type = request.form.get("reaction_type")
    if reaction_type not in REACTION_LABELS:
        if wants_json_response():
            return jsonify({"ok": False, "error": "Invalid reaction."}), 400
        flash("Invalid reaction.", "warning")
        return redirect(url_for("main.home"))
    existing = Reaction.query.filter_by(
        post_id=post.id, user_id=current_user.id
    ).first()
    if existing and existing.type == reaction_type:
        db.session.delete(existing)
        status = "removed"
        selected_reaction = None
        message = "Reaction removed."
    elif existing:
        existing.type = reaction_type
        existing.created_at = datetime.utcnow()
        status = "changed"
        selected_reaction = reaction_type
        message = "Reaction changed."
    else:
        db.session.add(Reaction(
            post_id=post.id, user_id=current_user.id, type=reaction_type
        ))
        status = "added"
        selected_reaction = reaction_type
        message = "Your support reaction has been added."
    try:
        db.session.flush()
        notify_post_author_about_reactions(post, allow_create=bool(selected_reaction))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.warning(
            "Concurrent reaction update rejected for post=%s user=%s",
            post.id,
            current_user.id,
        )
        if wants_json_response():
            return jsonify({"ok": False, "error": "Your reaction changed elsewhere. Please try again."}), 409
        flash("Your reaction changed elsewhere. Please try again.", "warning")
        return redirect(request.referrer or url_for("main.home"))
    if wants_json_response():
        return jsonify(
            {
                "ok": True,
                "status": status,
                "selected_reaction": selected_reaction,
                "counts": get_reaction_counts(post),
                "message": message,
            }
        )
    flash(message, "info" if status == "removed" else "success")
    return redirect(request.referrer or url_for("main.home"))


@main_bp.route("/post/<int:post_id>/reactions")
@login_required
def post_reactors(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post):
        abort(404)
    reaction_type = request.args.get("type", "").strip()
    if reaction_type and reaction_type not in REACTION_LABELS:
        return jsonify({"ok": False, "error": "Invalid reaction."}), 400
    blocked_ids = {
        row.blocked_id for row in Block.query.filter_by(blocker_id=current_user.id)
    } | {
        row.blocker_id for row in Block.query.filter_by(blocked_id=current_user.id)
    }
    query = Reaction.query.join(User, Reaction.user_id == User.id).filter(
        Reaction.post_id == post.id,
        ~Reaction.user_id.in_(blocked_ids),
    )
    if reaction_type:
        query = query.filter(Reaction.type == reaction_type)
    if not current_user.is_admin:
        query = query.filter(or_(
            User.is_hidden_from_directory == False,
            User.id == current_user.id,
        ))
    people = []
    for reaction in query.order_by(Reaction.created_at.desc()).all():
        user = reaction.user
        people.append({
            "username": user.username,
            "display_name": user.profile.display_name if user.profile else user.username,
            "avatar_url": user_avatar_url(user),
            "profile_url": url_for("main.profile", username=user.username),
            "reaction_type": reaction.type,
            "reaction_label": REACTION_LABELS[reaction.type],
        })
    return jsonify({"ok": True, "people": people})


@main_bp.route("/post/<int:post_id>/share", methods=["GET", "POST"])
@login_required
def share_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post):
        abort(404)
    source_post = post.original_post or post
    if not can_view_post(source_post):
        abort(404)
    memberships = FamilyMember.query.filter_by(user_id=current_user.id).all()
    families = [membership.family for membership in memberships if membership.family.is_active]
    can_share_publicly = (
        source_post.audience == "public"
        and not source_post.is_hidden
        and (not source_post.family or source_post.family.privacy == "public")
    )
    allowed_family_ids = {
        family.id for family in families
        if source_post.audience == "public"
        or (source_post.audience == "family" and source_post.family_id == family.id)
    }

    blocked_by = [
        block.blocker_id for block in Block.query.filter_by(blocked_id=current_user.id)
    ]
    blocked = [
        block.blocked_id for block in Block.query.filter_by(blocker_id=current_user.id)
    ]
    excluded_ids = set(blocked + blocked_by + [current_user.id])
    recipients = (
        User.query.filter(
            User.id.notin_(excluded_ids),
            User.is_hidden_from_directory == False,
        )
        .order_by(User.username.asc())
        .all()
    )
    if source_post.audience == "friends":
        recipients = [user for user in recipients if users_are_friends(source_post.user_id, user.id)]
    elif source_post.audience == "family":
        family_user_ids = {
            member.user_id for member in FamilyMember.query.filter_by(family_id=source_post.family_id).all()
        }
        recipients = [user for user in recipients if user.id in family_user_ids]
    elif source_post.audience != "public":
        recipients = []

    if request.method == "POST":
        destination = request.form.get("destination", "people")
        if destination in {"public", "family"}:
            family_id = request.form.get("family_id", "")
            selected_family_id = int(family_id) if family_id.isdigit() else None
            if destination == "public" and not can_share_publicly:
                flash("This post’s privacy does not allow public sharing.", "warning")
                return redirect(url_for("main.share_post", post_id=post.id))
            if destination == "family" and selected_family_id not in allowed_family_ids:
                flash("You cannot share this post with that Family.", "warning")
                return redirect(url_for("main.share_post", post_id=post.id))
            if destination == "family" and has_active_family_restriction(selected_family_id, current_user.id, "suspend"):
                flash("You are temporarily suspended from posting in that Family.", "warning")
                return redirect(url_for("main.share_post", post_id=post.id))
            existing = Post.query.filter_by(
                original_post_id=source_post.id,
                user_id=current_user.id,
                audience=destination,
                family_id=selected_family_id if destination == "family" else None,
            ).first()
            if existing:
                flash("You already shared this post there.", "info")
                return redirect(url_for("main.post_detail", post_id=existing.id))
            reshare = Post(
                user_id=current_user.id,
                content="",
                audience=destination,
                family_id=selected_family_id if destination == "family" else None,
                original_post_id=source_post.id,
            )
            db.session.add(reshare)
            try:
                db.session.flush()
                if source_post.user_id != current_user.id:
                    add_notification(
                        source_post.user_id,
                        "share",
                        f"{current_user.username} shared your post.",
                        url_for("main.post_detail", post_id=reshare.id),
                    )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("You already shared this post there.", "info")
                return redirect(url_for("main.share_post", post_id=source_post.id))
            flash("Post shared with attribution intact.", "success")
            return redirect(url_for("main.post_detail", post_id=reshare.id))

        recipient_ids = {
            int(user_id)
            for user_id in request.form.getlist("recipient_ids")
            if user_id.isdigit()
        }
        valid_recipients = [user for user in recipients if user.id in recipient_ids]
        if not valid_recipients:
            flash("Choose at least one person to share with.", "warning")
            return redirect(url_for("main.share_post", post_id=post.id))

        shared_count = 0
        for recipient in valid_recipients:
            existing_share = PostShare.query.filter_by(
                post_id=source_post.id,
                user_id=current_user.id,
                recipient_id=recipient.id,
            ).first()
            if existing_share:
                continue
            db.session.add(
                PostShare(
                    post_id=source_post.id,
                    user_id=current_user.id,
                    recipient_id=recipient.id,
                )
            )
            add_notification(
                recipient.id,
                "share",
                f"{current_user.username} shared a post with you.",
                url_for("main.post_detail", post_id=source_post.id),
            )
            shared_count += 1

        if shared_count and source_post.user_id != current_user.id:
            add_notification(
                source_post.user_id,
                "share",
                f"{current_user.username} shared your post with {shared_count} people.",
                url_for("main.post_detail", post_id=source_post.id),
            )
        db.session.commit()
        if shared_count:
            flash(f"Post shared with {shared_count} people.", "success")
        else:
            flash("You already shared this post with everyone selected.", "info")
        return redirect(url_for("main.post_detail", post_id=source_post.id))

    return render_template(
        "share_post.html",
        post=source_post,
        recipients=recipients,
        families=families,
        allowed_family_ids=allowed_family_ids,
        can_share_publicly=can_share_publicly,
        share_url=url_for("main.post_detail", post_id=source_post.id, _external=True),
    )


@main_bp.route("/post/<int:post_id>", methods=["GET", "POST"])
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post):
        abort(404)
    if current_user.is_authenticated:
        Notification.query.filter(
            Notification.user_id == current_user.id,
            Notification.action_url.startswith(url_for("main.post_detail", post_id=post.id)),
            Notification.seen == False,
        ).update({"seen": True})
        db.session.commit()
    if request.method == "POST" and current_user.is_authenticated:
        content = request.form.get("comment", "").strip()
        parent_id = request.form.get("parent_id")
        parent = None
        if parent_id:
            parent = Comment.query.filter_by(id=parent_id, post_id=post.id).first()
            if parent and parent.parent_id is not None:
                parent = parent.parent
        if content and len(content) <= 3000:
            comment = Comment(
                post_id=post.id,
                user_id=current_user.id,
                content=content,
                parent_id=parent.id if parent else None,
            )
            db.session.add(comment)
            db.session.flush()
            notify_user_id = parent.user_id if parent else post.user_id
            if notify_user_id != current_user.id:
                add_notification(
                    notify_user_id,
                    "comment",
                    (
                        f"{current_user.username} replied: ‘{' '.join(content.split())[:90]}’"
                        if parent
                        else f"{current_user.username} commented: ‘{' '.join(content.split())[:90]}’"
                    ),
                    url_for("main.post_detail", post_id=post.id) + f"#comment-{comment.id}",
                )
            mentioned_users = []
            names = mentioned_usernames(content)
            if names:
                mentioned_users = User.query.filter(
                    db.func.lower(User.username).in_(names),
                    User.is_hidden_from_directory == False,
                ).all()
            already_notified = {current_user.id, notify_user_id}
            for mentioned in mentioned_users:
                if mentioned.id in already_notified:
                    continue
                add_notification(
                    mentioned.id,
                    "mention",
                    f"{current_user.username} mentioned you: ‘{' '.join(content.split())[:90]}’",
                    url_for("main.post_detail", post_id=post.id) + f"#comment-{comment.id}",
                )
                already_notified.add(mentioned.id)
            db.session.commit()
            flash("Your comment is added.", "success")
        elif content:
            flash("Comments cannot exceed 3,000 characters.", "warning")
        return redirect(url_for("main.post_detail", post_id=post.id))
    try:
        comment_page = max(1, int(request.args.get("comment_page", 1)))
    except ValueError:
        comment_page = 1
    comment_limit = min(comment_page * 10, 100)
    root_comments = post.comments.filter_by(parent_id=None).order_by(Comment.created_at.asc())
    total_root_comments = root_comments.count()
    comments = root_comments.limit(comment_limit).all()
    return render_template(
        "post_detail.html",
        post=post,
        comments=comments,
        reactions=REACTION_LABELS,
        reaction_counts=get_reaction_counts(post),
        link_mentions=link_mentions,
        has_more_comments=total_root_comments > len(comments),
        next_comment_page=comment_page + 1,
    )


@main_bp.route("/post/<int:post_id>/<action>", methods=["POST"])
@login_required
def manage_post(post_id, action):
    post = Post.query.get_or_404(post_id)
    moderator_role = website_moderator_role(current_user)
    owns_post = post.user_id == current_user.id
    if not owns_post and moderator_role not in {"super_admin", "admin", "moderator"}:
        abort(403)
    if action == "delete":
        if not owns_post:
            db.session.add(
                AuditLog(
                    actor_user_id=current_user.id,
                    actor_role=moderator_role,
                    action_type="post_delete",
                    target_user_id=post.user_id,
                    target_family_id=post.family_id,
                    target_content_id=post.id,
                    reason=(request.form.get("reason", "") or "Harmful content moderation")[:500],
                    metadata_text="Post removed directly by website moderation.",
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
                )
            )
        db.session.delete(post)
        db.session.commit()
        flash("Post removed and recorded in the audit log." if not owns_post else "Post deleted.", "info")
        return redirect(url_for("main.home"))
    if action == "hide":
        post.is_hidden = not post.is_hidden
        db.session.commit()
        flash("Post visibility updated.", "success")
        return redirect(request.referrer or url_for("main.home"))
    abort(404)


COMMENT_REACTION_LABELS = {
    "support": "❤️ Support",
    "understand": "🤝 I Understand",
    "keep-going": "🔥 Keep Going",
    "inspire": "💪 You Inspire Me",
}


@main_bp.route("/comment/<int:comment_id>/like", methods=["POST"])
@login_required
def like_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if not can_view_post(comment.post):
        abort(404)
    reaction_type = request.form.get("reaction_type", "support")
    if reaction_type not in COMMENT_REACTION_LABELS:
        if wants_json_response():
            return jsonify({"ok": False, "error": "Invalid comment reaction."}), 400
        abort(400)
    existing = CommentReaction.query.filter_by(
        comment_id=comment.id, user_id=current_user.id
    ).first()
    if existing and existing.type == reaction_type:
        db.session.delete(existing)
        selected = None
        message = "Comment reaction removed."
    elif existing:
        existing.type = reaction_type
        selected = reaction_type
        message = "Comment reaction changed."
    else:
        db.session.add(CommentReaction(comment_id=comment.id, user_id=current_user.id, type=reaction_type))
        selected = reaction_type
        message = "Encouragement added."
        if comment.user_id != current_user.id:
            add_notification(
                comment.user_id,
                "comment",
                f"{current_user.username} encouraged your comment.",
                url_for("main.post_detail", post_id=comment.post_id),
            )
    db.session.commit()
    if wants_json_response():
        counts = {key: CommentReaction.query.filter_by(comment_id=comment.id, type=key).count() for key in COMMENT_REACTION_LABELS}
        return jsonify({"ok": True, "selected_reaction": selected, "counts": counts, "message": message})
    flash(message, "info" if selected is None else "success")
    return redirect(url_for("main.post_detail", post_id=comment.post_id))


@main_bp.route("/comment/<int:comment_id>/<action>", methods=["POST"])
@login_required
def manage_comment(comment_id, action):
    comment = Comment.query.get_or_404(comment_id)
    if not can_view_post(comment.post):
        abort(404)
    family_moderator = False
    if comment.post.family_id:
        membership = FamilyMember.query.filter_by(
            family_id=comment.post.family_id, user_id=current_user.id
        ).first()
        family_moderator = bool(membership and membership.role in {"owner", "admin", "moderator"})
    can_moderate = comment.user_id == current_user.id or current_user.is_admin or family_moderator
    if not can_moderate:
        abort(403)
    if action == "delete":
        post_id = comment.post_id
        db.session.delete(comment)
        db.session.commit()
        flash("Comment deleted.", "info")
        return redirect(url_for("main.post_detail", post_id=post_id))
    if action == "edit":
        content = request.form.get("content", "").strip()
        if not content or len(content) > 3000:
            flash("Comments must be between 1 and 3,000 characters.", "warning")
        else:
            comment.content = content
            comment.edited_at = datetime.utcnow()
            db.session.commit()
            flash("Comment updated.", "success")
        return redirect(url_for("main.post_detail", post_id=comment.post_id))
    abort(404)


@main_bp.route("/people")
@login_required
def people():
    query = request.args.get("q", "").strip()
    users = []
    if query:
        search = f"%{query}%"
        users = (
            User.query.filter(
                User.id != current_user.id,
                User.is_hidden_from_directory == False,
                or_(User.username.ilike(search), User.email.ilike(search)),
            )
            .order_by(User.username.asc())
            .limit(30)
            .all()
        )
    else:
        users = (
            User.query.filter(
                User.id != current_user.id,
                User.is_hidden_from_directory == False,
            )
            .order_by(User.created_at.desc())
            .limit(20)
            .all()
        )
    incoming_requests = (
        FriendRequest.query.filter_by(receiver_id=current_user.id, status="pending")
        .order_by(FriendRequest.created_at.desc())
        .all()
    )
    accepted_requests = (
        FriendRequest.query.filter(
            FriendRequest.status == "accepted",
            or_(
                FriendRequest.sender_id == current_user.id,
                FriendRequest.receiver_id == current_user.id,
            ),
        )
        .order_by(FriendRequest.responded_at.desc().nullslast())
        .all()
    )
    return render_template(
        "people.html",
        users=users,
        query=query,
        incoming_requests=incoming_requests,
        accepted_requests=accepted_requests,
    )


@main_bp.route("/memories")
@login_required
def memories():
    today = datetime.utcnow().date()
    rows = []
    for response in EncouragementResponse.query.filter_by(user_id=current_user.id).all():
        happened = response.created_at.date()
        if happened.month == today.month and happened.day == today.day and happened.year < today.year:
            name = response.request.requester.username if response.request.visibility == "identity" else "someone"
            years = today.year - happened.year
            rows.append({"date": response.created_at, "text": f"{years} year{'s' if years != 1 else ''} ago today you encouraged {name}.", "url": url_for("family.family_encouragement", family_id=response.request.family_id)})
    first_completion = ChallengeCompletion.query.filter_by(user_id=current_user.id, verification_status="completed").order_by(ChallengeCompletion.completed_at.asc()).first()
    if first_completion and first_completion.completed_at:
        rows.append({"date": first_completion.completed_at, "text": "You completed your first challenge.", "url": url_for("family.family_detail", family_id=first_completion.challenge.family_id) + "#family-challenges"})
    for membership in current_user.family_memberships:
        if membership.joined_at:
            years = today.year - membership.joined_at.date().year
            if years > 0 and membership.joined_at.month == today.month and membership.joined_at.day == today.day:
                rows.append({"date": membership.joined_at, "text": f"You joined {membership.family.name} {years} year{'s' if years != 1 else ''} ago today.", "url": url_for("family.family_detail", family_id=membership.family_id)})
    rows.sort(key=lambda row: row["date"], reverse=True)
    return render_template("memories.html", memories=rows)


@main_bp.route("/impact")
@login_required
def impact():
    encouraged_user_ids = {
        response.request.user_id for response in current_user.encouragement_responses
        if response.request and response.request.user_id != current_user.id
    }
    family_ids_helped = {
        response.request.family_id for response in current_user.encouragement_responses
        if response.request
    }
    impact_rows = [
        ("People encouraged", len(encouraged_user_ids)),
        ("Advice shared", EncouragementResponse.query.join(EncouragementRequest).filter(EncouragementResponse.user_id == current_user.id, EncouragementResponse.comment != "", EncouragementRequest.category == "advice").count()),
        ("Families helped", len(family_ids_helped)),
        ("Challenges inspired", current_user.created_family_challenges.count()),
        ("Thank-you messages received", current_user.appreciations_received.count()),
        ("Days active", StreakActivity.query.filter_by(user_id=current_user.id).with_entities(db.func.count(db.func.distinct(StreakActivity.activity_date))).scalar() or 0),
        ("Goals completed", current_user.owned_goals.filter_by(status="completed").count()),
    ]
    return render_template("impact.html", impact_rows=impact_rows, appreciations=current_user.appreciations_received.order_by(Appreciation.created_at.desc()).limit(20).all())


@main_bp.route("/friendship/<int:user_id>")
@login_required
def friendship_journey(user_id):
    friend = User.query.get_or_404(user_id)
    friendship = FriendRequest.query.filter(
        FriendRequest.status == "accepted",
        or_(
            (FriendRequest.sender_id == current_user.id) & (FriendRequest.receiver_id == friend.id),
            (FriendRequest.sender_id == friend.id) & (FriendRequest.receiver_id == current_user.id),
        ),
    ).first()
    if not friendship:
        abort(403)
    events = [{"date": friendship.responded_at or friendship.created_at, "text": "You became friends."}]
    first_message = Message.query.filter(or_(
        (Message.sender_id == current_user.id) & (Message.recipient_id == friend.id),
        (Message.sender_id == friend.id) & (Message.recipient_id == current_user.id),
    )).order_by(Message.created_at.asc()).first()
    if first_message:
        events.append({"date": first_message.created_at, "text": "Your first conversation began."})
    my_challenges = {row.challenge_id: row for row in ChallengeParticipant.query.filter_by(user_id=current_user.id).all()}
    shared = ChallengeParticipant.query.filter(ChallengeParticipant.user_id == friend.id, ChallengeParticipant.challenge_id.in_(list(my_challenges) or [-1])).order_by(ChallengeParticipant.joined_at.asc()).first()
    if shared:
        events.append({"date": max(shared.joined_at, my_challenges[shared.challenge_id].joined_at), "text": f"You joined {shared.challenge.title} together."})
    first_support = EncouragementResponse.query.join(EncouragementRequest).filter(or_(
        (EncouragementResponse.user_id == current_user.id) & (EncouragementRequest.user_id == friend.id),
        (EncouragementResponse.user_id == friend.id) & (EncouragementRequest.user_id == current_user.id),
    )).order_by(EncouragementResponse.created_at.asc()).first()
    if first_support:
        events.append({"date": first_support.created_at, "text": "Support was shared between you."})
    first_celebration = Reaction.query.join(Post).filter(
        Post.purpose == "celebrating",
        or_(
            (Post.user_id == current_user.id) & (Reaction.user_id == friend.id),
            (Post.user_id == friend.id) & (Reaction.user_id == current_user.id),
        ),
    ).order_by(Reaction.created_at.asc()).first()
    if first_celebration:
        events.append({"date": first_celebration.created_at, "text": "You celebrated a meaningful moment together."})
    events.sort(key=lambda event: event["date"])
    return render_template("friendship_journey.html", friend=friend, events=events)


@login_required
def live_sessions():
    if request.method == "POST":
        if not current_app.config.get("REALTIME_MEDIA_ENABLED"):
            flash("Live streaming is coming soon.", "info")
            return redirect(url_for("main.live_sessions"))
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title:
            flash("Add a title before going live.", "warning")
            return redirect(url_for("main.live_sessions"))
        session = LiveSession(user_id=current_user.id, title=title, description=description)
        db.session.add(session)
        db.session.flush()
        live_url = url_for("main.live_room", session_id=session.id)
        for follow in Follow.query.filter_by(followed_id=current_user.id).all():
            if follow.follower_id == current_user.id:
                continue
            existing = Notification.query.filter_by(
                user_id=follow.follower_id,
                category="live",
                action_url=live_url,
            ).first()
            if existing:
                continue
            add_notification(
                follow.follower_id,
                "live",
                f"{current_user.username} is live now.",
                live_url,
            )
        db.session.commit()
        socketio.emit(
            "live_started",
            {
                "session_id": session.id,
                "broadcaster_id": current_user.id,
                "broadcaster_name": current_user.profile.display_name,
                "action_url": live_url,
            },
            room=f"user-{current_user.id}",
        )
        flash("Live session started. Followers can now find it in Rise Together.", "success")
        return redirect(url_for("main.live_room", session_id=session.id))
    sessions = LiveSession.query.order_by(LiveSession.created_at.desc()).all()
    return render_template("live_sessions.html", sessions=sessions)


@login_required
def live_room(session_id):
    if not current_app.config.get("REALTIME_MEDIA_ENABLED"):
        flash("Live streaming is coming soon.", "info")
        return redirect(url_for("main.live_sessions"))
    session = LiveSession.query.get_or_404(session_id)
    return render_template(
        "live_room.html",
        session=session,
        is_host=session.user_id == current_user.id,
        ice_servers=get_ice_servers(),
    )


@login_required
def end_live_session(session_id):
    from datetime import datetime

    session = LiveSession.query.get_or_404(session_id)
    if session.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    session.status = "ended"
    session.ended_at = datetime.utcnow()
    db.session.commit()
    from routes.chat import live_broadcasters, live_viewers

    live_broadcasters.pop(session.id, None)
    live_viewers.pop(session.id, None)
    socketio.emit(
        "live_host_left",
        {"session_id": session.id},
        room=f"live-{session.id}",
    )
    flash("Live session ended.", "info")
    return redirect(url_for("main.live_sessions"))


@main_bp.route("/friend/request/<int:user_id>", methods=["POST"])
@login_required
def send_friend_request(user_id):
    target = User.query.get_or_404(user_id)
    if target.id == current_user.id:
        flash("You cannot send a request to yourself.", "warning")
        return redirect(url_for("main.people"))
    existing = FriendRequest.query.filter(
        or_(
            (FriendRequest.sender_id == current_user.id)
            & (FriendRequest.receiver_id == target.id),
            (FriendRequest.sender_id == target.id)
            & (FriendRequest.receiver_id == current_user.id),
        )
    ).first()
    if existing:
        flash("A friend request already exists with this person.", "info")
        return redirect(url_for("main.profile", username=target.username))
    friend_request = FriendRequest(sender_id=current_user.id, receiver_id=target.id)
    db.session.add(friend_request)
    add_notification(
        target.id,
        "friend_request",
        f"{current_user.username} sent you a friend request.",
        url_for("main.people"),
    )
    db.session.commit()
    flash("Friend request sent.", "success")
    return redirect(url_for("main.profile", username=target.username))


@main_bp.route("/friend/request/<int:request_id>/<action>", methods=["POST"])
@login_required
def respond_friend_request(request_id, action):
    friend_request = FriendRequest.query.filter_by(
        id=request_id, receiver_id=current_user.id, status="pending"
    ).first_or_404()
    if action not in {"accept", "decline"}:
        flash("Invalid friend request action.", "warning")
        return redirect(url_for("main.people"))
    from datetime import datetime

    friend_request.status = "accepted" if action == "accept" else "declined"
    friend_request.responded_at = datetime.utcnow()
    if action == "accept":
        add_notification(
            friend_request.sender_id,
            "friend_request",
            f"{current_user.username} accepted your friend request.",
            url_for("main.profile", username=current_user.username),
        )
    db.session.commit()
    flash("Friend request updated.", "success")
    return redirect(url_for("main.people"))


@main_bp.route("/notifications")
@login_required
def notifications():
    notifications = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.updated_at.desc(), Notification.created_at.desc())
        .all()
    )
    return render_template("notifications.html", notifications=notifications)


@main_bp.route("/check-ins")
@login_required
def return_checkins():
    received = ReturnCheckIn.query.filter_by(recipient_id=current_user.id).order_by(ReturnCheckIn.created_at.desc()).limit(50).all()
    sent = ReturnCheckIn.query.filter_by(sender_id=current_user.id).order_by(ReturnCheckIn.created_at.desc()).limit(20).all()
    return render_template("return_checkins.html", received=received, sent=sent)


@main_bp.route("/check-in/<int:user_id>/send", methods=["POST"])
@login_required
def send_return_checkin(user_id):
    recipient = User.query.get_or_404(user_id)
    message = request.form.get("message", "").strip()
    now = datetime.utcnow()
    if not can_send_return_checkin(current_user.id, recipient.id):
        abort(403)
    if not recipient.profile or not recipient.profile.miss_you_notifications_enabled:
        flash("This person is not receiving check-in messages.", "info")
        return redirect(request.referrer or url_for("main.home"))
    if not recipient.last_active_at or recipient.last_active_at > now - timedelta(days=3):
        flash("A check-in suggestion is no longer needed.", "info")
        return redirect(request.referrer or url_for("main.home"))
    if len(message) < 3 or len(message) > 500:
        flash("Write a check-in message between 3 and 500 characters.", "warning")
        return redirect(request.referrer or url_for("main.home"))
    recent_pair = ReturnCheckIn.query.filter(
        ReturnCheckIn.sender_id == current_user.id,
        ReturnCheckIn.recipient_id == recipient.id,
        ReturnCheckIn.created_at >= now - timedelta(days=7),
    ).first()
    recent_total = ReturnCheckIn.query.filter(
        ReturnCheckIn.recipient_id == recipient.id,
        ReturnCheckIn.created_at >= now - timedelta(days=7),
    ).count()
    if recent_pair or recent_total >= 3:
        flash("A recent check-in has already been sent. We are giving them quiet space.", "info")
        return redirect(request.referrer or url_for("main.home"))
    checkin = ReturnCheckIn(sender_id=current_user.id, recipient_id=recipient.id, message=message)
    db.session.add(checkin)
    db.session.flush()
    smart_notify(
        user_id=recipient.id,
        category="return_checkin",
        message=f"{current_user.username} checked on you.",
        action_url=url_for("main.return_checkins") + f"#checkin-{checkin.id}",
        group_key=f"return-checkin:{recipient.id}",
        dedupe_key=f"return-checkin:{checkin.id}:{recipient.id}",
    )
    db.session.commit()
    flash(f"Your check-in was sent to {recipient.username}. You reminded them that they are not alone.", "success")
    return redirect(request.referrer or url_for("main.home"))


@main_bp.route("/check-in/<int:user_id>/dismiss", methods=["POST"])
@login_required
def dismiss_return_suggestion(user_id):
    target = User.query.get_or_404(user_id)
    if not can_send_return_checkin(current_user.id, target.id):
        abort(403)
    dismissal = ReturnSuggestionDismissal.query.filter_by(
        viewer_id=current_user.id, inactive_user_id=target.id,
    ).first()
    if not dismissal:
        dismissal = ReturnSuggestionDismissal(viewer_id=current_user.id, inactive_user_id=target.id)
        db.session.add(dismissal)
    dismissal.dismissed_until = datetime.utcnow() + timedelta(days=30)
    db.session.commit()
    return redirect(request.referrer or url_for("main.home"))


@main_bp.route("/check-in/<int:checkin_id>/thank", methods=["POST"])
@login_required
def thank_return_checkin(checkin_id):
    checkin = ReturnCheckIn.query.filter_by(id=checkin_id, recipient_id=current_user.id).first_or_404()
    if checkin.thanked_at is None:
        checkin.thanked_at = datetime.utcnow()
        blocked = Block.query.filter(or_(
            (Block.blocker_id == current_user.id) & (Block.blocked_id == checkin.sender_id),
            (Block.blocker_id == checkin.sender_id) & (Block.blocked_id == current_user.id),
        )).first()
        if not blocked:
            smart_notify(
                user_id=checkin.sender_id,
                category="return_thanks",
                message=f"{current_user.username} thanked you for checking on them.",
                action_url=url_for("main.profile", username=current_user.username),
                dedupe_key=f"return-thanks:{checkin.id}:{checkin.sender_id}",
            )
        db.session.commit()
    flash("Your thanks were shared.", "success")
    return redirect(request.referrer or url_for("main.return_checkins"))


@main_bp.route("/welcome-back/dismiss", methods=["POST"])
@login_required
def dismiss_welcome_back():
    current_user.return_summary_dismissed_at = datetime.utcnow()
    current_user.return_summary_since = None
    db.session.commit()
    return redirect(url_for("main.home"))


@main_bp.route("/points")
@login_required
def point_history():
    personal_enabled = is_feature_enabled("personal_points")
    family_enabled = is_feature_enabled("family_points")
    if not personal_enabled and not family_enabled:
        return render_template(
            "point_history.html", coming_soon=True, personal_balance=0,
            family_balances=[], transactions=None,
        )
    page = request.args.get("page", 1, type=int)
    page = max(1, page)
    family_ids = [membership.family_id for membership in current_user.family_memberships]
    visibility = []
    if personal_enabled:
        visibility.append(PointTransaction.user_id == current_user.id)
    if family_enabled and family_ids:
        visibility.append(PointTransaction.family_id.in_(family_ids))
    query = PointTransaction.query
    if visibility:
        query = query.filter(or_(*visibility))
    else:
        query = query.filter(PointTransaction.id == -1)
    transactions = query.order_by(
        PointTransaction.created_at.desc(), PointTransaction.id.desc()
    ).paginate(page=page, per_page=30, error_out=False)
    family_balances = []
    if family_enabled:
        family_balances = [
            {"family": membership.family, "balance": family_point_balance(membership.family_id)}
            for membership in current_user.family_memberships
        ]
        family_balances.sort(key=lambda row: (-row["balance"], row["family"].name.lower()))
    return render_template(
        "point_history.html",
        coming_soon=False,
        personal_balance=personal_point_balance(current_user.id) if personal_enabled else 0,
        family_balances=family_balances,
        transactions=transactions,
        personal_enabled=personal_enabled,
        family_enabled=family_enabled,
    )


@main_bp.route("/notification/mark-read/<int:notification_id>", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.filter_by(
        id=notification_id, user_id=current_user.id
    ).first_or_404()
    notification.seen = True
    db.session.commit()
    return redirect(url_for("main.notifications"))


@main_bp.route("/notification/open/<int:notification_id>")
@login_required
def open_notification(notification_id):
    notification = Notification.query.filter_by(
        id=notification_id, user_id=current_user.id
    ).first_or_404()
    notification.seen = True
    db.session.commit()
    destination = notification.action_url or url_for("main.notifications")
    if not destination.startswith("/") or destination.startswith("//"):
        destination = url_for("main.notifications")
    return redirect(destination)


@main_bp.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def mark_all_notifications_read():
    Notification.query.filter_by(user_id=current_user.id, seen=False).update(
        {"seen": True}
    )
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("main.notifications"))


@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        current_user.profile.notifications_enabled = (
            request.form.get("notifications_enabled") == "on"
        )
        current_user.profile.notification_previews_enabled = (
            request.form.get("notification_previews_enabled") == "on"
        )
        current_user.profile.checkin_suggestions_enabled = (
            request.form.get("checkin_suggestions_enabled") == "on"
        )
        current_user.profile.miss_you_notifications_enabled = (
            request.form.get("miss_you_notifications_enabled") == "on"
        )
        current_user.profile.return_summaries_enabled = (
            request.form.get("return_summaries_enabled") == "on"
        )
        current_user.profile.auto_share_completed_challenges = (
            request.form.get("auto_share_completed_challenges") == "on"
        )
        save_notification_preferences(current_user, request.form)
        timezone_name = request.form.get("timezone", "Africa/Kampala").strip()
        try:
            if not timezone_name or len(timezone_name) > 64:
                raise ZoneInfoNotFoundError
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            flash("Choose a valid IANA time zone, such as Africa/Kampala.", "warning")
            return redirect(url_for("main.settings"))
        current_user.profile.timezone = timezone_name
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("main.settings"))
    return render_template(
        "settings.html", notification_categories=NOTIFICATION_CATEGORIES,
        notification_preferences=preference_map(current_user),
    )


@main_bp.route("/streaks")
@login_required
@feature_required("streaks")
def streak_dashboard():
    today = local_activity_date(current_user)
    streaks = {}
    for streak in current_user.streaks.all():
        protected_by_grace = bool(
            streak.grace_days_available > 0
            and streak.last_activity_date == today - timedelta(days=2)
        )
        expired = bool(
            streak.last_activity_date
            and streak.last_activity_date < today - timedelta(days=1)
            and not protected_by_grace
        )
        streaks[streak.streak_type] = {
            "record": streak, "current": 0 if expired else streak.current_count,
            "best": streak.best_count,
            "previous": streak.current_count if expired else streak.previous_count,
        }
    return render_template(
        "streaks.html", definitions=STREAK_DEFINITIONS, streaks=streaks,
        timezone_name=current_user.profile.timezone or "Africa/Kampala",
    )


@main_bp.route("/account/delete", methods=["POST"])
@fresh_login_required
def delete_account():
    if request.form.get("confirm") == "DELETE":
        user = current_user
        reverse_completion_rewards_for_user(user.id, reversed_by_id=user.id)
        db.session.flush()
        db.session.delete(user)
        db.session.commit()
        flash("Your account has been deleted.", "info")
        return redirect(url_for("auth.signup"))
    flash("Please type DELETE to confirm account removal.", "warning")
    return redirect(url_for("main.settings"))
