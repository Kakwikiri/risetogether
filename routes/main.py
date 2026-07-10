from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from extensions import db
from helpers import REACTION_LABELS, allowed_file, get_media_type, save_media
from models import (
    Block,
    Comment,
    CommentReaction,
    Family,
    FamilyMember,
    Follow,
    FriendRequest,
    LiveSession,
    Notification,
    Post,
    PostShare,
    Reaction,
    User,
)

main_bp = Blueprint("main", __name__)
POST_AUDIENCES = {"public", "friends", "family", "private"}


@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        video_only = request.args.get("type") == "videos"
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
        available_families = Family.query.order_by(Family.created_at.desc()).limit(8).all()
        trending_posts = sorted(posts, key=trend_score, reverse=True)[:5]
        if video_only:
            posts = [post for post in posts if post.media_type == "video"]
        if query:
            posts = [
                post
                for post in posts
                if query.lower() in (post.content or "").lower()
                or query.lower() in post.author.username.lower()
                or query.lower() in post.author.profile.display_name.lower()
            ]
        return render_template(
            "feed.html",
            posts=posts,
            reactions=REACTION_LABELS,
            families=families,
            available_families=available_families,
            trending_posts=trending_posts,
            video_only=video_only,
            query=query,
        )
    return render_template("landing.html")


def get_reaction_counts(post):
    return {
        key: Reaction.query.filter_by(post_id=post.id, type=key).count()
        for key in REACTION_LABELS
    }


def trend_score(post):
    return (post.reactions.count() * 2) + post.comments.count() + (post.shares.count() * 3)


def user_family_ids(user):
    if not user.is_authenticated:
        return []
    return [membership.family_id for membership in user.family_memberships]


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


def can_view_post(post):
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
    family_memberships = FamilyMember.query.filter_by(user_id=user.id).all()
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
    if not (current_user.is_authenticated and current_user.id == user.id):
        posts_query = posts_query.filter_by(audience="public", is_hidden=False)
    posts = posts_query.order_by(Post.created_at.desc()).all()
    following = [
        follow.followed
        for follow in Follow.query.filter_by(follower_id=user.id)
        .order_by(Follow.created_at.desc())
        .limit(12)
        .all()
    ]
    return render_template(
        "profile.html",
        user=user,
        profile=profile,
        memberships=family_memberships,
        friend_status=friend_status,
        friend_request=friend_request,
        is_following=is_following,
        posts=posts,
        follower_count=Follow.query.filter_by(followed_id=user.id).count(),
        following_count=Follow.query.filter_by(follower_id=user.id).count(),
        following=following,
    )


@main_bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    profile = current_user.profile
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        bio = request.form.get("bio", "").strip()
        privacy = request.form.get("privacy_posts", "public")
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        reset_phrase = request.form.get("reset_phrase", "").strip()
        avatar_file = request.files.get("avatar")
        if avatar_file and allowed_file(avatar_file.filename):
            filename = save_media(avatar_file)
            if filename:
                profile.avatar = filename
        profile.display_name = display_name or profile.display_name
        profile.bio = bio
        profile.privacy_posts = privacy if privacy in POST_AUDIENCES else "public"
        if current_password or new_password or confirm_password:
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
    if audience not in POST_AUDIENCES:
        audience = "public"
    media_file = request.files.get("media")
    if not content and not media_file:
        flash("Please add text, image, or audio/video to your post.", "warning")
        return redirect(url_for("main.home"))
    media_url = ""
    media_type = "text"
    if media_file and allowed_file(media_file.filename):
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
            db.session.add(
                Notification(
                    user_id=follow.follower_id,
                    category="followed_post",
                    message=f"{current_user.username} shared a new post.",
                    action_url=url_for("main.post_detail", post_id=post.id),
                )
            )
    db.session.commit()
    flash("Post shared with RiseTogether.", "success")
    return redirect(url_for("main.home"))


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
        db.session.add(
            Notification(
                user_id=target.id,
                category="follow",
                message=f"{current_user.username} followed you.",
                action_url=url_for("main.profile", username=current_user.username),
            )
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
        flash("Invalid reaction.", "warning")
        return redirect(url_for("main.home"))
    existing_reactions = Reaction.query.filter_by(
        post_id=post.id, user_id=current_user.id, type=reaction_type
    ).all()
    if existing_reactions:
        for reaction in existing_reactions:
            db.session.delete(reaction)
        db.session.commit()
        flash("Reaction removed.", "info")
        return redirect(url_for("main.post_detail", post_id=post.id))
    reaction = Reaction(post_id=post.id, user_id=current_user.id, type=reaction_type)
    db.session.add(reaction)
    if post.user_id != current_user.id:
        db.session.add(
            Notification(
                user_id=post.user_id,
                category="reaction",
                message=f"{current_user.username} reacted to your post.",
                action_url=url_for("main.post_detail", post_id=post.id),
            )
        )
    db.session.commit()
    flash("Your support reaction has been added.", "success")
    return redirect(url_for("main.post_detail", post_id=post.id))


@main_bp.route("/post/<int:post_id>/share", methods=["GET", "POST"])
@login_required
def share_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post):
        abort(404)

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

    if request.method == "POST":
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
                post_id=post.id,
                user_id=current_user.id,
                recipient_id=recipient.id,
            ).first()
            if existing_share:
                continue
            db.session.add(
                PostShare(
                    post_id=post.id,
                    user_id=current_user.id,
                    recipient_id=recipient.id,
                )
            )
            db.session.add(
                Notification(
                    user_id=recipient.id,
                    category="share",
                    message=f"{current_user.username} shared a post with you.",
                    action_url=url_for("main.post_detail", post_id=post.id),
                )
            )
            shared_count += 1

        if shared_count and post.user_id != current_user.id:
            db.session.add(
                Notification(
                    user_id=post.user_id,
                    category="share",
                    message=f"{current_user.username} shared your post with {shared_count} people.",
                    action_url=url_for("main.post_detail", post_id=post.id),
                )
            )
        db.session.commit()
        if shared_count:
            flash(f"Post shared with {shared_count} people.", "success")
        else:
            flash("You already shared this post with everyone selected.", "info")
        return redirect(url_for("main.post_detail", post_id=post.id))

    return render_template("share_post.html", post=post, recipients=recipients)


@main_bp.route("/post/<int:post_id>", methods=["GET", "POST"])
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_view_post(post):
        abort(404)
    if request.method == "POST" and current_user.is_authenticated:
        content = request.form.get("comment", "").strip()
        parent_id = request.form.get("parent_id")
        parent = None
        if parent_id:
            parent = Comment.query.filter_by(id=parent_id, post_id=post.id).first()
        if content:
            comment = Comment(
                post_id=post.id,
                user_id=current_user.id,
                content=content,
                parent_id=parent.id if parent else None,
            )
            db.session.add(comment)
            notify_user_id = parent.user_id if parent else post.user_id
            if notify_user_id != current_user.id:
                notification = Notification(
                    user_id=notify_user_id,
                    category="comment",
                    message=(
                        f"{current_user.username} replied to your comment."
                        if parent
                        else f"{current_user.username} commented on your post."
                    ),
                    action_url=url_for("main.post_detail", post_id=post.id),
                )
                db.session.add(notification)
            db.session.commit()
            flash("Your comment is added.", "success")
        return redirect(url_for("main.post_detail", post_id=post.id))
    comments = (
        post.comments.filter_by(parent_id=None).order_by(Comment.created_at.asc()).all()
    )
    return render_template(
        "post_detail.html",
        post=post,
        comments=comments,
        reactions=REACTION_LABELS,
        reaction_counts=get_reaction_counts(post),
    )


@main_bp.route("/post/<int:post_id>/<action>", methods=["POST"])
@login_required
def manage_post(post_id, action):
    post = Post.query.filter_by(id=post_id, user_id=current_user.id).first_or_404()
    if action == "delete":
        db.session.delete(post)
        db.session.commit()
        flash("Post deleted.", "info")
        return redirect(url_for("main.home"))
    if action == "hide":
        post.is_hidden = not post.is_hidden
        db.session.commit()
        flash("Post visibility updated.", "success")
        return redirect(request.referrer or url_for("main.home"))
    abort(404)


@main_bp.route("/comment/<int:comment_id>/like", methods=["POST"])
@login_required
def like_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if not can_view_post(comment.post):
        abort(404)
    existing = CommentReaction.query.filter_by(
        comment_id=comment.id, user_id=current_user.id
    ).first()
    if existing:
        db.session.delete(existing)
        flash("Comment like removed.", "info")
    else:
        db.session.add(CommentReaction(comment_id=comment.id, user_id=current_user.id))
        if comment.user_id != current_user.id:
            db.session.add(
                Notification(
                    user_id=comment.user_id,
                    category="comment",
                    message=f"{current_user.username} liked your comment.",
                    action_url=url_for("main.post_detail", post_id=comment.post_id),
                )
            )
        flash("Comment liked.", "success")
    db.session.commit()
    return redirect(url_for("main.post_detail", post_id=comment.post_id))


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


@main_bp.route("/live", methods=["GET", "POST"])
@login_required
def live_sessions():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title:
            flash("Add a title before going live.", "warning")
            return redirect(url_for("main.live_sessions"))
        db.session.add(
            LiveSession(user_id=current_user.id, title=title, description=description)
        )
        db.session.commit()
        flash("Live session started. Share your room link with viewers.", "success")
        return redirect(url_for("main.live_sessions"))
    sessions = LiveSession.query.order_by(LiveSession.created_at.desc()).all()
    return render_template("live_sessions.html", sessions=sessions)


@main_bp.route("/live/<int:session_id>/end", methods=["POST"])
@login_required
def end_live_session(session_id):
    from datetime import datetime

    session = LiveSession.query.get_or_404(session_id)
    if session.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    session.status = "ended"
    session.ended_at = datetime.utcnow()
    db.session.commit()
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
    db.session.add(
        Notification(
            user_id=target.id,
            category="friend_request",
            message=f"{current_user.username} sent you a friend request.",
            action_url=url_for("main.people"),
        )
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
        db.session.add(
            Notification(
                user_id=friend_request.sender_id,
                category="friend_request",
                message=f"{current_user.username} accepted your friend request.",
                action_url=url_for("main.profile", username=current_user.username),
            )
        )
    db.session.commit()
    flash("Friend request updated.", "success")
    return redirect(url_for("main.people"))


@main_bp.route("/notifications")
@login_required
def notifications():
    notifications = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return render_template("notifications.html", notifications=notifications)


@main_bp.route("/notification/mark-read/<int:notification_id>", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.filter_by(
        id=notification_id, user_id=current_user.id
    ).first_or_404()
    notification.seen = True
    db.session.commit()
    return redirect(url_for("main.notifications"))


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
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html")


@main_bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    if request.form.get("confirm") == "DELETE":
        user = current_user
        logout = request.form.get("logout")
        db.session.delete(user)
        db.session.commit()
        flash("Your account has been deleted.", "info")
        return redirect(url_for("auth.signup"))
    flash("Please type DELETE to confirm account removal.", "warning")
    return redirect(url_for("main.settings"))
