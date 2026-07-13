import os
import io
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from models import AuditLog, ChallengeCompletion, ChallengeParticipant, FamilyChallenge, PasswordResetToken, PointSecurityEvent, PointTransaction, Reaction
from models import SiteSetting
from extensions import db
from routes.auth import google_oauth_config, public_url_for, should_make_admin
from routes.main import FEED_FILTERS, SUPPORTIVE_PROMPTS
from routes.family import (
    CHALLENGE_ALLOWED_REWARD_TIERS,
    CHALLENGE_VISIBILITIES,
    COMPLETION_FREQUENCIES,
    EVIDENCE_REQUIREMENTS,
    PARTICIPANT_SCOPES,
    REWARD_TIER_DEFAULTS,
    challenge_completion_period,
)
from security import csrf_token, init_csrf
from helpers import family_avatar_url, user_avatar_url, validate_upload
from feature_flags import (
    FEATURE_FLAG_DEFINITIONS,
    default_feature_flags,
    feature_flag_key,
    get_feature_flags,
)


ROOT = Path(__file__).resolve().parents[1]


class SecurityRegressionTests(unittest.TestCase):
    def test_stage_fifteen_point_reversals_are_audited_and_grouped(self):
        point_source = (ROOT / "points.py").read_text()
        moderation_source = (ROOT / "routes/moderation.py").read_text()
        family_source = (ROOT / "routes/family.py").read_text()
        self.assertIn("def reverse_reward_group", point_source)
        self.assertIn("def reverse_completion_rewards_for_user", point_source)
        self.assertIn("source_id=transaction.source_id", point_source)
        self.assertIn('"point_reward_reversal"', moderation_source)
        self.assertIn('completion.verification_status = "invalidated"', moderation_source)
        self.assertIn("Family leaders cannot approve their own", family_source)
        self.assertIn("challenge_points_reversed", family_source)

    def test_stage_fifteen_rate_limits_and_security_flags_are_database_backed(self):
        family_source = (ROOT / "routes/family.py").read_text()
        point_source = (ROOT / "points.py").read_text()
        migration = (ROOT / "migrations/20260713_stage15_point_security.sql").read_text()
        self.assertIn("recent_completion_count >= 20", family_source)
        self.assertIn("DAILY_REPEATABLE_PERSONAL_LIMIT = 100", point_source)
        self.assertIn("class PointLimitExceeded", point_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS point_security_events", migration)
        self.assertIn("reversal_reason", migration)
        self.assertIn("suspicious", migration)
        self.assertEqual(PointSecurityEvent.__tablename__, "point_security_events")

    def test_stage_fifteen_admin_ledger_is_super_admin_only(self):
        source = (ROOT / "routes/moderation.py").read_text()
        template = (ROOT / "templates/admin_point_transactions.html").read_text()
        route = source[source.index("def admin_point_transactions"):]
        self.assertIn('require_admin_role("super_admin")', route[:300])
        self.assertIn("Point transactions", template)
        self.assertIn("Reverse linked reward", template)
        self.assertIn('name="csrf_token"', template)

    def test_stage_fourteen_ledger_has_idempotency_and_recipient_constraints(self):
        self.assertTrue(PointTransaction.unique_reward_key.unique)
        constraint_names = {constraint.name for constraint in PointTransaction.__table__.constraints}
        self.assertIn("ck_point_transaction_single_recipient", constraint_names)
        self.assertIn("ck_point_transaction_positive_amount", constraint_names)
        migration = (ROOT / "migrations/20260713_stage14_point_ledger.sql").read_text()
        self.assertIn("ON CONFLICT (unique_reward_key) DO NOTHING", migration)
        self.assertIn("challenge_completion:' || cc.id || ':' || 'personal", migration)
        self.assertIn("challenge_completion:' || cc.id || ':' || 'family", migration)

    def test_stage_fourteen_rewards_are_server_side_and_history_is_private(self):
        family_source = (ROOT / "routes/family.py").read_text()
        point_source = (ROOT / "points.py").read_text()
        main_source = (ROOT / "routes/main.py").read_text()
        history = (ROOT / "templates/point_history.html").read_text()
        self.assertIn("award_challenge_completion_points(completion)", family_source)
        self.assertIn("verification_status != \"completed\"", point_source)
        self.assertIn("@login_required\ndef point_history", main_source)
        self.assertIn("PointTransaction.user_id == current_user.id", main_source)
        self.assertIn("Personal Points", history)
        self.assertIn("Family Points", history)

    def test_stage_seven_reactions_enforce_one_choice_per_post(self):
        constraint_columns = {
            column.name for constraint in Reaction.__table__.constraints
            if constraint.name == "uq_reaction_post_user"
            for column in constraint.columns
        }
        self.assertEqual(constraint_columns, {"post_id", "user_id"})
        migration = Path("migrations/20260713_stage7_single_reaction.sql").read_text()
        self.assertIn("ON reactions (post_id, user_id)", migration)
        self.assertIn("r.id < newer.id", migration)

    def test_stage_seven_reaction_ui_is_live_and_accessible(self):
        ui = Path("templates/components/ui.html").read_text()
        javascript = Path("static/js/app.js").read_text()
        base = Path("templates/base.html").read_text()
        self.assertIn('aria-pressed=', ui)
        self.assertIn("data-reaction-list", ui)
        self.assertIn("selected_reaction", javascript)
        self.assertIn("data-reaction-modal", base)

    def test_stage_seven_reactor_list_checks_visibility_and_limits_fields(self):
        source = Path("routes/main.py").read_text()
        self.assertIn('def post_reactors(post_id):', source)
        self.assertIn("if not can_view_post(post):", source)
        self.assertIn("User.is_hidden_from_directory == False", source)
        self.assertNotIn('"email": user.email', source)

    def test_stage_eight_comments_have_one_level_management_and_reactions(self):
        source = (ROOT / "routes/main.py").read_text()
        template = (ROOT / "templates/post_detail.html").read_text()
        self.assertIn("parent.parent", source)
        self.assertIn("def manage_comment(comment_id, action):", source)
        self.assertIn("COMMENT_REACTION_LABELS", source)
        self.assertIn("Be the first to encourage them", template)
        self.assertIn("Load more comments", template)

    def test_stage_eight_shares_reference_original_and_enforce_privacy(self):
        source = (ROOT / "routes/main.py").read_text()
        share = (ROOT / "templates/share_post.html").read_text()
        migration = (ROOT / "migrations/20260713_stage8_comments_sharing.sql").read_text()
        self.assertIn("original_post_id=source_post.id", source)
        self.assertIn("can_share_publicly", source)
        self.assertIn("source_post.family_id == family.id", source)
        self.assertIn("Copy post link", share)
        self.assertIn("uq_public_post_reshare", migration)

    def test_stage_nine_achievement_sharing_is_opt_in_and_server_controlled(self):
        family_source = (ROOT / "routes/family.py").read_text()
        model_source = (ROOT / "models.py").read_text()
        self.assertIn('auto_share_completed_challenges = db.Column(db.Boolean, default=False', model_source)
        self.assertIn('challenge_completion_id=completion.id', family_source)
        self.assertIn('audience not in {"public", "family", "private"}', family_source)
        self.assertIn('family.privacy != "public"', family_source)
        self.assertIn('if not is_feature_enabled("achievement_posts")', family_source)

    def test_stage_nine_achievement_card_and_choice_screen_are_complete(self):
        components = (ROOT / "templates/components/ui.html").read_text()
        choice = (ROOT / "templates/share_achievement.html").read_text()
        settings = (ROOT / "templates/settings.html").read_text()
        for achievement_type in [
            "challenge_completed", "goal_achieved", "streak_milestone", "quiz_passed",
            "family_level_increased", "family_upgrade_unlocked",
            "encouragement_milestone", "weekly_family_milestone",
        ]:
            self.assertIn(achievement_type, components)
        self.assertIn("Share this achievement?", choice)
        self.assertIn('value="private"', choice)
        self.assertIn("Automatically share my completed challenges", settings)

    def test_stage_ten_fixed_reward_tiers_and_recommended_caps(self):
        self.assertEqual(
            REWARD_TIER_DEFAULTS,
            {"small": 5, "easy": 10, "medium": 25, "hard": 50, "major": 100},
        )
        self.assertEqual(CHALLENGE_ALLOWED_REWARD_TIERS["daily_check_in"], {"small"})
        self.assertEqual(CHALLENGE_ALLOWED_REWARD_TIERS["task"], {"easy"})
        self.assertNotIn("major", CHALLENGE_ALLOWED_REWARD_TIERS["team"])
        self.assertIn("major", CHALLENGE_ALLOWED_REWARD_TIERS["major"])
        template = (ROOT / "templates/family_detail.html").read_text()
        self.assertIn('name="reward_tier"', template)
        self.assertNotIn('name="points" min="0" value="10"', template)

    def test_stage_ten_completion_rewards_are_snapshotted_per_period(self):
        class DailyChallenge:
            challenge_type = "habit"

        class OneTimeChallenge:
            challenge_type = "task"

        self.assertRegex(challenge_completion_period(DailyChallenge()), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(challenge_completion_period(OneTimeChallenge()), "once")
        constraint = next(
            item for item in ChallengeCompletion.__table__.constraints
            if item.name == "uq_challenge_completion_period"
        )
        self.assertEqual(
            {column.name for column in constraint.columns},
            {"challenge_id", "user_id", "period_key"},
        )
        source = (ROOT / "routes/family.py").read_text()
        self.assertIn("points_awarded=reward_for_challenge(challenge)", source)
        self.assertIn('stats["challenge_points"] += completion.points_awarded', source)
        self.assertIn("attempt.score = min(score, 25)", source)

    def test_stage_eleven_challenge_configuration_fields_and_options(self):
        columns = FamilyChallenge.__table__.columns
        for name in [
            "completion_frequency", "custom_frequency_days", "evidence_requirement",
            "participant_scope", "max_participants", "visibility",
            "requires_admin_approval", "allow_achievement_sharing",
        ]:
            self.assertIn(name, columns)
        self.assertEqual(COMPLETION_FREQUENCIES, {"one_time", "daily", "weekly", "custom"})
        self.assertEqual(EVIDENCE_REQUIREMENTS, {"none", "completion_note", "photo", "video", "audio", "file", "admin_approval"})
        self.assertEqual(PARTICIPANT_SCOPES, {"all_members", "admins_moderators", "owners_admins"})
        self.assertEqual(CHALLENGE_VISIBILITIES, {"family", "public", "admins_only"})

    def test_stage_eleven_frequency_evidence_approval_and_capacity_are_server_enforced(self):
        family_source = (ROOT / "routes/family.py").read_text()
        template = (ROOT / "templates/family_detail.html").read_text()
        script = (ROOT / "static/js/app.js").read_text()
        for snippet in [
            "member_can_participate(challenge, member)",
            "validate_upload(evidence_file)",
            "with_for_update()",
            'verification_status = "pending"',
            "joined_count >= challenge.max_participants",
            "completion.verification_status != \"completed\"",
        ]:
            self.assertIn(snippet, family_source)
        self.assertIn("More challenge settings", template)
        self.assertIn("data-custom-frequency", template)
        self.assertIn("data-challenge-advanced-toggle", script)

        class WeeklyChallenge:
            completion_frequency = "weekly"
            custom_frequency_days = None
            starts_at = None
            created_at = None

        class CustomChallenge:
            completion_frequency = "custom"
            custom_frequency_days = 3
            starts_at = datetime(2026, 7, 1)
            created_at = None

        moment = datetime(2026, 7, 13)
        self.assertEqual(challenge_completion_period(WeeklyChallenge(), moment), "2026-W29")
        self.assertEqual(challenge_completion_period(CustomChallenge(), moment), "custom-4")

    def test_stage_twelve_participants_are_explicit_and_unique(self):
        constraint = next(
            item for item in ChallengeParticipant.__table__.constraints
            if item.name == "uq_challenge_participant_user"
        )
        self.assertEqual(
            {column.name for column in constraint.columns},
            {"challenge_id", "user_id"},
        )
        migration = (ROOT / "migrations/20260713_stage12_challenge_participants.sql").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS challenge_participants", migration)
        self.assertIn("FROM challenge_completions", migration)
        source = (ROOT / "routes/family.py").read_text()
        self.assertIn("def challenge_participation(family_id, challenge_id, action):", source)
        self.assertIn("Join this challenge before submitting a completion.", source)
        self.assertIn("with_for_update()", source)

    def test_stage_twelve_progress_is_participant_based_and_human(self):
        source = (ROOT / "routes/family.py").read_text()
        template = (ROOT / "templates/family_detail.html").read_text()
        for snippet in [
            '"participant_count": participant_count',
            '"completed_count": completed_count',
            '"working_count": max(0, participant_count - completed_count)',
            '"completed_users": completed_users',
            '"working_users": working_users',
            "mandatory_all_members",
        ]:
            self.assertIn(snippet, source)
        self.assertIn("Join challenge", template)
        self.assertIn("Leave challenge", template)
        self.assertIn("participants completed", template)
        self.assertIn("still working", template)
        self.assertIn("Mandatory", template)
        self.assertNotIn("active member challenge slots", template)

    def test_stage_thirteen_completion_evidence_ui_is_previewable_and_progressive(self):
        template = (ROOT / "templates/family_detail.html").read_text()
        script = (ROOT / "static/js/app.js").read_text()
        styles = (ROOT / "static/css/styles.css").read_text()
        for snippet in [
            "What did you do?",
            "data-challenge-evidence-preview",
            "data-challenge-evidence-remove",
            "data-challenge-upload-progress",
            "Supported:",
            "Maximum {{ upload_limits",
        ]:
            self.assertIn(snippet, template)
        self.assertIn('xhr.upload.addEventListener("progress"', script)
        self.assertIn("URL.createObjectURL(file)", script)
        self.assertIn("clearEvidence", script)
        self.assertIn("challenge-upload-progress", styles)
        self.assertIn("celebration-gentle-pop", styles)

    def test_stage_thirteen_server_completion_payload_uses_awarded_points(self):
        source = (ROOT / "routes/family.py").read_text()
        for snippet in [
            '"personal_points": completion.points_awarded',
            '"family_points": completion.points_awarded',
            '"ask_to_share": True',
            '"challenge_completed"',
            "validate_upload(evidence_file)",
            "period_key=period_key",
        ]:
            self.assertIn(snippet, source)
        self.assertIn("ChallengeParticipant.query.filter_by", source)

    def test_feature_flag_registry_has_all_stage_two_flags(self):
        expected = {
            "daily_checkins",
            "personal_points",
            "family_points",
            "streaks",
            "achievement_posts",
            "family_levels",
            "family_upgrades",
            "weekly_reports",
            "enhanced_notifications",
            "verification_badges",
            "anonymous_support_posts",
            "media_autoplay",
            "family_leaderboards",
        }
        self.assertEqual(set(FEATURE_FLAG_DEFINITIONS), expected)
        defaults = default_feature_flags()
        self.assertFalse(defaults["personal_points"])
        self.assertFalse(defaults["anonymous_support_posts"])
        self.assertTrue(defaults["verification_badges"])
        self.assertTrue(defaults["family_leaderboards"])

    def test_feature_flag_admin_route_is_super_admin_only(self):
        source = (ROOT / "routes" / "moderation.py").read_text()
        self.assertIn('@mod_bp.route("/admin/feature-flags", methods=["GET", "POST"])', source)
        self.assertIn("def admin_feature_flags", source)
        self.assertIn('@fresh_login_required\ndef admin_feature_flags', source)
        route_source = source[source.index("def admin_feature_flags"):]
        self.assertIn('require_admin_role("super_admin")', route_source[:500])

    def test_feature_flag_database_value_overrides_safe_default(self):
        test_app = Flask(__name__)
        test_app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(test_app)
        with test_app.app_context():
            SiteSetting.__table__.create(db.engine)
            self.assertFalse(get_feature_flags()["personal_points"])
            db.session.add(
                SiteSetting(key=feature_flag_key("personal_points"), value="true")
            )
            db.session.commit()
            self.assertTrue(get_feature_flags()["personal_points"])

    def test_csrf_rejects_missing_token_and_accepts_valid_token(self):
        test_app = Flask(__name__)
        test_app.config.update(SECRET_KEY="test-secret", TESTING=True)
        init_csrf(test_app)
        test_app.add_url_rule(
            "/token", "token", lambda: csrf_token(), methods=["GET"]
        )
        test_app.add_url_rule(
            "/change", "change", lambda: "changed", methods=["POST"]
        )
        client = test_app.test_client()
        token = client.get("/token").get_data(as_text=True)
        self.assertEqual(client.post("/change").status_code, 400)
        self.assertEqual(
            client.post("/change", data={"csrf_token": token}).status_code, 200
        )

    def test_socket_room_join_requires_server_authorization(self):
        source = (ROOT / "routes" / "chat.py").read_text()
        self.assertIn('room.startswith("private-")', source)
        self.assertIn('room.startswith("family-")', source)
        self.assertIn("current_user.id in {first_id, second_id}", source)
        self.assertIn('emit("room_join_denied"', source)

    def test_upload_validation_rejects_spoofed_extension(self):
        test_app = Flask(__name__)
        test_app.config.update(
            IMAGE_UPLOAD_LIMIT=1024,
            VIDEO_UPLOAD_LIMIT=1024,
            FILE_UPLOAD_LIMIT=1024,
        )
        with test_app.app_context():
            fake = FileStorage(stream=io.BytesIO(b"not a png"), filename="fake.png")
            valid, message = validate_upload(fake)
        self.assertFalse(valid)
        self.assertIn("contents", message)

    def test_signup_email_never_auto_promotes_admin(self):
        self.assertFalse(should_make_admin("owner@example.com"))
        self.assertFalse(should_make_admin("admin@example.com"))

    def test_google_env_aliases_are_supported(self):
        env = {
            "GOOGLE_ID": "client-id-from-render",
            "GOOGLE_SECRET": "client-secret-from-render",
        }
        with patch.dict(os.environ, env, clear=False):
            config = google_oauth_config()
        self.assertEqual(config["client_id"], "client-id-from-render")
        self.assertEqual(config["client_secret"], "client-secret-from-render")

    def test_public_base_url_builds_google_callback(self):
        test_app = Flask(__name__)
        test_app.add_url_rule(
            "/login/google/callback",
            endpoint="auth.google_callback",
            view_func=lambda: "ok",
        )
        with test_app.test_request_context():
            with patch.dict(
                os.environ,
                {"PUBLIC_BASE_URL": "https://rise-together.example"},
                clear=False,
            ):
                self.assertEqual(
                    public_url_for("auth.google_callback"),
                    "https://rise-together.example/login/google/callback",
                )

    def test_audit_log_model_has_safe_expected_fields(self):
        columns = AuditLog.__table__.columns
        for column in [
            "actor_user_id",
            "actor_role",
            "action_type",
            "target_user_id",
            "target_family_id",
            "target_content_id",
            "reason",
            "metadata_text",
            "ip_address",
            "created_at",
        ]:
            self.assertIn(column, columns)
        self.assertNotIn("password", columns)
        self.assertNotIn("token", columns)

    def test_admin_audit_route_and_logging_calls_exist(self):
        source = (ROOT / "routes" / "moderation.py").read_text()
        self.assertIn('@mod_bp.route("/admin/audit-log")', source)
        self.assertIn("def admin_audit_log", source)
        self.assertIn("def record_admin_audit", source)
        for action_type in [
            "settings_change",
            "admin_role_change",
            "family_suspension",
            "warning",
            "suspension",
            "ban",
            "content_deletion",
            "user_delete",
        ]:
            self.assertIn(action_type, source)

    def test_password_reset_code_model_has_expected_fields(self):
        columns = PasswordResetToken.__table__.columns
        for column in [
            "user_id",
            "token",
            "code_hash",
            "used",
            "attempts",
            "created_at",
            "expires_at",
            "last_sent_at",
        ]:
            self.assertIn(column, columns)

    def test_password_reset_routes_use_code_flow(self):
        source = (ROOT / "routes" / "auth.py").read_text()
        self.assertIn("send_password_reset_code_email", source)
        self.assertIn('@auth_bp.route("/forgot-password/verify"', source)
        self.assertIn('@auth_bp.route("/reset-password", methods=["GET", "POST"])', source)
        self.assertIn("generate_password_hash(code)", source)
        self.assertIn("check_password_hash(reset.code_hash, code)", source)
        self.assertIn("timedelta(hours=3)", source)
        self.assertNotIn("Reset word is incorrect", source)

    def test_base_template_has_social_preview_metadata(self):
        source = (ROOT / "templates" / "base.html").read_text()
        for snippet in [
            'property="og:title" content="RiseTogether - Safe Community"',
            'property="og:image" content="{{ social_preview_image_url }}"',
            'property="og:url" content="{{ public_page_url }}"',
            'name="twitter:card" content="summary_large_image"',
            "images/apple-touch-icon-v2.png",
            "images/favicon-v2.png",
        ]:
            self.assertIn(snippet, source)

    def test_stage_three_reusable_ui_components_exist(self):
        components = (ROOT / "templates" / "components" / "ui.html").read_text()
        for macro in [
            "progress_bar",
            "achievement_card",
            "avatar_stack",
            "skeleton",
            "empty_state",
            "status_chip",
            "point_badge",
            "family_level_badge",
        ]:
            self.assertIn(f"macro {macro}", components)
        base = (ROOT / "templates" / "base.html").read_text()
        self.assertIn("data-confirmation-modal", base)
        self.assertIn("data-celebration-modal", base)
        self.assertIn('aria-live="polite"', base)

    def test_stage_three_respects_reduced_motion(self):
        styles = (ROOT / "static" / "css" / "styles.css").read_text()
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("animation-duration: 0.01ms !important", styles)

    def test_stage_four_generated_avatars_are_stable_and_distinct(self):
        class ExampleProfile:
            avatar = ""
            display_name = "Allan"

        class ExampleUser:
            id = 42
            username = "allan"
            profile = ExampleProfile()

        class ExampleFamily:
            id = 42
            name = "Hope Builders"
            profile_image = ""

        user_url = user_avatar_url(ExampleUser())
        self.assertEqual(user_url, user_avatar_url(ExampleUser()))
        self.assertTrue(user_url.startswith("data:image/svg+xml,"))
        self.assertIn("%3EA%3C", user_url)
        family_url = family_avatar_url(ExampleFamily())
        self.assertTrue(family_url.startswith("data:image/svg+xml,"))
        self.assertIn("HB", family_url)
        self.assertNotEqual(user_url, family_url)

    def test_stage_four_templates_use_shared_avatar_helpers(self):
        combined = "\n".join(
            path.read_text() for path in (ROOT / "templates").rglob("*.html")
        )
        self.assertNotIn("images/default-avatar.png", combined)
        self.assertIn("user_avatar_url(", combined)
        self.assertIn("family_avatar_url(", combined)
        self.assertIn("macro user_avatar", combined)
        self.assertIn("macro family_avatar", combined)

    def test_stage_five_mobile_navigation_is_accessible(self):
        base = (ROOT / "templates" / "base.html").read_text()
        for snippet in [
            'aria-label="Open menu"',
            'aria-controls="primary-navigation"',
            'id="primary-navigation"',
            'data-nav-scrim',
            'data-message-badge',
            'data-notification-badge',
        ]:
            self.assertIn(snippet, base)
        script = (ROOT / "static" / "js" / "app.js").read_text()
        self.assertIn("closeNavigation", script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('document.body.classList.add("nav-menu-open")', script)
        self.assertIn('navLinks.querySelectorAll("a")', script)

    def test_stage_five_mobile_tap_targets_and_back_spacing(self):
        styles = (ROOT / "static" / "css" / "styles.css").read_text()
        self.assertIn("min-width: 44px", styles)
        self.assertIn("min-height: 44px", styles)
        self.assertIn("body.nav-menu-open", styles)
        self.assertIn("overscroll-behavior: none", styles)
        self.assertIn(".profile-back-button", styles)

    def test_stage_six_feed_filters_and_prompts_are_complete(self):
        self.assertEqual(
            FEED_FILTERS,
            {"all", "videos", "families", "highlights", "kindness", "trending"},
        )
        for prompt in [
            "What’s on your heart today?",
            "Share a small win.",
            "Does anyone need encouragement?",
            "What did you learn today?",
            "Write something uplifting or honest.",
        ]:
            self.assertIn(prompt, SUPPORTIVE_PROMPTS)

    def test_stage_six_feed_has_loading_empty_and_read_more_states(self):
        template = (ROOT / "templates" / "feed.html").read_text()
        script = (ROOT / "static" / "js" / "app.js").read_text()
        self.assertIn("data-feed-filters", template)
        self.assertIn("data-feed-loading", template)
        self.assertIn("empty_messages", template)
        self.assertIn("data-read-more", template)
        self.assertIn("data-supportive-prompts", template)
        self.assertIn("14000", script)
        self.assertIn('document.activeElement === promptInput', script)


if __name__ == "__main__":
    unittest.main()
