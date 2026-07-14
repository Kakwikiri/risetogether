import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import helpers
from models import FamilyMember


ROOT = Path(__file__).resolve().parents[1]


class FamilyFinalPolishTests(unittest.TestCase):
    def test_family_member_creation_rights_are_additive(self):
        for field in (
            "can_create_polls",
            "can_create_quizzes",
            "can_create_challenges",
            "can_create_campaigns",
        ):
            self.assertIn(field, FamilyMember.__table__.c)
        routes = (ROOT / "routes/family.py").read_text()
        self.assertIn("def update_member_creation_permissions", routes)
        self.assertIn("role_rank(family_role(actor_member)) <= role_rank", routes)

    def test_video_duration_is_limited_to_three_minutes(self):
        result = SimpleNamespace(stderr="Duration: 00:03:00.00, start: 0.000000", returncode=1)
        with patch.object(helpers, "get_ffmpeg_executable", return_value="ffmpeg"), patch.object(
            helpers.subprocess, "run", return_value=result
        ):
            self.assertEqual(helpers.video_duration_seconds("sample.mp4"), 180)
        self.assertEqual(helpers.MAX_VIDEO_DURATION_SECONDS, 180)
        socket_js = (ROOT / "static/js/socket.js").read_text()
        self.assertIn("}, 180000);", socket_js)

    def test_family_tabs_and_media_do_not_overflow(self):
        css = (ROOT / "static/css/styles.css").read_text()
        template = (ROOT / "templates/family_detail.html").read_text()
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", css)
        self.assertIn(".chat-log { overflow-x: hidden; }", css)
        self.assertIn('id="family-chat-locked"', template)
        self.assertIn("data-post-copy", template)

    def test_settings_and_legal_pages_are_visible(self):
        base = (ROOT / "templates/base.html").read_text()
        main_routes = (ROOT / "routes/main.py").read_text()
        self.assertIn("url_for('main.settings')", base)
        self.assertIn('main_bp.route("/privacy")', main_routes)
        self.assertIn('main_bp.route("/terms")', main_routes)
        self.assertTrue((ROOT / "templates/privacy.html").exists())
        self.assertTrue((ROOT / "templates/terms.html").exists())


if __name__ == "__main__":
    unittest.main()
