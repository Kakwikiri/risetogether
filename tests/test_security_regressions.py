import os
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from models import AuditLog
from routes.auth import google_oauth_config, public_url_for, should_make_admin


ROOT = Path(__file__).resolve().parents[1]


class SecurityRegressionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
