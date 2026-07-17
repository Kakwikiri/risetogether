from datetime import date, datetime

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
    last_active_at = db.Column(db.DateTime, nullable=True, index=True)
    return_summary_since = db.Column(db.DateTime, nullable=True)
    return_summary_dismissed_at = db.Column(db.DateTime, nullable=True)
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
    admin_role = db.Column(db.String(20), default="")
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
    interests = db.Column(db.Text, default="", nullable=False)
    avatar = db.Column(db.String(255), default="")
    privacy_posts = db.Column(db.String(20), default="public")
    notifications_enabled = db.Column(db.Boolean, default=True)
    notification_previews_enabled = db.Column(db.Boolean, default=True)
    checkin_suggestions_enabled = db.Column(db.Boolean, default=True, nullable=False)
    miss_you_notifications_enabled = db.Column(db.Boolean, default=True, nullable=False)
    return_summaries_enabled = db.Column(db.Boolean, default=True, nullable=False)
    auto_share_completed_challenges = db.Column(db.Boolean, default=False, nullable=False)
    timezone = db.Column(db.String(64), default="Africa/Kampala", nullable=False)
    show_point_balance = db.Column(db.Boolean, default=False, nullable=False)
    show_streaks = db.Column(db.Boolean, default=True, nullable=False)
    show_achievements = db.Column(db.Boolean, default=True, nullable=False)
    show_family_memberships = db.Column(db.Boolean, default=True, nullable=False)
    show_checkins = db.Column(db.Boolean, default=False, nullable=False)
    show_goal_progress = db.Column(db.Boolean, default=False, nullable=False)


class RiseBadgeAssignment(db.Model):
    __tablename__ = "rise_badge_assignments"
    __table_args__ = (
        db.UniqueConstraint("badge_type", "user_id", name="uq_rise_badge_user_type"),
        db.UniqueConstraint("badge_type", "family_id", name="uq_rise_badge_family_type"),
        db.CheckConstraint(
            "(user_id IS NOT NULL AND family_id IS NULL) OR (user_id IS NULL AND family_id IS NOT NULL)",
            name="ck_rise_badge_one_subject",
        ),
        db.CheckConstraint(
            "badge_type IN ('verified_person','official_organization','trusted_family','platform_moderator')",
            name="ck_rise_badge_assignable_type",
        ),
        db.CheckConstraint("status IN ('active','revoked')", name="ck_rise_badge_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    badge_type = db.Column(db.String(40), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True)
    status = db.Column(db.String(16), default="active", nullable=False, index=True)
    verification_note = db.Column(db.String(500), nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("rise_badge_assignments", lazy="dynamic", cascade="all, delete-orphan"))
    family = db.relationship("Family", foreign_keys=[family_id], backref=db.backref("rise_badge_assignments", lazy="dynamic", cascade="all, delete-orphan"))
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])


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
    purpose = db.Column(db.String(32), default="normal", nullable=False)
    is_hidden = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    original_post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    post_type = db.Column(db.String(32), default="standard", nullable=False)
    achievement_type = db.Column(db.String(48), default="")
    challenge_completion_id = db.Column(
        db.Integer,
        db.ForeignKey("challenge_completions.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="SET NULL"), unique=True, nullable=True)
    encouraging_message = db.Column(db.String(240), default="")
    original_post = db.relationship(
        "Post",
        remote_side=[id],
        backref=db.backref(
            "feed_reshares",
            lazy="dynamic",
            cascade="all, delete-orphan",
            passive_deletes=True,
        ),
        foreign_keys=[original_post_id],
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


class PostSupportResponse(db.Model):
    __tablename__ = "post_support_responses"
    __table_args__ = (
        db.UniqueConstraint("post_id", "user_id", "action", name="uq_post_support_action_user"),
        db.CheckConstraint("action IN ('idea','may_help','listen')", name="ck_post_support_action"),
        db.CheckConstraint("visibility IN ('public','private')", name="ck_post_support_visibility"),
        db.CheckConstraint("status IN ('shared','pending','accepted','declined')", name="ck_post_support_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action = db.Column(db.String(16), nullable=False)
    explanation = db.Column(db.String(1000), default="", nullable=False)
    visibility = db.Column(db.String(16), default="private", nullable=False)
    status = db.Column(db.String(16), default="shared", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    post = db.relationship("Post", backref=db.backref("support_responses", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("post_support_responses", lazy="dynamic", cascade="all, delete-orphan"))


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
        db.UniqueConstraint("post_id", "user_id", name="uq_reaction_post_user"),
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
    user = db.relationship("User", backref=db.backref("post_reactions", lazy="dynamic"))


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
    edited_at = db.Column(db.DateTime, nullable=True)
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
    type = db.Column(db.String(32), default="support", nullable=False)
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
    member_limit = db.Column(db.Integer, default=50, nullable=False)
    profile_image = db.Column(db.String(255), default="")
    profile_image_public_id = db.Column(db.String(255), default="")
    banner_image = db.Column(db.String(255), default="", nullable=False)
    theme = db.Column(db.String(32), default="classic", nullable=False)
    certificate_style = db.Column(db.String(32), default="growth", nullable=False)
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


class PremiumSubscription(db.Model):
    __tablename__ = "premium_subscriptions"
    __table_args__ = (
        db.CheckConstraint(
            "(user_id IS NOT NULL AND family_id IS NULL) OR "
            "(user_id IS NULL AND family_id IS NOT NULL)",
            name="ck_premium_subscription_one_subject",
        ),
        db.CheckConstraint(
            "plan IN ('personal','family')", name="ck_premium_subscription_plan"
        ),
        db.CheckConstraint(
            "billing_period IN ('monthly','yearly','lifetime')",
            name="ck_premium_subscription_period",
        ),
        db.CheckConstraint(
            "status IN ('active','expired','cancelled')",
            name="ck_premium_subscription_status",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plan = db.Column(db.String(20), nullable=False)
    billing_period = db.Column(db.String(20), nullable=False)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    status = db.Column(db.String(20), default="active", nullable=False, index=True)
    auto_renew = db.Column(db.Boolean, default=False, nullable=False)
    granted_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    user = db.relationship(
        "User", foreign_keys=[user_id],
        backref=db.backref("premium_subscriptions", lazy="dynamic", cascade="all, delete-orphan"),
    )
    family = db.relationship(
        "Family", foreign_keys=[family_id],
        backref=db.backref("premium_subscriptions", lazy="dynamic", cascade="all, delete-orphan"),
    )
    granted_by = db.relationship("User", foreign_keys=[granted_by_id])


class ReferralCode(db.Model):
    __tablename__ = "referral_codes"
    id = db.Column(db.Integer, primary_key=True)
    inviter_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True)
    token = db.Column(db.String(80), unique=True, nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    inviter = db.relationship("User", backref=db.backref("referral_codes", lazy="dynamic", cascade="all, delete-orphan"))
    family = db.relationship("Family", backref=db.backref("referral_codes", lazy="dynamic", cascade="all, delete-orphan"))


class ReferralConversion(db.Model):
    __tablename__ = "referral_conversions"
    id = db.Column(db.Integer, primary_key=True)
    referral_code_id = db.Column(db.Integer, db.ForeignKey("referral_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    referred_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    qualified_at = db.Column(db.DateTime, nullable=True)
    rewarded_at = db.Column(db.DateTime, nullable=True)
    referral_code = db.relationship("ReferralCode", backref=db.backref("conversions", lazy="dynamic", cascade="all, delete-orphan"))
    referred_user = db.relationship("User", backref=db.backref("referral_conversion", uselist=False))


class UserActivityDay(db.Model):
    __tablename__ = "user_activity_days"
    __table_args__ = (db.UniqueConstraint("user_id", "activity_date", name="uq_user_activity_day"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("activity_days", lazy="dynamic", cascade="all, delete-orphan"))


class VerificationApplication(db.Model):
    __tablename__ = "verification_applications"
    __table_args__ = (
        db.CheckConstraint(
            "(user_id IS NOT NULL AND family_id IS NULL) OR "
            "(user_id IS NULL AND family_id IS NOT NULL)",
            name="ck_verification_application_one_subject",
        ),
        db.CheckConstraint(
            "application_type IN ('verified_user','official_organization','trusted_family')",
            name="ck_verification_application_type",
        ),
        db.CheckConstraint(
            "status IN ('pending','approved','rejected','withdrawn')",
            name="ck_verification_application_status",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    application_type = db.Column(db.String(32), nullable=False)
    statement = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = db.Column(db.String(500), default="", nullable=False)
    user = db.relationship("User", foreign_keys=[user_id])
    family = db.relationship("Family", foreign_keys=[family_id])
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])


class FamilyUpgradePurchase(db.Model):
    __tablename__ = "family_upgrade_purchases"
    __table_args__ = (
        db.UniqueConstraint("family_id", "upgrade_key", name="uq_family_upgrade_purchase"),
        db.CheckConstraint("cost > 0", name="ck_family_upgrade_positive_cost"),
    )
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upgrade_key = db.Column(db.String(64), nullable=False)
    cost = db.Column(db.Integer, nullable=False)
    purchased_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    family = db.relationship(
        "Family", backref=db.backref("upgrade_purchases", lazy="dynamic", cascade="all, delete-orphan")
    )
    purchased_by = db.relationship("User", foreign_keys=[purchased_by_id])


class FamilyGalleryItem(db.Model):
    __tablename__ = "family_gallery_items"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    media_url = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.String(240), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    family = db.relationship(
        "Family", backref=db.backref("gallery_items", lazy="dynamic", cascade="all, delete-orphan")
    )
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])


class FamilyContributionCampaign(db.Model):
    __tablename__ = "family_contribution_campaigns"
    __table_args__ = (
        db.UniqueConstraint("family_id", "active_slot", name="uq_family_active_campaign"),
        db.CheckConstraint("points_required > 0", name="ck_campaign_positive_goal"),
        db.CheckConstraint(
            "status IN ('active', 'reached', 'cancelled', 'activated')",
            name="ck_campaign_status",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upgrade_key = db.Column(db.String(64), nullable=False)
    points_required = db.Column(db.Integer, nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deadline = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(24), default="active", nullable=False, index=True)
    active_slot = db.Column(db.Boolean, default=True, nullable=True)
    highest_milestone = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    activated_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    family = db.relationship(
        "Family", backref=db.backref("contribution_campaigns", lazy="dynamic", cascade="all, delete-orphan")
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class FamilyCampaignContribution(db.Model):
    __tablename__ = "family_campaign_contributions"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_campaign_contribution_positive"),
        db.UniqueConstraint("contribution_key", name="uq_campaign_contribution_key"),
    )
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(
        db.Integer, db.ForeignKey("family_contribution_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount = db.Column(db.Integer, nullable=False)
    contribution_key = db.Column(db.String(120), nullable=False)
    refunded = db.Column(db.Boolean, default=False, nullable=False)
    refunded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    campaign = db.relationship(
        "FamilyContributionCampaign",
        backref=db.backref("contributions", lazy="dynamic", cascade="all, delete-orphan"),
    )
    user = db.relationship("User", foreign_keys=[user_id])


class FamilyMember(db.Model):
    __tablename__ = "family_members"
    __table_args__ = (
        db.UniqueConstraint("family_id", "user_id", name="uq_family_member_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role = db.Column(db.String(20), default="member")
    can_create_polls = db.Column(db.Boolean, default=False, nullable=False)
    can_create_quizzes = db.Column(db.Boolean, default=False, nullable=False)
    can_create_challenges = db.Column(db.Boolean, default=False, nullable=False)
    can_create_campaigns = db.Column(db.Boolean, default=False, nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)


class FamilyWeeklyReport(db.Model):
    __tablename__ = "family_weekly_reports"
    __table_args__ = (
        db.UniqueConstraint("family_id", "week_start", name="uq_family_weekly_report"),
    )
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week_start = db.Column(db.Date, nullable=False, index=True)
    week_end = db.Column(db.Date, nullable=False)
    snapshot = db.Column(db.JSON, nullable=False, default=dict)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notified_at = db.Column(db.DateTime, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True)
    published_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    family = db.relationship(
        "Family", backref=db.backref("weekly_reports", lazy="dynamic", cascade="all, delete-orphan")
    )
    published_by = db.relationship("User", foreign_keys=[published_by_id])
    published_post = db.relationship("Post", foreign_keys=[published_post_id])


class PointTransaction(db.Model):
    __tablename__ = "point_transactions"
    __table_args__ = (
        db.CheckConstraint(
            "(user_id IS NOT NULL AND family_id IS NULL) OR "
            "(user_id IS NULL AND family_id IS NOT NULL)",
            name="ck_point_transaction_single_recipient",
        ),
        db.CheckConstraint("amount > 0", name="ck_point_transaction_positive_amount"),
        db.CheckConstraint(
            "transaction_kind IN ('award', 'spend')",
            name="ck_point_transaction_kind",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True
    )
    amount = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    source_type = db.Column(db.String(64), nullable=False, index=True)
    source_id = db.Column(db.Integer, nullable=True)
    transaction_kind = db.Column(db.String(16), default="award", nullable=False)
    unique_reward_key = db.Column(db.String(180), unique=True, nullable=False)
    reversed = db.Column(db.Boolean, default=False, nullable=False)
    reversed_at = db.Column(db.DateTime, nullable=True)
    reversed_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reversal_reason = db.Column(db.String(500), default="", nullable=False)
    suspicious = db.Column(db.Boolean, default=False, nullable=False, index=True)
    suspicious_reason = db.Column(db.String(500), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    awarded_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user = db.relationship(
        "User", foreign_keys=[user_id], backref=db.backref("point_transactions", lazy="dynamic")
    )
    family = db.relationship(
        "Family", foreign_keys=[family_id], backref=db.backref("point_transactions", lazy="dynamic")
    )
    awarded_by = db.relationship("User", foreign_keys=[awarded_by_id])
    reversed_by = db.relationship("User", foreign_keys=[reversed_by_id])


class PointSecurityEvent(db.Model):
    __tablename__ = "point_security_events"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type = db.Column(db.String(64), nullable=False, index=True)
    source_type = db.Column(db.String(64), default="", nullable=False)
    source_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.String(500), default="", nullable=False)
    ip_address = db.Column(db.String(64), default="", nullable=False)
    resolved = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    user = db.relationship("User", foreign_keys=[user_id])
    family = db.relationship("Family", foreign_keys=[family_id])


class FamilyModerationLog(db.Model):
    __tablename__ = "family_moderation_logs"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    actor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action = db.Column(db.String(64), nullable=False)
    previous_role = db.Column(db.String(20), default="")
    new_role = db.Column(db.String(20), default="")
    reason = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship("Family", backref=db.backref("moderation_logs", lazy="dynamic", cascade="all, delete-orphan"))
    actor = db.relationship("User", foreign_keys=[actor_id], backref="family_moderation_actions")
    target_user = db.relationship("User", foreign_keys=[target_user_id], backref="family_moderation_events")


class FamilyMemberRestriction(db.Model):
    __tablename__ = "family_member_restrictions"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    restriction_type = db.Column(db.String(32), nullable=False)
    reason = db.Column(db.Text, default="")
    starts_at = db.Column(db.DateTime, default=datetime.utcnow)
    ends_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship("Family", backref=db.backref("member_restrictions", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", foreign_keys=[user_id], backref="family_restrictions")
    created_by = db.relationship("User", foreign_keys=[created_by_id], backref="created_family_restrictions")


class FamilyPoll(db.Model):
    __tablename__ = "family_polls"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    creator_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    question = db.Column(db.String(240), nullable=False)
    allows_multiple_choices = db.Column(db.Boolean, default=False, nullable=False)
    anonymous_voting = db.Column(db.Boolean, default=False, nullable=False)
    results_visibility = db.Column(db.String(24), default="after_vote", nullable=False)
    allow_vote_changes = db.Column(db.Boolean, default=True, nullable=False)
    closes_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="open", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship("Family", backref=db.backref("polls", lazy="dynamic", cascade="all, delete-orphan"))
    creator = db.relationship("User", foreign_keys=[creator_id], backref="created_family_polls")


class FamilyPollOption(db.Model):
    __tablename__ = "family_poll_options"
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(
        db.Integer, db.ForeignKey("family_polls.id", ondelete="CASCADE"), nullable=False
    )
    option_text = db.Column(db.String(180), nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    poll = db.relationship("FamilyPoll", backref=db.backref("options", lazy="dynamic", cascade="all, delete-orphan"))


class FamilyPollVote(db.Model):
    __tablename__ = "family_poll_votes"
    __table_args__ = (
        db.UniqueConstraint("poll_id", "option_id", "user_id", name="uq_family_poll_option_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(
        db.Integer, db.ForeignKey("family_polls.id", ondelete="CASCADE"), nullable=False
    )
    option_id = db.Column(
        db.Integer, db.ForeignKey("family_poll_options.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    poll = db.relationship("FamilyPoll", backref=db.backref("votes", lazy="dynamic", cascade="all, delete-orphan"))
    option = db.relationship("FamilyPollOption", backref=db.backref("votes", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("family_poll_votes", lazy="dynamic", cascade="all, delete-orphan"))


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
    reward_tier = db.Column(db.String(32), default="easy", nullable=False)
    completion_frequency = db.Column(db.String(24), default="one_time", nullable=False)
    custom_frequency_days = db.Column(db.Integer, nullable=True)
    evidence_requirement = db.Column(db.String(24), default="none", nullable=False)
    participant_scope = db.Column(db.String(32), default="all_members", nullable=False)
    max_participants = db.Column(db.Integer, nullable=True)
    visibility = db.Column(db.String(24), default="family", nullable=False)
    requires_admin_approval = db.Column(db.Boolean, default=False, nullable=False)
    allow_achievement_sharing = db.Column(db.Boolean, default=True, nullable=False)
    mandatory_all_members = db.Column(db.Boolean, default=False, nullable=False)
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship("Family", backref=db.backref("challenges", lazy="dynamic", cascade="all, delete-orphan"))
    creator = db.relationship("User", backref=db.backref("created_family_challenges", lazy="dynamic"))


class ChallengeParticipant(db.Model):
    __tablename__ = "challenge_participants"
    __table_args__ = (
        db.UniqueConstraint("challenge_id", "user_id", name="uq_challenge_participant_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("family_challenges.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    challenge = db.relationship(
        "FamilyChallenge",
        backref=db.backref("participants", lazy="dynamic", cascade="all, delete-orphan"),
    )
    user = db.relationship(
        "User", backref=db.backref("challenge_participations", lazy="dynamic", cascade="all, delete-orphan")
    )


class ChallengeCompletion(db.Model):
    __tablename__ = "challenge_completions"
    __table_args__ = (
        db.UniqueConstraint(
            "challenge_id", "user_id", "period_key",
            name="uq_challenge_completion_period",
        ),
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
    period_key = db.Column(db.String(32), default="once", nullable=False)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    challenge = db.relationship("FamilyChallenge", backref=db.backref("completions", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("challenge_completions", lazy="dynamic", cascade="all, delete-orphan"))
    achievement_post = db.relationship(
        "Post",
        backref="challenge_completion",
        uselist=False,
        foreign_keys="Post.challenge_completion_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Quiz(db.Model):
    __tablename__ = "quizzes"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    creator_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    opens_at = db.Column(db.DateTime, nullable=True)
    closes_at = db.Column(db.DateTime, nullable=True)
    time_limit_seconds = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default="open", nullable=False)
    allow_multiple_attempts = db.Column(db.Boolean, default=False, nullable=False)
    show_correct_answers = db.Column(db.Boolean, default=True, nullable=False)
    pass_mark = db.Column(db.Integer, default=60, nullable=False)
    attempt_limit = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship("Family", backref=db.backref("quizzes", lazy="dynamic", cascade="all, delete-orphan"))
    creator = db.relationship("User", backref=db.backref("created_quizzes", lazy="dynamic"))


class QuizQuestion(db.Model):
    __tablename__ = "quiz_questions"
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(
        db.Integer, db.ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
    )
    question_text = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(32), default="multiple_choice", nullable=False)
    points = db.Column(db.Integer, default=1, nullable=False)
    position = db.Column(db.Integer, default=1, nullable=False)
    explanation = db.Column(db.Text, default="")
    quiz = db.relationship("Quiz", backref=db.backref("questions", lazy="dynamic", cascade="all, delete-orphan"))


class QuizChoice(db.Model):
    __tablename__ = "quiz_choices"
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(
        db.Integer, db.ForeignKey("quiz_questions.id", ondelete="CASCADE"), nullable=False
    )
    choice_text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, default=False, nullable=False)
    question = db.relationship("QuizQuestion", backref=db.backref("choices", lazy="dynamic", cascade="all, delete-orphan"))


class QuizAttempt(db.Model):
    __tablename__ = "quiz_attempts"
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(
        db.Integer, db.ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    score = db.Column(db.Integer, default=0, nullable=False)
    percentage = db.Column(db.Integer, default=0, nullable=False)
    passed = db.Column(db.Boolean, default=False, nullable=False)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    quiz = db.relationship("Quiz", backref=db.backref("attempts", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("quiz_attempts", lazy="dynamic", cascade="all, delete-orphan"))


class QuizAnswer(db.Model):
    __tablename__ = "quiz_answers"
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(
        db.Integer, db.ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id = db.Column(
        db.Integer, db.ForeignKey("quiz_questions.id", ondelete="CASCADE"), nullable=False
    )
    selected_choice_id = db.Column(
        db.Integer, db.ForeignKey("quiz_choices.id", ondelete="SET NULL"), nullable=True
    )
    awarded_points = db.Column(db.Integer, default=0, nullable=False)
    attempt = db.relationship("QuizAttempt", backref=db.backref("answers", lazy="dynamic", cascade="all, delete-orphan"))
    question = db.relationship("QuizQuestion", backref=db.backref("answers", lazy="dynamic", cascade="all, delete-orphan"))
    selected_choice = db.relationship("QuizChoice", backref=db.backref("answers", lazy="dynamic"))


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
    read_at = db.Column(db.DateTime, nullable=True, index=True)
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


class MessageReaction(db.Model):
    __tablename__ = "message_reactions"
    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="uq_message_reaction_user"),
        db.CheckConstraint("reaction IN ('heart','support','understand')", name="ck_message_reaction"),
    )
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reaction = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    message = db.relationship("Message", backref=db.backref("reactions", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("message_reactions", lazy="dynamic", cascade="all, delete-orphan"))


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
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    group_key = db.Column(db.String(180), default="", nullable=False, index=True)
    dedupe_key = db.Column(db.String(180), nullable=True, unique=True)
    event_count = db.Column(db.Integer, default=1, nullable=False)
    important = db.Column(db.Boolean, default=False, nullable=False, index=True)


class NotificationPreference(db.Model):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        db.UniqueConstraint("user_id", "category", name="uq_notification_preference_user_category"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(48), nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("notification_preferences", lazy="dynamic", cascade="all, delete-orphan"))


class NotificationDeliveryKey(db.Model):
    __tablename__ = "notification_delivery_keys"
    key = db.Column(db.String(180), primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notification = db.relationship("Notification", backref=db.backref("delivery_keys", lazy="dynamic", cascade="all, delete-orphan"))


class DailyCheckIn(db.Model):
    __tablename__ = "daily_check_ins"
    __table_args__ = (
        db.UniqueConstraint("user_id", "checkin_date", name="uq_daily_checkin_user_date"),
        db.CheckConstraint(
            "mood IN ('happy','peaceful','motivated','okay','tired','worried','struggling','prefer_not_to_say')",
            name="ck_daily_checkin_mood",
        ),
        db.CheckConstraint(
            "privacy IN ('private','family','all_families','public')",
            name="ck_daily_checkin_privacy",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mood = db.Column(db.String(32), nullable=False)
    note = db.Column(db.String(500), default="", nullable=False)
    privacy = db.Column(db.String(24), default="private", nullable=False, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True)
    checkin_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("daily_checkins", lazy="dynamic", cascade="all, delete-orphan"))
    family = db.relationship("Family", backref=db.backref("daily_checkins", lazy="dynamic"))


class CheckInResponse(db.Model):
    __tablename__ = "checkin_responses"
    __table_args__ = (
        db.UniqueConstraint("checkin_id", "user_id", name="uq_checkin_response_user"),
        db.CheckConstraint(
            "reaction IN ('support','understand','keep_going','inspire')",
            name="ck_checkin_response_reaction",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    checkin_id = db.Column(db.Integer, db.ForeignKey("daily_check_ins.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reaction = db.Column(db.String(24), nullable=False)
    message = db.Column(db.String(500), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    checkin = db.relationship("DailyCheckIn", backref=db.backref("responses", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("checkin_responses", lazy="dynamic", cascade="all, delete-orphan"))


class EncouragementRequest(db.Model):
    __tablename__ = "encouragement_requests"
    __table_args__ = (
        db.CheckConstraint("visibility IN ('identity','anonymous','admins')", name="ck_encouragement_visibility"),
        db.CheckConstraint("status IN ('active','removed')", name="ck_encouragement_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(48), nullable=False)
    content = db.Column(db.Text, nullable=False)
    visibility = db.Column(db.String(16), default="identity", nullable=False)
    needs_crisis_guidance = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(16), default="active", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    family = db.relationship("Family", backref=db.backref("encouragement_requests", lazy="dynamic", cascade="all, delete-orphan"))
    requester = db.relationship("User", backref=db.backref("encouragement_requests", lazy="dynamic", cascade="all, delete-orphan"))


class EncouragementResponse(db.Model):
    __tablename__ = "encouragement_responses"
    __table_args__ = (
        db.UniqueConstraint("request_id", "user_id", name="uq_encouragement_response_user"),
        db.CheckConstraint("reaction IN ('support','understand','keep_going','inspire')", name="ck_encouragement_response_reaction"),
    )
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("encouragement_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reaction = db.Column(db.String(24), nullable=False)
    comment = db.Column(db.String(1000), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    request = db.relationship("EncouragementRequest", backref=db.backref("responses", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("encouragement_responses", lazy="dynamic", cascade="all, delete-orphan"))


class Appreciation(db.Model):
    __tablename__ = "appreciations"
    __table_args__ = (
        db.UniqueConstraint("response_id", "sender_id", name="uq_appreciation_response_sender"),
        db.CheckConstraint(
            "message_key IN ('thank_you','words_helped','appreciate_advice')",
            name="ck_appreciation_message_key",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    response_id = db.Column(db.Integer, db.ForeignKey("encouragement_responses.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    message_key = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    response = db.relationship("EncouragementResponse", backref=db.backref("appreciations", lazy="dynamic", cascade="all, delete-orphan"))
    sender = db.relationship("User", foreign_keys=[sender_id], backref=db.backref("appreciations_sent", lazy="dynamic"))
    recipient = db.relationship("User", foreign_keys=[recipient_id], backref=db.backref("appreciations_received", lazy="dynamic"))


class EncouragementRequestReport(db.Model):
    __tablename__ = "encouragement_request_reports"
    __table_args__ = (
        db.UniqueConstraint("request_id", "reporter_id", name="uq_encouragement_report_user"),
        db.CheckConstraint("status IN ('open','dismissed','removed')", name="ck_encouragement_report_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("encouragement_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(16), default="open", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    request = db.relationship("EncouragementRequest", backref=db.backref("reports", lazy="dynamic", cascade="all, delete-orphan"))
    reporter = db.relationship("User", backref=db.backref("encouragement_reports", lazy="dynamic", cascade="all, delete-orphan"))


class UserStreak(db.Model):
    __tablename__ = "user_streaks"
    __table_args__ = (db.UniqueConstraint("user_id", "streak_type", name="uq_user_streak_type"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    streak_type = db.Column(db.String(32), nullable=False)
    current_count = db.Column(db.Integer, default=0, nullable=False)
    best_count = db.Column(db.Integer, default=0, nullable=False)
    previous_count = db.Column(db.Integer, default=0, nullable=False)
    last_activity_date = db.Column(db.Date, nullable=True)
    grace_days_available = db.Column(db.Integer, default=1, nullable=False)
    last_warning_date = db.Column(db.Date, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("streaks", lazy="dynamic", cascade="all, delete-orphan"))


class StreakActivity(db.Model):
    __tablename__ = "streak_activities"
    __table_args__ = (
        db.UniqueConstraint("user_id", "streak_type", "activity_date", name="uq_streak_activity_day"),
        db.UniqueConstraint("unique_activity_key", name="uq_streak_activity_key"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    streak_type = db.Column(db.String(32), nullable=False)
    activity_date = db.Column(db.Date, nullable=False, index=True)
    source_type = db.Column(db.String(48), nullable=False)
    source_id = db.Column(db.Integer, nullable=True)
    unique_activity_key = db.Column(db.String(160), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("streak_activities", lazy="dynamic", cascade="all, delete-orphan"))


class StreakMilestone(db.Model):
    __tablename__ = "streak_milestones"
    __table_args__ = (db.UniqueConstraint("streak_id", "milestone", name="uq_streak_milestone_claim"),)
    id = db.Column(db.Integer, primary_key=True)
    streak_id = db.Column(db.Integer, db.ForeignKey("user_streaks.id", ondelete="CASCADE"), nullable=False, index=True)
    milestone = db.Column(db.Integer, nullable=False)
    badge_name = db.Column(db.String(80), nullable=False)
    bonus_points = db.Column(db.Integer, default=0, nullable=False)
    awarded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    streak = db.relationship("UserStreak", backref=db.backref("milestones", lazy="dynamic", cascade="all, delete-orphan"))


class Goal(db.Model):
    __tablename__ = "goals"
    __table_args__ = (
        db.CheckConstraint("scope IN ('personal','family')", name="ck_goal_scope"),
        db.CheckConstraint("visibility IN ('private','family','public')", name="ck_goal_visibility"),
        db.CheckConstraint("measurement_type IN ('number','percentage','binary')", name="ck_goal_measurement"),
        db.CheckConstraint("status IN ('active','completed','archived')", name="ck_goal_status"),
        db.CheckConstraint("target_amount > 0", name="ck_goal_target_positive"),
    )
    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=False, index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id", ondelete="CASCADE"), nullable=True, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    category = db.Column(db.String(48), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    target_date = db.Column(db.Date, nullable=True)
    measurement_type = db.Column(db.String(20), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_progress = db.Column(db.Float, default=0, nullable=False)
    visibility = db.Column(db.String(16), default="private", nullable=False, index=True)
    status = db.Column(db.String(16), default="active", nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    owner = db.relationship("User", backref=db.backref("owned_goals", lazy="dynamic"))
    family = db.relationship("Family", backref=db.backref("expanded_goals", lazy="dynamic", cascade="all, delete-orphan"))
    achievement_post = db.relationship("Post", backref="goal", uselist=False, foreign_keys="Post.goal_id")


class GoalParticipant(db.Model):
    __tablename__ = "goal_participants"
    __table_args__ = (db.UniqueConstraint("goal_id", "user_id", name="uq_goal_participant"),)
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    goal = db.relationship("Goal", backref=db.backref("participants", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("goal_participations", lazy="dynamic", cascade="all, delete-orphan"))


class GoalMilestone(db.Model):
    __tablename__ = "goal_milestones"
    __table_args__ = (db.UniqueConstraint("goal_id", "target_amount", name="uq_goal_milestone_target"),)
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    goal = db.relationship("Goal", backref=db.backref("milestones", lazy="dynamic", cascade="all, delete-orphan"))


class GoalProgress(db.Model):
    __tablename__ = "goal_progress_entries"
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    amount = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(500), default="", nullable=False)
    evidence_url = db.Column(db.String(255), default="", nullable=False)
    evidence_type = db.Column(db.String(24), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    goal = db.relationship("Goal", backref=db.backref("progress_entries", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("goal_progress_entries", lazy="dynamic"))


class GoalActivity(db.Model):
    __tablename__ = "goal_activities"
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type = db.Column(db.String(32), nullable=False, index=True)
    message = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    goal = db.relationship("Goal", backref=db.backref("activities", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User")


class GoalEncouragement(db.Model):
    __tablename__ = "goal_encouragements"
    __table_args__ = (db.UniqueConstraint("goal_id", "user_id", name="uq_goal_encouragement_user"),)
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reaction = db.Column(db.String(24), nullable=False)
    message = db.Column(db.String(500), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    goal = db.relationship("Goal", backref=db.backref("encouragements", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User")


class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        db.UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    user = db.relationship(
        "User",
        backref=db.backref("push_subscriptions", lazy="dynamic", cascade="all, delete-orphan"),
    )


class ReturnCheckIn(db.Model):
    __tablename__ = "return_checkins"
    __table_args__ = (
        db.CheckConstraint("sender_id <> recipient_id", name="ck_return_checkin_different_users"),
    )
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    message = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    thanked_at = db.Column(db.DateTime, nullable=True)
    sender = db.relationship("User", foreign_keys=[sender_id], backref=db.backref("return_checkins_sent", lazy="dynamic", cascade="all, delete-orphan"))
    recipient = db.relationship("User", foreign_keys=[recipient_id], backref=db.backref("return_checkins_received", lazy="dynamic", cascade="all, delete-orphan"))


class ReturnSuggestionDismissal(db.Model):
    __tablename__ = "return_suggestion_dismissals"
    __table_args__ = (
        db.UniqueConstraint("viewer_id", "inactive_user_id", name="uq_return_suggestion_dismissal"),
    )
    id = db.Column(db.Integer, primary_key=True)
    viewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    inactive_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    dismissed_until = db.Column(db.DateTime, nullable=False, index=True)
    viewer = db.relationship("User", foreign_keys=[viewer_id])
    inactive_user = db.relationship("User", foreign_keys=[inactive_user_id])


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
    code_hash = db.Column(db.String(256), default="")
    used = db.Column(db.Boolean, default=False, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    last_sent_at = db.Column(db.DateTime, nullable=True)
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


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_role = db.Column(db.String(20), default="")
    action_type = db.Column(db.String(80), nullable=False)
    target_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_family_id = db.Column(
        db.Integer, db.ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    target_content_id = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.Text, default="")
    metadata_text = db.Column(db.Text, default="")
    ip_address = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    actor = db.relationship("User", foreign_keys=[actor_user_id], backref="audit_actions")
    target_user = db.relationship("User", foreign_keys=[target_user_id], backref="audit_events")
    target_family = db.relationship("Family", foreign_keys=[target_family_id], backref="audit_events")


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
