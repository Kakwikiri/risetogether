from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    country = db.Column(db.String(80), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    profile = db.relationship(
        "Profile", backref="user", uselist=False, cascade="all, delete-orphan"
    )
    posts = db.relationship(
        "Post", backref="author", lazy="dynamic", cascade="all, delete-orphan"
    )
    family_memberships = db.relationship(
        "FamilyMember", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    messages_sent = db.relationship(
        "Message",
        foreign_keys="Message.sender_id",
        back_populates="sender",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    messages_received = db.relationship(
        "Message",
        foreign_keys="Message.recipient_id",
        back_populates="recipient",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False, nullable=False)
    ban_until = db.Column(db.DateTime, nullable=True)
    warning_count = db.Column(db.Integer, default=0, nullable=False)
    reset_phrase_hash = db.Column(db.String(256), default="")
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    is_hidden_from_directory = db.Column(db.Boolean, default=False, nullable=False)
    notifications = db.relationship(
        "Notification",
        backref="recipient",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    reports = db.relationship(
        "Report",
        foreign_keys="Report.reporter_id",
        backref="reporter",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    reports_against = db.relationship(
        "Report",
        foreign_keys="Report.reported_user_id",
        backref="reported_user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def set_reset_phrase(self, phrase):
        self.reset_phrase_hash = generate_password_hash(phrase)

    def check_reset_phrase(self, phrase):
        return bool(self.reset_phrase_hash) and check_password_hash(
            self.reset_phrase_hash, phrase
        )


class Follow(db.Model):
    __tablename__ = "follows"
    __table_args__ = (
        db.UniqueConstraint("follower_id", "followed_id", name="uq_follow_pair"),
    )
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    followed_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    follower = db.relationship(
        "User", foreign_keys=[follower_id], backref="following_links"
    )
    followed = db.relationship(
        "User", foreign_keys=[followed_id], backref="follower_links"
    )


class Profile(db.Model):
    __tablename__ = "profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    display_name = db.Column(db.String(120), nullable=False)
    bio = db.Column(db.Text, default="")
    avatar = db.Column(db.String(255), default="")
    privacy_posts = db.Column(db.String(20), default="public")
    notifications_enabled = db.Column(db.Boolean, default=True)


class Post(db.Model):
    __tablename__ = "posts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content = db.Column(db.Text, nullable=False)
    media_url = db.Column(db.String(255), default="")
    media_type = db.Column(db.String(32), default="text")
    audience = db.Column(db.String(20), default="public", nullable=False)
    is_hidden = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    reactions = db.relationship(
        "Reaction", backref="post", lazy="dynamic", cascade="all, delete-orphan"
    )
    comments = db.relationship(
        "Comment", backref="post", lazy="dynamic", cascade="all, delete-orphan"
    )
    reports = db.relationship(
        "Report", backref="post", lazy="dynamic", cascade="all, delete-orphan"
    )
    shares = db.relationship(
        "PostShare", backref="post", lazy="dynamic", cascade="all, delete-orphan"
    )


class PostShare(db.Model):
    __tablename__ = "post_shares"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", foreign_keys=[user_id], backref="post_shares")
    recipient = db.relationship(
        "User", foreign_keys=[recipient_id], backref="received_post_shares"
    )


class MediaAsset(db.Model):
    __tablename__ = "media_assets"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False, index=True)
    content_type = db.Column(db.String(120), default="application/octet-stream")
    media_type = db.Column(db.String(32), default="file")
    data = db.Column(db.LargeBinary, nullable=False)
    size = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Reaction(db.Model):
    __tablename__ = "reactions"
    __table_args__ = (
        db.UniqueConstraint("post_id", "user_id", "type", name="uq_reaction_user_type"),
    )
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content = db.Column(db.Text, nullable=False)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="comments", foreign_keys=[user_id])
    replies = db.relationship(
        "Comment",
        backref=db.backref("parent", remote_side=[id]),
        lazy="dynamic",
        cascade="all, delete-orphan",
        single_parent=True,
    )


class CommentReaction(db.Model):
    __tablename__ = "comment_reactions"
    __table_args__ = (
        db.UniqueConstraint("comment_id", "user_id", name="uq_comment_like_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(
        db.Integer, db.ForeignKey("comments.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    comment = db.relationship("Comment", backref=db.backref("likes", lazy="dynamic"))
    user = db.relationship("User", backref="comment_likes")


class Family(db.Model):
    __tablename__ = "families"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(40), default="friendship_and_support", nullable=False)
    custom_category = db.Column(db.String(80), default="")
    goal_title = db.Column(db.String(160), default="")
    goal_description = db.Column(db.Text, default="")
    start_date = db.Column(db.DateTime, nullable=True)
    target_date = db.Column(db.DateTime, nullable=True)
    privacy = db.Column(db.String(20), default="public", nullable=False)
    member_limit = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    members = db.relationship(
        "FamilyMember", backref="family", lazy="dynamic", cascade="all, delete-orphan"
    )
    posts = db.relationship(
        "Post", backref="family", lazy="dynamic", cascade="all, delete-orphan"
    )


class FamilyMember(db.Model):
    __tablename__ = "family_members"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role = db.Column(db.String(20), default="member")
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)


class FamilyChallenge(db.Model):
    __tablename__ = "family_challenges"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    creator_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    challenge_type = db.Column(db.String(40), default="task", nullable=False)
    points = db.Column(db.Integer, default=10, nullable=False)
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship("Family", backref=db.backref("challenges", lazy="dynamic", cascade="all, delete-orphan"))
    creator = db.relationship("User", backref=db.backref("created_family_challenges", lazy="dynamic"))


class ChallengeCompletion(db.Model):
    __tablename__ = "challenge_completions"
    __table_args__ = (
        db.UniqueConstraint("challenge_id", "user_id", name="uq_challenge_completion_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("family_challenges.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)
    evidence_text = db.Column(db.Text, default="")
    evidence_media_url = db.Column(db.String(255), default="")
    verification_status = db.Column(db.String(20), default="completed", nullable=False)
    challenge = db.relationship("FamilyChallenge", backref=db.backref("completions", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("challenge_completions", lazy="dynamic", cascade="all, delete-orphan"))


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True
    )
    content = db.Column(db.Text, nullable=False)
    media_url = db.Column(db.String(255), default="")
    media_type = db.Column(db.String(32), default="text")
    reply_to_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    view_once = db.Column(db.Boolean, default=False, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    pinned_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered = db.Column(db.Boolean, default=False)
    reply_to = db.relationship("Message", remote_side=[id], backref="replies")
    sender = db.relationship(
        "User",
        foreign_keys=[sender_id],
        back_populates="messages_sent",
    )
    recipient = db.relationship(
        "User",
        foreign_keys=[recipient_id],
        back_populates="messages_received",
    )
    family = db.relationship("Family", backref="messages", foreign_keys=[family_id])


class MessageDeletion(db.Model):
    __tablename__ = "message_deletions"
    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="uq_message_delete_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    message = db.relationship("Message", backref=db.backref("deletions", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("message_deletions", lazy="dynamic", cascade="all, delete-orphan"))


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category = db.Column(db.String(64), nullable=False)
    message = db.Column(db.Text, nullable=False)
    action_url = db.Column(db.String(255), default="")
    seen = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Block(db.Model):
    __tablename__ = "blocks"
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    blocked_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Report(db.Model):
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reported_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class FriendRequest(db.Model):
    __tablename__ = "friend_requests"
    __table_args__ = (
        db.UniqueConstraint(
            "sender_id", "receiver_id", name="uq_friend_request_sender_receiver"
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    receiver_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime)
    sender = db.relationship(
        "User",
        foreign_keys=[sender_id],
        backref=db.backref(
            "sent_friend_requests", lazy="dynamic", cascade="all, delete-orphan"
        ),
    )
    receiver = db.relationship(
        "User",
        foreign_keys=[receiver_id],
        backref=db.backref(
            "received_friend_requests", lazy="dynamic", cascade="all, delete-orphan"
        ),
    )


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token = db.Column(db.String(128), unique=True, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    user = db.relationship("User", backref="password_reset_tokens")


class HelpRequest(db.Model):
    __tablename__ = "help_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    subject = db.Column(db.String(160), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="help_requests")


class SiteSetting(db.Model):
    __tablename__ = "site_settings"
    key = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LiveSession(db.Model):
    __tablename__ = "live_sessions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="live", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship("User", backref="live_sessions")
