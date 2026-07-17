import os
import io
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from models import AuditLog, ChallengeCompletion, ChallengeParticipant, FamilyCampaignContribution, FamilyChallenge, FamilyContributionCampaign, FamilyGalleryItem, FamilyUpgradePurchase, PasswordResetToken, PointSecurityEvent, PointTransaction, Post, Reaction, User
from models import SiteSetting
from extensions import db
from routes.auth import google_oauth_config, public_url_for, should_make_admin
from routes.main import FEED_FILTERS, SUPPORTIVE_PROMPTS, interest_match_count, normalized_interests
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
from family_levels import family_level_for_xp
from family_upgrades import UPGRADE_CATALOG
from points import award_points, personal_point_balance, spend_personal_points


ROOT = Path(__file__).resolve().parents[1]


class SecurityRegressionTests(unittest.TestCase):
    def test_friend_point_gift_preserves_total_points(self):
        test_app = Flask(__name__)
        test_app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(test_app)
        with test_app.app_context():
            User.__table__.create(db.engine)
            PointTransaction.__table__.create(db.engine)
            sender = User(username="sender", email="sender@example.com", password_hash="x")
            friend = User(username="friend", email="friend@example.com", password_hash="x")
            db.session.add_all((sender, friend))
            db.session.flush()
            award_points(
                amount=100, reason="Starting test balance", source_type="test",
                source_id=1, unique_reward_key="test:gift:starting", user_id=sender.id,
            )
            db.session.commit()
            spend_personal_points(
                user_id=sender.id, amount=25, reason="Shared with friend",
                source_type="friend_point_gift", source_id=friend.id,
                unique_reward_key="test:gift:sent",
            )
            award_points(
                amount=25, reason="Shared by sender", source_type="friend_point_gift_received",
                source_id=sender.id, unique_reward_key="test:gift:received", user_id=friend.id,
                awarded_by_id=sender.id,
            )
            db.session.commit()
            self.assertEqual(personal_point_balance(sender.id), 75)
            self.assertEqual(personal_point_balance(friend.id), 25)
            self.assertEqual(personal_point_balance(sender.id) + personal_point_balance(friend.id), 100)

    def test_stage_eighteen_campaigns_have_safe_unique_accounting(self):
        constraint_columns = {
            column.name for constraint in FamilyContributionCampaign.__table__.constraints
            if constraint.name == "uq_family_active_campaign" for column in constraint.columns
        }
        self.assertEqual(constraint_columns, {"family_id", "active_slot"})
        contribution_constraints = {constraint.name for constraint in FamilyCampaignContribution.__table__.constraints}
        self.assertIn("uq_campaign_contribution_key", contribution_constraints)
        point_source = (ROOT / "points.py").read_text()
        self.assertIn("def spend_personal_points", point_source)
        self.assertIn('transaction_kind="spend"', point_source)
        self.assertIn("personal_point_balance(user_id) < amount", point_source)

    def test_stage_eighteen_refunds_and_activation_are_server_controlled(self):
        source = (ROOT / "routes/family.py").read_text()
        self.assertIn("def cancel_upgrade_campaign", source)
        self.assertIn("reverse_reward_group(", source)
        self.assertIn('campaign.status = "cancelled"', source)
        self.assertIn("contribution.refunded = True", source)
        self.assertIn('campaign.status = "activated"', source)
        self.assertIn("campaign.active_slot = None", source)
        self.assertIn("family_available + contributed < campaign.points_required", source)
        self.assertIn("Only Family owners and admins can activate campaign upgrades", source)

    def test_stage_eighteen_ui_is_cooperative_mobile_and_confirmed(self):
        page = (ROOT / "templates/family_upgrades.html").read_text()
        styles = (ROOT / "static/css/styles.css").read_text()
        for label in ["Total required", "Family Points available", "Member contributions", "Still needed", "Contributors"]:
            self.assertIn(label, page)
        for amount in ["10 points", "25 points", "50 points", "100 points", "Custom amount"]:
            self.assertIn(amount, page)
        self.assertIn("Cancel and refund", page)
        self.assertIn("data-confirm=", page)
        self.assertIn("@media (max-width: 620px)", styles)
        self.assertIn(".campaign-totals", styles)

    def test_stage_eighteen_milestones_and_contribution_messages_are_not_competitive(self):
        source = (ROOT / "routes/family.py").read_text()
        self.assertIn("(25, 50, 75, 100)", source)
        self.assertIn("contributed {amount} points toward", source)
        self.assertIn("Thank you for helping", source)
        self.assertIn("Together, {family.name} unlocked", source)
        self.assertIn("amount > remaining", source)
        self.assertIn("amount > 500", (ROOT / "points.py").read_text())
        self.assertIn("optional_family_features_unavailable", source)

    def test_stage_seventeen_upgrade_catalog_is_practical_and_capacity_costs_increase(self):
        expected = {
            "custom_banner", "pinned_announcements", "challenge_slots", "family_gallery",
            "quiz_slots", "extra_themes", "advanced_statistics", "custom_badge_frame",
            "celebration_certificates", "certificate_sunrise", "certificate_unity",
            "certificate_excellence", "certificate_legacy",
            "extra_admins", "extra_moderators", "family_calendar", "resource_library",
            "capacity_75", "capacity_100", "capacity_150", "capacity_250", "capacity_500",
        }
        self.assertEqual(set(UPGRADE_CATALOG), expected)
        capacity_costs = [UPGRADE_CATALOG[f"capacity_{size}"]["cost"] for size in (75, 100, 150, 250, 500)]
        self.assertEqual(capacity_costs, sorted(capacity_costs))
        self.assertEqual([UPGRADE_CATALOG[f"capacity_{size}"]["capacity"] for size in (75, 100, 150, 250, 500)], [75, 100, 150, 250, 500])

    def test_stage_seventeen_purchases_are_atomic_unique_and_server_priced(self):
        constraint_columns = {
            column.name for constraint in FamilyUpgradePurchase.__table__.constraints
            if constraint.name == "uq_family_upgrade_purchase" for column in constraint.columns
        }
        self.assertEqual(constraint_columns, {"family_id", "upgrade_key"})
        family_source = (ROOT / "routes/family.py").read_text()
        point_source = (ROOT / "points.py").read_text()
        self.assertIn("with_for_update()", family_source)
        self.assertIn('definition = upgrade_definition(upgrade_key)', family_source)
        self.assertIn('unique_reward_key=f"family_upgrade:{family.id}:{upgrade_key}"', family_source)
        self.assertIn("def spend_family_points", point_source)
        self.assertIn('transaction_kind="spend"', point_source)
        self.assertIn('"activate_upgrade": {"owner", "admin"}', family_source)

    def test_stage_seventeen_limits_and_upgrade_ui_are_enforced(self):
        family_source = (ROOT / "routes/family.py").read_text()
        chat_source = (ROOT / "routes/chat.py").read_text()
        page = (ROOT / "templates/family_upgrades.html").read_text()
        create_page = (ROOT / "templates/create_family.html").read_text()
        edit_page = (ROOT / "templates/edit_family.html").read_text()
        self.assertIn("active_challenge_limit", family_source)
        self.assertIn("open_quiz_limit", family_source)
        self.assertIn("pinned_announcement_limit", chat_source)
        self.assertIn("data-confirm=", page)
        self.assertIn("available Family Points", page)
        self.assertIn("Purchase history", page)
        self.assertNotIn('name="member_limit"', create_page)
        self.assertNotIn('name="member_limit"', edit_page)
        self.assertEqual(FamilyGalleryItem.__tablename__, "family_gallery_items")

    def test_stage_sixteen_family_levels_use_lifetime_xp_thresholds(self):
        thresholds = {1: 0, 2: 100, 3: 300, 4: 750, 5: 1500, 6: 3000, 7: 5000}
        with patch("family_levels.family_level_thresholds", return_value=(thresholds, 2500)):
            self.assertEqual(family_level_for_xp(0)["name"], "Seed")
            self.assertEqual(family_level_for_xp(300)["level"], 3)
            self.assertEqual(family_level_for_xp(5000)["name"], "Rising Family")
            self.assertEqual(family_level_for_xp(7500)["level"], 8)
            self.assertEqual(family_level_for_xp(1499)["xp_to_next"], 1)

    def test_stage_sixteen_spending_does_not_reduce_family_xp(self):
        point_source = (ROOT / "points.py").read_text()
        model_source = (ROOT / "models.py").read_text()
        migration = (ROOT / "migrations/20260713_stage16_family_levels.sql").read_text()
        self.assertIn('transaction_kind == "spend"', point_source)
        self.assertIn('transaction_kind == "award"', point_source)
        self.assertIn("def family_lifetime_xp", point_source)
        self.assertIn("ck_point_transaction_kind", model_source)
        self.assertIn("family_level_rising_interval", migration)

    def test_stage_sixteen_family_level_ui_and_settings_are_complete(self):
        family_template = (ROOT / "templates/family_detail.html").read_text()
        settings_template = (ROOT / "templates/admin_settings.html").read_text()
        moderation_source = (ROOT / "routes/moderation.py").read_text()
        for label in ["lifetime XP", "available Family Points", "Challenges completed", "Goals achieved", "Encouragement milestones", "Days growing together"]:
            self.assertIn(label, family_template)
        self.assertIn("family_level_rising_interval", settings_template)
        self.assertIn("configured_thresholds", moderation_source)
        self.assertIn('require_admin_role("super_admin")', moderation_source)

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
            "family_xp", "point_transfers", "referral_rewards", "contribution_campaigns",
            "premium_membership", "premium_families", "premium_profiles",
            "premium_storage", "premium_upload_limits", "premium_themes",
            "premium_analytics", "premium_challenges",
            "premium_verification_applications", "premium_beta_testing",
        }
        self.assertEqual(set(FEATURE_FLAG_DEFINITIONS), expected)
        defaults = default_feature_flags()
        self.assertTrue(defaults["personal_points"])
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
            self.assertTrue(get_feature_flags()["personal_points"])
            db.session.add(
                SiteSetting(key=feature_flag_key("personal_points"), value="false")
            )
            db.session.commit()
            self.assertFalse(get_feature_flags()["personal_points"])

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
            {"all", "interests", "videos", "families", "highlights", "kindness", "trending"},
        )
        for prompt in [
            "What’s on your heart today?",
            "Share a small win.",
            "Does anyone need encouragement?",
            "What did you learn today?",
            "Write something uplifting or honest.",
        ]:
            self.assertIn(prompt, SUPPORTIVE_PROMPTS)

    def test_interests_are_optional_bounded_and_do_not_override_feed_privacy(self):
        self.assertEqual(
            normalized_interests(" Coding, books, coding,  fitness  "),
            ["coding", "books", "fitness"],
        )
        self.assertEqual(len(normalized_interests(",".join(f"topic-{i}" for i in range(20)))), 10)
        post = Post(content="Learning Python coding together", purpose="normal")
        self.assertEqual(interest_match_count(post, ["coding", "books"]), 1)
        routes = (ROOT / "routes" / "main.py").read_text()
        self.assertIn("posts = [post for post in posts if can_view_post(post)]", routes)

    def test_non_members_are_not_given_broken_family_post_links(self):
        template = (ROOT / "templates" / "family_detail.html").read_text()
        self.assertIn("Posts are for Family members", template)
        self.assertIn("{% if member %}\n    {% for post in posts %}", template)

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

    def test_stage_nineteen_family_home_has_living_dashboard_cards(self):
        template = (ROOT / "templates" / "family_detail.html").read_text()
        for heading in [
            "Today in the Family", "Active Challenge", "Family Goal",
            "Someone Needs Encouragement", "Upcoming Quiz", "Recent Chat",
            "Recent Posts", "Weekly Activity", "New Members",
            "Family Level Progress", "Upgrade Campaign", "Top Supporter of the Week",
        ]:
            self.assertIn(heading, template)
        self.assertIn("Here’s what your family is growing through today.", template)
        self.assertIn("family-dashboard-grid--living", template)

    def test_website_moderation_can_remove_posts_without_expanding_chat_access(self):
        main_routes = (ROOT / "routes" / "main.py").read_text()
        chat_routes = (ROOT / "routes" / "chat.py").read_text()
        self.assertIn('action_type="post_delete"', main_routes)
        self.assertIn("website_moderator_role(current_user)", main_routes)
        self.assertIn("Post removed directly by website moderation.", main_routes)
        self.assertNotIn("website_moderator_role", chat_routes)

    def test_legacy_admin_flag_and_super_admin_family_feed_are_supported(self):
        moderation_routes = (ROOT / "routes" / "moderation.py").read_text()
        main_routes = (ROOT / "routes" / "main.py").read_text()
        self.assertIn("if role in ADMIN_ROLE_RANK and role", moderation_routes)
        self.assertIn('website_moderator_role(current_user) == "super_admin"', main_routes)
        self.assertIn("Post.id != None", main_routes)

    def test_stage_twenty_timeline_is_private_grouped_and_filterable(self):
        routes = (ROOT / "routes" / "family.py").read_text()
        template = (ROOT / "templates" / "family_detail.html").read_text()
        self.assertIn('TIMELINE_FILTERS = {"all", "challenges", "goals", "members", "upgrades"}', routes)
        self.assertIn("if not current_member and not is_super_admin", routes)
        self.assertIn("completion_groups", routes)
        self.assertNotIn('"reactions"', routes[routes.index("def family_activity_timeline"):routes.index("def validate_family_payload")])
        for label in ["All", "Challenges", "Goals", "Members", "Upgrades"]:
            self.assertIn(label, template)
        self.assertIn("Meaningful moments, without the noise", template)

    def test_family_dashboard_handles_legacy_null_scores_and_missing_profiles(self):
        routes = (ROOT / "routes" / "family.py").read_text()
        template = (ROOT / "templates" / "family_detail.html").read_text()
        self.assertGreaterEqual(routes.count("completion.points_awarded or 0"), 2)
        self.assertGreaterEqual(routes.count("attempt.score or 0"), 2)
        self.assertIn("post.author.profile and post.author.profile.display_name", template)

    def test_family_avatar_stack_import_receives_template_context(self):
        template = (ROOT / "templates" / "family_detail.html").read_text()
        first_line = template.splitlines()[0]
        self.assertIn("avatar_stack", first_line)
        self.assertIn("with context", first_line)

    def test_stage_twenty_one_checkins_are_private_unique_and_server_validated(self):
        models = (ROOT / "models.py").read_text()
        routes = (ROOT / "routes" / "main.py").read_text()
        self.assertIn('db.UniqueConstraint("user_id", "checkin_date"', models)
        self.assertIn('privacy = db.Column(db.String(24), default="private"', models)
        self.assertIn('request.form.get("public_consent") != "yes"', routes)
        self.assertIn("family_id not in family_ids", routes)
        self.assertIn('@feature_required("daily_checkins")', routes)
        self.assertNotIn("award_", routes[routes.index("def daily_checkins"):routes.index("def respond_to_checkin")])

    def test_stage_twenty_one_support_and_family_summary_respect_visibility(self):
        family_routes = (ROOT / "routes" / "family.py").read_text()
        template = (ROOT / "templates" / "daily_checkins.html").read_text()
        self.assertIn('DailyCheckIn.privacy == "family"', family_routes)
        self.assertIn('DailyCheckIn.privacy == "all_families"', family_routes)
        self.assertIn("Check-ins do not earn daily points.", template)
        self.assertIn("I understand this mood and note will be visible publicly.", template)
        for mood in ["Happy", "Peaceful", "Motivated", "Okay", "Tired", "Worried", "Struggling", "Prefer not to say"]:
            self.assertIn(mood.lower().replace(" ", "_") if mood == "Prefer not to say" else mood.lower(), (ROOT / "routes" / "main.py").read_text().lower())

    def test_stage_twenty_two_anonymous_identity_and_admin_privacy_are_server_enforced(self):
        models = (ROOT / "models.py").read_text()
        routes = (ROOT / "routes" / "family.py").read_text()
        self.assertIn("class EncouragementRequest", models)
        self.assertIn("user_id = db.Column", models)
        self.assertIn('if item.visibility == "admins"', routes)
        self.assertIn('family_role(member) in {"owner", "admin"}', routes)
        self.assertIn('@feature_required("anonymous_support_posts")', routes)
        self.assertIn("EncouragementRequestReport", routes)

    def test_stage_twenty_two_crisis_guidance_and_support_options_are_complete(self):
        routes = (ROOT / "routes" / "family.py").read_text()
        template = (ROOT / "templates" / "family_encouragement.html").read_text()
        for label in ["I need someone to listen", "Motivation", "Advice", "Celebration", "Grief or sadness", "Feeling alone", "Study/work encouragement", "Other"]:
            self.assertIn(label, routes)
        for label in ["Support", "I Understand", "Keep Going", "You Inspire Me", "Thoughtful comment"]:
            self.assertIn(label, routes + template)
        self.assertIn("cannot provide emergency or professional mental-health care", template)
        self.assertIn("WHO crisis and suicide-prevention guidance", template)

    def test_stage_twenty_three_streaks_only_use_constructive_server_actions(self):
        service = (ROOT / "streaks.py").read_text()
        main_routes = (ROOT / "routes" / "main.py").read_text()
        family_routes = (ROOT / "routes" / "family.py").read_text()
        for streak_type in ["challenge_progress", "habit", "reflection", "encouragement", "learning"]:
            self.assertIn(streak_type, service)
        self.assertIn("len(note) >= 10", main_routes)
        self.assertIn("len(comment) >= 10", family_routes)
        self.assertNotIn("record_streak_activity", main_routes[main_routes.index("def home"):main_routes.index("def get_reaction_counts")])

    def test_stage_twenty_three_timezone_grace_and_rewards_are_idempotent(self):
        models = (ROOT / "models.py").read_text()
        service = (ROOT / "streaks.py").read_text()
        template = (ROOT / "templates" / "streaks.html").read_text()
        self.assertIn('timezone = db.Column(db.String(64)', models)
        self.assertIn('db.UniqueConstraint("unique_activity_key"', models)
        self.assertIn('db.UniqueConstraint("streak_id", "milestone"', models)
        self.assertIn("grace_days_available", models + service)
        self.assertIn("ZoneInfo", service)
        self.assertIn("MILESTONE_REWARDS", service)
        self.assertIn("Today is a fresh start.", template)
        self.assertIn("only if it feels supportive", service)

    def test_stage_twenty_four_goal_models_and_progress_are_server_controlled(self):
        models = (ROOT / "models.py").read_text()
        routes = (ROOT / "routes" / "goals.py").read_text()
        for model in ["Goal", "GoalParticipant", "GoalMilestone", "GoalProgress", "GoalActivity", "GoalEncouragement"]:
            self.assertIn(f"class {model}", models)
        self.assertIn("if amount <= 0 or amount > goal.target_amount - goal.current_progress", routes)
        self.assertIn("validate_upload(evidence)", routes)
        self.assertIn("family_goal_admin(goal)", routes)
        self.assertIn("active_query.count() >= 10", routes)

    def test_stage_twenty_four_rewards_sharing_and_activity_are_idempotent(self):
        routes = (ROOT / "routes" / "goals.py").read_text()
        migration = (ROOT / "migrations" / "20260713_stage24_goals.sql").read_text()
        self.assertIn("goal.created_at > datetime.utcnow() - timedelta(hours=24)", routes)
        self.assertIn('unique_reward_key=f"goal:', routes)
        self.assertIn("milestone_percent in {25, 50, 75}", routes)
        self.assertIn("family_goal_points_today + amount > 50", routes)
        self.assertIn('achievement_type="goal_achieved"', routes)
        self.assertIn("Nothing is shared unless you choose it.", (ROOT / "templates" / "goal_share.html").read_text())
        self.assertIn("migration_stage24_family_goals", migration)

    def test_stage_twenty_five_poll_privacy_and_duplicate_controls(self):
        models = (ROOT / "models.py").read_text()
        routes = (ROOT / "routes" / "family.py").read_text()
        template = (ROOT / "templates" / "family_detail.html").read_text()
        self.assertIn("results_visibility", models + routes + template)
        self.assertIn('db.UniqueConstraint("poll_id", "option_id", "user_id"', models)
        self.assertIn("participation_percentage", routes + template)
        self.assertIn("poll.anonymous_voting", template)
        self.assertIn("Your vote was already recorded", routes)

    def test_stage_twenty_five_quiz_scoring_is_server_verified_and_idempotent(self):
        models = (ROOT / "models.py").read_text()
        routes = (ROOT / "routes" / "family.py").read_text()
        migration = (ROOT / "migrations" / "20260713_stage25_polls_quizzes.sql").read_text()
        for field in ["pass_mark", "attempt_limit", "percentage", "passed", "points_awarded", "explanation"]:
            self.assertIn(field, models)
        self.assertIn("selected_choice.is_correct", routes)
        self.assertIn('unique_reward_key=f"quiz:{quiz.id}:user:{current_user.id}:reward"', routes)
        self.assertIn("quiz.time_limit_seconds", routes)
        self.assertIn("migration_stage25_polls_quizzes", migration)

    def test_stage_twenty_five_admin_review_progress_and_weekly_highlight(self):
        routes = (ROOT / "routes" / "family.py").read_text()
        family_template = (ROOT / "templates" / "family_detail.html").read_text()
        quiz_template = (ROOT / "templates" / "quiz_take.html").read_text()
        self.assertIn("def quiz_performance", routes)
        self.assertIn('family_has_permission(member, "create_quiz")', routes)
        self.assertIn("dashboard_quiz_highlight", routes + family_template)
        self.assertIn("data-quiz-progress", quiz_template)

    def test_stage_twenty_six_reports_are_weekly_idempotent_snapshots(self):
        models = (ROOT / "models.py").read_text()
        service = (ROOT / "weekly_reports.py").read_text()
        migration = (ROOT / "migrations" / "20260713_stage26_weekly_family_reports.sql").read_text()
        self.assertIn("class FamilyWeeklyReport", models)
        self.assertIn('db.UniqueConstraint("family_id", "week_start"', models)
        self.assertIn("snapshot = db.Column(db.JSON", models)
        self.assertIn("completed_week_bounds", service)
        self.assertIn("migration_stage26_weekly_family_reports", migration)

    def test_stage_twenty_six_notifications_and_publication_cannot_duplicate(self):
        routes = (ROOT / "routes" / "family.py").read_text()
        self.assertIn('report.notified_at is None', routes)
        self.assertIn('report.published_post_id', routes)
        self.assertIn('.with_for_update()', routes)
        self.assertIn('@feature_required("weekly_reports")', routes)
        self.assertIn('family_has_permission(member, "create_poll")', routes)

    def test_stage_twenty_six_is_warm_and_does_not_shame_inactive_members(self):
        template = (ROOT / "templates" / "family_weekly_report.html").read_text()
        service = (ROOT / "weekly_reports.py").read_text()
        for title in ["Supporter of the Week", "Most Improved", "Learning Champion"]:
            self.assertIn(title, service)
        self.assertIn("without comparing or shaming anyone", template)
        self.assertNotIn("inactive member ranking", (service + template).lower())

    def test_stage_twenty_seven_profile_privacy_is_server_enforced(self):
        models = (ROOT / "models.py").read_text()
        routes = (ROOT / "routes" / "main.py").read_text()
        for field in ["show_point_balance", "show_streaks", "show_achievements",
                      "show_family_memberships", "show_checkins", "show_goal_progress"]:
            self.assertIn(field, models)
            self.assertIn(field, routes)
        self.assertIn('membership.family.privacy == "public"', routes)
        self.assertIn('checkin_query.filter_by(privacy="public")', routes)
        self.assertIn('goals_query.filter_by(visibility="public")', routes)

    def test_stage_twenty_seven_uses_verified_growth_not_kindness_score(self):
        routes = (ROOT / "routes" / "main.py").read_text()
        template = (ROOT / "templates" / "profile.html").read_text()
        self.assertIn('verification_status="completed"', routes)
        self.assertIn("StreakMilestone.query", routes)
        self.assertIn("People encouraged", template)
        self.assertIn("No kindness score", template)
        self.assertNotIn("kindness_score", routes + (ROOT / "models.py").read_text())

    def test_stage_twenty_seven_migration_and_defaults_preserve_privacy(self):
        migration = (ROOT / "migrations" / "20260713_stage27_growth_profiles.sql").read_text()
        self.assertIn("show_point_balance BOOLEAN NOT NULL DEFAULT FALSE", migration)
        self.assertIn("show_checkins BOOLEAN NOT NULL DEFAULT FALSE", migration)
        self.assertIn("show_goal_progress BOOLEAN NOT NULL DEFAULT FALSE", migration)
        self.assertIn("migration_stage27_growth_profiles", migration)

    def test_stage_twenty_eight_badges_are_server_controlled_and_audited(self):
        models = (ROOT / "models.py").read_text()
        moderation = (ROOT / "routes" / "moderation.py").read_text()
        self.assertIn("class RiseBadgeAssignment", models)
        self.assertIn("verification_note", models)
        self.assertIn('require_admin_role("super_admin")', moderation[moderation.index("def set_user_badge"):])
        self.assertIn("record_admin_audit", moderation[moderation.index("def set_user_badge"):moderation.index("def toggle_ban_user")])
        self.assertIn("with_for_update", moderation)

    def test_stage_twenty_eight_family_admin_badge_is_contextual(self):
        service = (ROOT / "badges.py").read_text()
        family_template = (ROOT / "templates" / "family_detail.html").read_text()
        feed_template = (ROOT / "templates" / "feed.html").read_text()
        self.assertIn("if family is not None", service)
        self.assertIn('membership.role in {"owner", "admin"}', service)
        self.assertIn("rise_user_badges(membership.user, family)", family_template)
        self.assertNotIn("rise_user_badges(post.author, family)", feed_template)

    def test_stage_twenty_eight_unique_mark_tooltips_and_impersonation_protection(self):
        component = (ROOT / "templates" / "components" / "ui.html").read_text()
        auth = (ROOT / "routes" / "auth.py").read_text()
        migration = (ROOT / "migrations" / "20260713_stage28_risetogether_badges.sql").read_text()
        self.assertIn("rise-badge__seal", component)
        self.assertIn("rise-badge__burst", component)
        self.assertIn("rise-badge__check", component)
        self.assertIn("Verified by RiseTogether.", (ROOT / "badges.py").read_text())
        self.assertIn('role="tooltip"', component)
        self.assertIn("SAFE_USERNAME_RE", auth)
        self.assertIn("Badge-like symbols are not allowed", auth)
        self.assertIn("migration_stage28_risetogether_badges", migration)

    def test_stage_twenty_nine_notifications_group_and_dedupe_server_side(self):
        models = (ROOT / "models.py").read_text()
        service = (ROOT / "notifications_service.py").read_text()
        self.assertIn("class NotificationPreference", models)
        self.assertIn("class NotificationDeliveryKey", models)
        self.assertIn("event_count", models + service)
        self.assertIn("with_for_update", service)
        self.assertIn("NotificationDeliveryKey(key=dedupe_key", service)
        self.assertIn("timedelta(hours=24)", service)

    def test_stage_twenty_nine_message_context_privacy_and_deep_links(self):
        chat = (ROOT / "routes" / "chat.py").read_text()
        template = (ROOT / "templates" / "chat.html").read_text()
        helpers = (ROOT / "helpers.py").read_text()
        self.assertIn("preview =", chat)
        self.assertIn("#message-{message.id}", chat)
        self.assertIn('id="message-{{ message.id }}"', template)
        self.assertIn("notification_previews_enabled", helpers)
        self.assertIn("You have a new message.", helpers)

    def test_stage_twenty_nine_preferences_reads_and_required_categories(self):
        service = (ROOT / "notifications_service.py").read_text()
        main = (ROOT / "routes" / "main.py").read_text()
        settings = (ROOT / "templates" / "settings.html").read_text()
        migration = (ROOT / "migrations" / "20260713_stage29_smart_notifications.sql").read_text()
        for category in ["message", "comment", "reaction", "follow", "family_invitation",
                         "challenge_invitation", "challenge_reminder", "challenge_completed",
                         "goal_progress", "weekly_report", "upgrade_campaign",
                         "contribution_received", "family_level", "encouragement"]:
            self.assertIn(f'"{category}"', service)
        self.assertIn("mark_notification_read", main)
        self.assertIn("mark_all_notifications_read", main)
        self.assertIn("notification_preferences", settings)
        self.assertIn("migration_stage29_smart_notifications", migration)


if __name__ == "__main__":
    unittest.main()
