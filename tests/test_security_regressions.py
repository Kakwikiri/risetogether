import os
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from models import AuditLog, PasswordResetToken
from models import SiteSetting
from extensions import db
from routes.auth import google_oauth_config, public_url_for, should_make_admin
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


if __name__ == "__main__":
    unittest.main()
