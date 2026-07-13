import os
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urljoin

import click
from dotenv import load_dotenv
from flask import Flask, Response, request, url_for
from flask_login import current_user
from sqlalchemy import text
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db, login_manager, socketio
from feature_flags import get_feature_flags
from security import csrf_token, init_csrf

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

database_url = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/rise_together"
)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "rise-together-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["PREFERRED_URL_SCHEME"] = "https"
is_production = (
    os.getenv("FLASK_ENV", "").lower() == "production"
    or os.getenv("RENDER", "").lower() == "true"
    or bool(os.getenv("RENDER_EXTERNAL_HOSTNAME"))
)
app.config["SESSION_COOKIE_SECURE"] = is_production
app.config["REMEMBER_COOKIE_SECURE"] = is_production
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["REMEMBER_COOKIE_SAMESITE"] = os.getenv("REMEMBER_COOKIE_SAMESITE", "Lax")
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(
    days=int(os.getenv("REMEMBER_COOKIE_DAYS", "30"))
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    days=int(os.getenv("SESSION_COOKIE_DAYS", "7"))
)
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
}
app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
app.config["IMAGE_UPLOAD_LIMIT"] = int(os.getenv("IMAGE_UPLOAD_LIMIT_MB", "5")) * 1024 * 1024
app.config["VIDEO_UPLOAD_LIMIT"] = int(os.getenv("VIDEO_UPLOAD_LIMIT_MB", "25")) * 1024 * 1024
app.config["FILE_UPLOAD_LIMIT"] = int(os.getenv("FILE_UPLOAD_LIMIT_MB", "10")) * 1024 * 1024
try:
    default_family_member_limit = int(os.getenv("DEFAULT_FAMILY_MEMBER_LIMIT", "50"))
except ValueError:
    default_family_member_limit = 50
app.config["DEFAULT_FAMILY_MEMBER_LIMIT"] = max(2, default_family_member_limit)
app.config["MAX_CONTENT_LENGTH"] = max(
    app.config["IMAGE_UPLOAD_LIMIT"],
    app.config["VIDEO_UPLOAD_LIMIT"],
    app.config["FILE_UPLOAD_LIMIT"],
) + 1024 * 1024
app.config["REALTIME_MEDIA_ENABLED"] = (
    os.getenv("REALTIME_MEDIA_ENABLED", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
app.config["VAPID_PUBLIC_KEY"] = os.getenv("VAPID_PUBLIC_KEY", "").strip()
app.config["VAPID_PRIVATE_KEY"] = os.getenv("VAPID_PRIVATE_KEY", "").strip()
app.config["VAPID_SUBJECT"] = os.getenv("VAPID_SUBJECT", "mailto:admin@risetogether.local").strip()
app.config["CSRF_ENABLED"] = os.getenv("CSRF_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}


os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)
init_csrf(app)
login_manager.init_app(app)
login_manager.login_view = "auth.login"
login_manager.refresh_view = "auth.reauthenticate"
login_manager.login_message_category = "info"
login_manager.needs_refresh_message = "Please confirm your password to continue."
login_manager.needs_refresh_message_category = "warning"
socketio.init_app(
    app,
    async_mode="threading",
    ping_interval=25,
    ping_timeout=60,
    logger=os.getenv("SOCKETIO_LOGGER", "").lower() == "true",
    engineio_logger=os.getenv("ENGINEIO_LOGGER", "").lower() == "true",
)


@app.route("/service-worker.js")
def service_worker():
    response = app.send_static_file("service-worker.js")
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


def public_base_url():
    base_url = (
        os.getenv("PUBLIC_BASE_URL", "").strip()
        or os.getenv("SITE_URL", "").strip()
        or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    )
    if not base_url and os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip():
        base_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME').strip()}"
    return base_url.rstrip("/")


@app.context_processor
def inject_social_meta():
    base_url = public_base_url()
    current_path = request.full_path.rstrip("?") if request else "/"
    page_url = urljoin(base_url + "/", current_path.lstrip("/")) if base_url else request.url
    image_path = url_for("static", filename="images/social-preview.png")
    image_url = urljoin(base_url + "/", image_path.lstrip("/")) if base_url else url_for(
        "static",
        filename="images/social-preview.png",
        _external=True,
        _scheme="https",
    )
    return {
        "public_page_url": page_url,
        "social_preview_image_url": image_url,
    }


def ensure_schema_compatibility():
    db.create_all()
    from models import (
        ChallengeCompletion,
        ChallengeParticipant,
        FamilyChallenge,
        MediaAsset,
        MessageDeletion,
        PushSubscription,
        AuditLog,
        FamilyMember,
        FamilyWeeklyReport,
        FamilyMemberRestriction,
        FamilyModerationLog,
        FamilyGalleryItem,
        FamilyUpgradePurchase,
        FamilyCampaignContribution,
        FamilyContributionCampaign,
        FamilyPoll,
        FamilyPollOption,
        FamilyPollVote,
        Quiz,
        QuizAnswer,
        QuizAttempt,
        QuizChoice,
        QuizQuestion,
        PointTransaction,
        PointSecurityEvent,
        RiseBadgeAssignment,
    )
    from helpers import get_media_type, mimetype_for_filename

    MediaAsset.__table__.create(db.engine, checkfirst=True)
    MessageDeletion.__table__.create(db.engine, checkfirst=True)
    PushSubscription.__table__.create(db.engine, checkfirst=True)
    AuditLog.__table__.create(db.engine, checkfirst=True)
    FamilyMemberRestriction.__table__.create(db.engine, checkfirst=True)
    FamilyWeeklyReport.__table__.create(db.engine, checkfirst=True)
    FamilyModerationLog.__table__.create(db.engine, checkfirst=True)
    FamilyUpgradePurchase.__table__.create(db.engine, checkfirst=True)
    FamilyGalleryItem.__table__.create(db.engine, checkfirst=True)
    FamilyContributionCampaign.__table__.create(db.engine, checkfirst=True)
    FamilyCampaignContribution.__table__.create(db.engine, checkfirst=True)
    FamilyPoll.__table__.create(db.engine, checkfirst=True)
    FamilyPollOption.__table__.create(db.engine, checkfirst=True)
    FamilyPollVote.__table__.create(db.engine, checkfirst=True)
    FamilyChallenge.__table__.create(db.engine, checkfirst=True)
    ChallengeCompletion.__table__.create(db.engine, checkfirst=True)
    ChallengeParticipant.__table__.create(db.engine, checkfirst=True)
    Quiz.__table__.create(db.engine, checkfirst=True)
    QuizQuestion.__table__.create(db.engine, checkfirst=True)
    QuizChoice.__table__.create(db.engine, checkfirst=True)
    QuizAttempt.__table__.create(db.engine, checkfirst=True)
    QuizAnswer.__table__.create(db.engine, checkfirst=True)
    PointTransaction.__table__.create(db.engine, checkfirst=True)
    PointSecurityEvent.__table__.create(db.engine, checkfirst=True)
    RiseBadgeAssignment.__table__.create(db.engine, checkfirst=True)
    default_family_member_limit = int(app.config["DEFAULT_FAMILY_MEMBER_LIMIT"])
    platform_owner_username = os.getenv("PLATFORM_SUPER_ADMIN_USERNAME", "Kakwikiri").strip()
    platform_owner_literal = platform_owner_username.replace("'", "''")
    updates = [
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS audience VARCHAR(20) NOT NULL DEFAULT 'public'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP",
        "ALTER TABLE comment_reactions ADD COLUMN IF NOT EXISTS type VARCHAR(32) NOT NULL DEFAULT 'support'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS original_post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_url VARCHAR(255) DEFAULT ''",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_type VARCHAR(32) DEFAULT 'text'",
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS action_url VARCHAR(255) DEFAULT ''",
        "ALTER TABLE password_reset_tokens ADD COLUMN IF NOT EXISTS code_hash VARCHAR(256) DEFAULT ''",
        "ALTER TABLE password_reset_tokens ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE password_reset_tokens ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMP",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_password_reset_tokens_user_used') THEN CREATE INDEX ix_password_reset_tokens_user_used ON password_reset_tokens (user_id, used); END IF; END $$",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_role VARCHAR(20) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_until TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS warning_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_phrase_hash VARCHAR(256) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS country VARCHAR(80) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_hidden_from_directory BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS notification_previews_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS auto_share_completed_challenges BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'Africa/Kampala'",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_point_balance BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_streaks BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_achievements BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_family_memberships BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_checkins BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_goal_progress BOOLEAN NOT NULL DEFAULT FALSE",
        "INSERT INTO rise_badge_assignments (badge_type, user_id, status, verification_note, assigned_by_id, assigned_at) SELECT 'verified_person', u.id, 'active', 'Legacy verification migrated into the audited RiseTogether badge system.', (SELECT id FROM users WHERE admin_role = 'super_admin' ORDER BY created_at ASC LIMIT 1), CURRENT_TIMESTAMP FROM users u WHERE u.is_verified = TRUE ON CONFLICT (badge_type, user_id) DO NOTHING",
        "INSERT INTO rise_badge_assignments (badge_type, user_id, status, verification_note, assigned_by_id, assigned_at) SELECT 'platform_moderator', u.id, 'active', 'Existing website moderation role migrated into the protected badge system.', (SELECT id FROM users WHERE admin_role = 'super_admin' ORDER BY created_at ASC LIMIT 1), CURRENT_TIMESTAMP FROM users u WHERE u.admin_role IN ('moderator','admin') ON CONFLICT (badge_type, user_id) DO NOTHING",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS post_type VARCHAR(32) NOT NULL DEFAULT 'standard'",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS achievement_type VARCHAR(48) DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS challenge_completion_id INTEGER REFERENCES challenge_completions(id) ON DELETE CASCADE",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS encouraging_message VARCHAR(240) DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS reward_tier VARCHAR(32) NOT NULL DEFAULT 'easy'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS completion_frequency VARCHAR(24) NOT NULL DEFAULT 'one_time'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS custom_frequency_days INTEGER",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS evidence_requirement VARCHAR(24) NOT NULL DEFAULT 'none'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS participant_scope VARCHAR(32) NOT NULL DEFAULT 'all_members'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS max_participants INTEGER",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS visibility VARCHAR(24) NOT NULL DEFAULT 'family'",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS requires_admin_approval BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS allow_achievement_sharing BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE family_challenges ADD COLUMN IF NOT EXISTS mandatory_all_members BOOLEAN NOT NULL DEFAULT FALSE",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM site_settings WHERE key = 'migration_stage12_participants') THEN INSERT INTO challenge_participants (challenge_id, user_id, joined_at) SELECT challenge_id, user_id, MIN(completed_at) FROM challenge_completions GROUP BY challenge_id, user_id ON CONFLICT (challenge_id, user_id) DO NOTHING; INSERT INTO site_settings (key, value, updated_at) VALUES ('migration_stage12_participants', 'complete', CURRENT_TIMESTAMP); END IF; END $$",
        "UPDATE family_challenges SET completion_frequency = 'daily' WHERE challenge_type IN ('daily_check_in', 'habit') AND completion_frequency = 'one_time'",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_family_challenges_max_participants') THEN ALTER TABLE family_challenges ADD CONSTRAINT ck_family_challenges_max_participants CHECK (max_participants IS NULL OR max_participants >= 1); END IF; END $$",
        "ALTER TABLE challenge_completions ADD COLUMN IF NOT EXISTS period_key VARCHAR(32) NOT NULL DEFAULT 'once'",
        "ALTER TABLE challenge_completions ADD COLUMN IF NOT EXISTS points_awarded INTEGER NOT NULL DEFAULT 0",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM site_settings WHERE key = 'migration_stage10_rewards') THEN UPDATE challenge_completions SET points_awarded = family_challenges.points FROM family_challenges WHERE challenge_completions.challenge_id = family_challenges.id; UPDATE challenge_completions SET period_key = TO_CHAR(completed_at, 'YYYY-MM-DD') FROM family_challenges WHERE challenge_completions.challenge_id = family_challenges.id AND family_challenges.challenge_type IN ('daily_check_in', 'habit') AND challenge_completions.period_key = 'once'; UPDATE family_challenges SET reward_tier = CASE WHEN points <= 5 THEN 'small' WHEN points <= 10 THEN 'easy' WHEN points <= 25 THEN 'medium' WHEN points <= 50 THEN 'hard' ELSE 'major' END; UPDATE family_challenges SET reward_tier = 'small' WHERE challenge_type = 'daily_check_in'; UPDATE family_challenges SET reward_tier = 'easy' WHERE challenge_type = 'task'; UPDATE family_challenges SET reward_tier = 'easy' WHERE challenge_type = 'habit' AND reward_tier NOT IN ('small', 'easy'); UPDATE family_challenges SET reward_tier = 'medium' WHERE challenge_type IN ('learning_lesson', 'quiz') AND reward_tier NOT IN ('small', 'easy', 'medium'); UPDATE family_challenges SET points = CASE reward_tier WHEN 'small' THEN 5 WHEN 'easy' THEN 10 WHEN 'medium' THEN 25 WHEN 'hard' THEN 50 WHEN 'major' THEN 100 ELSE 10 END; INSERT INTO site_settings (key, value, updated_at) VALUES ('migration_stage10_rewards', 'complete', CURRENT_TIMESTAMP); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM site_settings WHERE key = 'migration_stage14_point_ledger') THEN INSERT INTO point_transactions (user_id, family_id, amount, reason, source_type, source_id, unique_reward_key, reversed, created_at, awarded_by_id) SELECT cc.user_id, NULL, cc.points_awarded, LEFT('Completed ' || fc.title, 240), 'challenge_completion', cc.id, 'challenge_completion:' || cc.id || ':' || 'personal', FALSE, cc.completed_at, NULL FROM challenge_completions cc JOIN family_challenges fc ON fc.id = cc.challenge_id WHERE cc.verification_status = 'completed' AND cc.points_awarded > 0 ON CONFLICT (unique_reward_key) DO NOTHING; INSERT INTO point_transactions (user_id, family_id, amount, reason, source_type, source_id, unique_reward_key, reversed, created_at, awarded_by_id) SELECT NULL, fc.family_id, cc.points_awarded, LEFT('Completed ' || fc.title, 240), 'challenge_completion', cc.id, 'challenge_completion:' || cc.id || ':' || 'family', FALSE, cc.completed_at, NULL FROM challenge_completions cc JOIN family_challenges fc ON fc.id = cc.challenge_id WHERE cc.verification_status = 'completed' AND cc.points_awarded > 0 ON CONFLICT (unique_reward_key) DO NOTHING; INSERT INTO site_settings (key, value, updated_at) VALUES ('migration_stage14_point_ledger', 'complete', CURRENT_TIMESTAMP); END IF; END $$",
        "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS reversed_at TIMESTAMP",
        "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS reversed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS reversal_reason VARCHAR(500) NOT NULL DEFAULT ''",
        "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS suspicious BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS suspicious_reason VARCHAR(500) NOT NULL DEFAULT ''",
        "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS transaction_kind VARCHAR(16) NOT NULL DEFAULT 'award'",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_point_transaction_kind') THEN ALTER TABLE point_transactions ADD CONSTRAINT ck_point_transaction_kind CHECK (transaction_kind IN ('award', 'spend')); END IF; END $$",
        "CREATE INDEX IF NOT EXISTS ix_point_transactions_suspicious ON point_transactions (suspicious)",
        "INSERT INTO site_settings (key, value, updated_at) VALUES ('family_level_2_xp', '100', CURRENT_TIMESTAMP), ('family_level_3_xp', '300', CURRENT_TIMESTAMP), ('family_level_4_xp', '750', CURRENT_TIMESTAMP), ('family_level_5_xp', '1500', CURRENT_TIMESTAMP), ('family_level_6_xp', '3000', CURRENT_TIMESTAMP), ('family_level_7_xp', '5000', CURRENT_TIMESTAMP), ('family_level_rising_interval', '2500', CURRENT_TIMESTAMP) ON CONFLICT (key) DO NOTHING",
        "ALTER TABLE challenge_completions DROP CONSTRAINT IF EXISTS uq_challenge_completion_user",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_challenge_completion_period') THEN CREATE UNIQUE INDEX uq_challenge_completion_period ON challenge_completions (challenge_id, user_id, period_key); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_posts_challenge_completion') THEN CREATE UNIQUE INDEX uq_posts_challenge_completion ON posts (challenge_completion_id) WHERE challenge_completion_id IS NOT NULL; END IF; END $$",
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'open'",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS privacy VARCHAR(20) NOT NULL DEFAULT 'public'",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS category VARCHAR(40) NOT NULL DEFAULT 'friendship_and_support'",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS custom_category VARCHAR(80) DEFAULT ''",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS goal_title VARCHAR(160) DEFAULT ''",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS goal_description TEXT DEFAULT ''",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS start_date TIMESTAMP",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS target_date TIMESTAMP",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS member_limit INTEGER",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS profile_image VARCHAR(255) DEFAULT ''",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS profile_image_public_id VARCHAR(255) DEFAULT ''",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS banner_image VARCHAR(255) NOT NULL DEFAULT ''",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS theme VARCHAR(32) NOT NULL DEFAULT 'classic'",
        f"UPDATE families SET member_limit = {default_family_member_limit} WHERE member_limit IS NULL",
        f"ALTER TABLE families ALTER COLUMN member_limit SET DEFAULT {default_family_member_limit}",
        "ALTER TABLE families ALTER COLUMN member_limit SET NOT NULL",
        "ALTER TABLE families ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES messages(id) ON DELETE SET NULL",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS view_once BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS pinned_until TIMESTAMP",
        "ALTER TABLE post_shares ADD COLUMN IF NOT EXISTS recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
        "DELETE FROM reactions r USING reactions newer WHERE r.post_id = newer.post_id AND r.user_id = newer.user_id AND r.id < newer.id",
        "ALTER TABLE reactions DROP CONSTRAINT IF EXISTS uq_reaction_user_type",
        "DROP INDEX IF EXISTS uq_reaction_user_type",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_reaction_post_user') THEN CREATE UNIQUE INDEX uq_reaction_post_user ON reactions (post_id, user_id); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_public_post_reshare') THEN CREATE UNIQUE INDEX uq_public_post_reshare ON posts (original_post_id, user_id) WHERE original_post_id IS NOT NULL AND audience = 'public'; END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_family_post_reshare') THEN CREATE UNIQUE INDEX uq_family_post_reshare ON posts (original_post_id, user_id, family_id) WHERE original_post_id IS NOT NULL AND family_id IS NOT NULL; END IF; END $$",
        "UPDATE family_members SET role = 'owner' FROM families WHERE family_members.family_id = families.id AND family_members.user_id = families.owner_id AND family_members.role != 'owner'",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_families_member_limit_min') THEN ALTER TABLE families ADD CONSTRAINT ck_families_member_limit_min CHECK (member_limit >= 2); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_family_member_user_idx') AND NOT EXISTS (SELECT 1 FROM (SELECT family_id, user_id FROM family_members GROUP BY family_id, user_id HAVING COUNT(*) > 1) duplicates) THEN CREATE UNIQUE INDEX uq_family_member_user_idx ON family_members (family_id, user_id); END IF; END $$",
        "ALTER TABLE family_polls ADD COLUMN IF NOT EXISTS allow_vote_changes BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE family_polls ADD COLUMN IF NOT EXISTS results_visibility VARCHAR(24) NOT NULL DEFAULT 'after_vote'",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_family_poll_results_visibility') THEN ALTER TABLE family_polls ADD CONSTRAINT ck_family_poll_results_visibility CHECK (results_visibility IN ('always', 'after_vote', 'after_close')); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_family_polls_family_status') THEN CREATE INDEX ix_family_polls_family_status ON family_polls (family_id, status); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_family_poll_votes_poll_user') THEN CREATE INDEX ix_family_poll_votes_poll_user ON family_poll_votes (poll_id, user_id); END IF; END $$",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS pass_mark INTEGER NOT NULL DEFAULT 60",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS attempt_limit INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS explanation TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS percentage INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS passed BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS points_awarded INTEGER NOT NULL DEFAULT 0",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_quiz_pass_mark') THEN ALTER TABLE quizzes ADD CONSTRAINT ck_quiz_pass_mark CHECK (pass_mark BETWEEN 1 AND 100); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_quiz_attempt_limit') THEN ALTER TABLE quizzes ADD CONSTRAINT ck_quiz_attempt_limit CHECK (attempt_limit BETWEEN 1 AND 10); END IF; END $$",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_quiz_attempt_percentage') THEN ALTER TABLE quiz_attempts ADD CONSTRAINT ck_quiz_attempt_percentage CHECK (percentage BETWEEN 0 AND 100); END IF; END $$",
        "CREATE INDEX IF NOT EXISTS ix_quiz_attempt_user_quiz_submitted ON quiz_attempts (user_id, quiz_id, submitted_at)",
        "UPDATE users SET admin_role = 'admin' WHERE is_admin = TRUE AND COALESCE(admin_role, '') = ''",
        f"UPDATE users SET admin_role = 'admin' WHERE admin_role = 'super_admin' AND LOWER(username) != LOWER('{platform_owner_literal}')",
        f"UPDATE users SET is_admin = TRUE, admin_role = 'super_admin', is_banned = FALSE, ban_until = NULL WHERE LOWER(username) = LOWER('{platform_owner_literal}')",
        f"WITH first_admin AS (SELECT id FROM users WHERE is_admin = TRUE ORDER BY created_at ASC, id ASC LIMIT 1) UPDATE users SET admin_role = 'super_admin' WHERE id IN (SELECT id FROM first_admin) AND NOT EXISTS (SELECT 1 FROM users WHERE admin_role = 'super_admin') AND NOT EXISTS (SELECT 1 FROM users WHERE LOWER(username) = LOWER('{platform_owner_literal}'))",
        "UPDATE users SET is_admin = TRUE WHERE admin_role IN ('super_admin', 'admin', 'moderator')",
        "UPDATE users SET is_admin = FALSE WHERE COALESCE(admin_role, '') = ''",
    ]
    for statement in updates:
        db.session.execute(text(statement))
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    if upload_folder.exists():
        for path in upload_folder.iterdir():
            if not path.is_file():
                continue
            filename = path.name
            if MediaAsset.query.filter_by(filename=filename).first():
                continue
            data = path.read_bytes()
            db.session.add(
                MediaAsset(
                    filename=filename,
                    content_type=mimetype_for_filename(filename),
                    media_type=get_media_type(filename),
                    data=data,
                    size=len(data),
                )
            )
    db.session.commit()

with app.app_context():
    from models import User
    from routes.api import api_bp
    from routes.auth import auth_bp
    from routes.chat import chat_bp
    from routes.family import family_bp
    from routes.goals import goals_bp
    from routes.main import main_bp
    from routes.moderation import mod_bp

    ensure_schema_compatibility()
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(family_bp)
    app.register_blueprint(goals_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(mod_bp)
    app.register_blueprint(api_bp)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def inject_navigation_counts():
    from badges import family_badges, user_badges
    from helpers import family_avatar_url, get_media_type, is_hevc_upload, user_avatar_url
    from models import Message, Notification

    unread_notifications = 0
    unread_messages = 0
    if current_user.is_authenticated:
        unread_notifications = Notification.query.filter_by(
            user_id=current_user.id, seen=False
        ).count()
        unread_messages = Message.query.filter_by(
            recipient_id=current_user.id, delivered=False
        ).count()
    feature_flags = get_feature_flags()
    return {
        "unread_notifications": unread_notifications,
        "unread_messages": unread_messages,
        "is_hevc_upload": is_hevc_upload,
        "get_media_type": get_media_type,
        "chat_day_label": chat_day_label,
        "realtime_media_enabled": app.config["REALTIME_MEDIA_ENABLED"],
        "user_avatar_url": user_avatar_url,
        "family_avatar_url": family_avatar_url,
        "feature_flags": feature_flags,
        "feature_enabled": lambda name: feature_flags.get(name, False),
        "rise_user_badges": user_badges,
        "rise_family_badges": family_badges,
    }


def chat_day_label(value):
    if not value:
        return ""
    message_day = value.date()
    today = datetime.utcnow().date()
    if message_day == today:
        return "Today"
    if message_day == today - timedelta(days=1):
        return "Yesterday"
    if message_day > today - timedelta(days=7):
        return value.strftime("%A")
    return value.strftime("%b %d, %Y")


def find_user_by_identifier(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    return User.query.filter(
        (User.email == identifier.lower()) | (User.username.ilike(identifier))
    ).first()


def validate_cli_password(password):
    if len(password or "") < 8:
        raise click.ClickException("Password must be at least 8 characters.")


def admin_setup_token_is_valid(token):
    expected = os.getenv("ADMIN_SETUP_TOKEN", "").strip()
    return bool(expected) and token and token == expected


def admin_setup_is_enabled():
    if not os.getenv("ADMIN_SETUP_TOKEN", "").strip():
        return False
    if os.getenv("ADMIN_SETUP_ALLOW_EXISTING", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return User.query.filter(
        User.is_admin == True,
        User.admin_role == "super_admin",
        User.is_banned == False,
    ).count() == 0


@app.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(error):
    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return f"Upload is too large. Please choose a file under {limit_mb} MB.", 413


@app.errorhandler(500)
def handle_unexpected_error(error):
    app.logger.exception(
        "unhandled_application_error path=%s method=%s",
        request.path,
        request.method,
        exc_info=getattr(error, "original_exception", error),
    )
    return "RiseTogether hit an unexpected problem. Please try again shortly.", 500


def admin_setup_form(token="", message="", status=200):
    safe_message = escape(message) if message else ""
    safe_token = escape(token or "")
    html = f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>RiseTogether Admin Setup</title>
  </head>
  <body>
    <h1>RiseTogether Admin Setup</h1>
    {'<p><strong>' + safe_message + '</strong></p>' if safe_message else ''}
    <form method="post">
      <input type="hidden" name="csrf_token" value="{csrf_token()}" />
      <input type="hidden" name="token" value="{safe_token}" />
      <label>Action
        <select name="action">
          <option value="create">Create admin</option>
          <option value="promote">Promote existing user</option>
          <option value="reset">Reset admin password</option>
        </select>
      </label>
      <p><label>Username <input name="username" autocomplete="username" /></label></p>
      <p><label>Email <input name="email" type="email" autocomplete="email" /></label></p>
      <p><label>Country <input name="country" value="Other" /></label></p>
      <p><label>Password <input name="password" type="password" autocomplete="new-password" /></label></p>
      <p><label>Confirm password <input name="confirm_password" type="password" autocomplete="new-password" /></label></p>
      <button type="submit">Apply</button>
    </form>
  </body>
</html>
"""
    return Response(html, status=status, mimetype="text/html")


@app.route("/setup/admin", methods=["GET", "POST"])
def admin_setup_web():
    token = request.values.get("token", "").strip()
    if not admin_setup_is_enabled():
        return Response("Admin setup is disabled.", status=404, mimetype="text/plain")
    if not admin_setup_token_is_valid(token):
        return admin_setup_form(token="", message="Invalid or missing setup token.", status=403)
    if request.method == "GET":
        return admin_setup_form(token=token)

    from models import Profile

    action = request.form.get("action", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    country = request.form.get("country", "Other").strip() or "Other"
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    try:
        if action == "create":
            if not username or not email:
                raise ValueError("Username and email are required.")
            if password != confirm_password:
                raise ValueError("Passwords do not match.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")
            duplicate = User.query.filter(
                (User.username == username) | (User.email == email)
            ).first()
            if duplicate:
                raise ValueError("A user with that username or email already exists.")
            user = User(username=username, email=email, country=country)
            user.set_password(password)
            user.is_admin = True
            user.admin_role = "super_admin"
            user.is_banned = False
            user.ban_until = None
            user.is_verified = True
            db.session.add(user)
            db.session.flush()
            db.session.add(Profile(user_id=user.id, display_name=username))
            db.session.commit()
            return admin_setup_form(token=token, message=f"Admin created for {username}.")

        identifier = email or username
        user = find_user_by_identifier(identifier)
        if not user:
            raise ValueError("No user found with that username or email.")
        if action == "promote":
            user.is_admin = True
            user.admin_role = user.admin_role or (
                "super_admin"
                if User.query.filter_by(admin_role="super_admin").count() == 0
                else "admin"
            )
            user.is_banned = False
            user.ban_until = None
            db.session.commit()
            return admin_setup_form(token=token, message=f"Promoted {user.username} to admin.")
        if action == "reset":
            if not user.is_admin:
                raise ValueError("That user is not currently an admin.")
            if password != confirm_password:
                raise ValueError("Passwords do not match.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")
            user.set_password(password)
            user.is_banned = False
            user.ban_until = None
            db.session.commit()
            return admin_setup_form(token=token, message=f"Password reset for {user.username}.")
        raise ValueError("Choose a valid action.")
    except Exception as exc:
        db.session.rollback()
        return admin_setup_form(token=token, message=str(exc), status=400)


@app.cli.command("create-admin")
def create_admin_command():
    """Create a production admin in the currently configured database."""
    from models import Profile

    username = click.prompt("Username").strip()
    email = click.prompt("Email").strip().lower()
    country = click.prompt("Country", default="Other").strip() or "Other"
    password = click.prompt(
        "Password",
        hide_input=True,
        confirmation_prompt=True,
    )
    validate_cli_password(password)

    if not username:
        raise click.ClickException("Username is required.")
    if not email:
        raise click.ClickException("Email is required.")

    try:
        duplicate = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        if duplicate:
            raise click.ClickException("A user with that username or email already exists.")

        user = User(username=username, email=email, country=country)
        user.set_password(password)
        user.is_admin = True
        user.admin_role = "super_admin" if User.query.filter_by(is_admin=True).count() == 0 else "admin"
        user.is_banned = False
        user.ban_until = None
        user.is_verified = True
        db.session.add(user)
        db.session.flush()
        db.session.add(Profile(user_id=user.id, display_name=username))
        db.session.commit()
    except click.ClickException:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise click.ClickException(f"Admin could not be created: {exc}") from exc

    click.echo(f"Admin created: {username} <{email}>")


@app.cli.command("promote-admin")
def promote_admin_command():
    """Promote an existing user to admin in the current database."""
    identifier = click.prompt("Existing username or email").strip()
    user = find_user_by_identifier(identifier)
    if not user:
        raise click.ClickException("No user found with that username or email.")
    if user.is_admin:
        click.echo(f"{user.username} is already an admin.")
        return

    try:
        user.is_admin = True
        user.admin_role = user.admin_role or (
            "super_admin"
            if User.query.filter_by(admin_role="super_admin").count() == 0
            else "admin"
        )
        user.is_banned = False
        user.ban_until = None
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise click.ClickException(f"User could not be promoted: {exc}") from exc

    click.echo(f"Promoted to admin: {user.username} <{user.email}>")


@app.cli.command("reset-admin-password")
def reset_admin_password_command():
    """Reset an existing admin password using the app password hash method."""
    identifier = click.prompt("Admin username or email").strip()
    user = find_user_by_identifier(identifier)
    if not user:
        raise click.ClickException("No user found with that username or email.")
    if not user.is_admin:
        raise click.ClickException("That user is not currently an admin.")

    password = click.prompt(
        "New password",
        hide_input=True,
        confirmation_prompt=True,
    )
    validate_cli_password(password)

    try:
        user.set_password(password)
        user.is_banned = False
        user.ban_until = None
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise click.ClickException(f"Admin password could not be reset: {exc}") from exc

    click.echo(f"Password reset for admin: {user.username} <{user.email}>")


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
