import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from models import RiseBadgeAssignment


ROOT = Path(__file__).resolve().parents[1]


class AdminChatPolishTests(unittest.TestCase):
    def test_expired_pins_are_cleared_before_chat_rendering(self):
        chat = (ROOT / "routes" / "chat.py").read_text()
        self.assertIn("def clear_expired_message_pins", chat)
        self.assertEqual(chat.count("    clear_expired_message_pins(messages)"), 2)
        template = (ROOT / "templates" / "chat.html").read_text()
        self.assertIn("position: sticky", (ROOT / "static" / "css" / "styles.css").read_text())
        self.assertIn("chat-date-separator", template)

    def test_badges_have_expiry_and_owner_can_delete_only_records(self):
        self.assertIn("expires_at", RiseBadgeAssignment.__table__.columns)
        moderation = (ROOT / "routes" / "moderation.py").read_text()
        self.assertIn('duration_days not in {"30", "180", "365"}', moderation)
        self.assertIn('action == "delete_report"', moderation)
        self.assertIn('action == "delete"', moderation)
        self.assertIn("Only the platform owner can permanently delete", moderation)

    def test_push_error_family_menu_reactions_and_crop_are_connected(self):
        api = (ROOT / "routes" / "api.py").read_text()
        self.assertIn("This affects both phones and computers", api)
        family = (ROOT / "templates" / "family_detail.html").read_text()
        self.assertNotIn('class="activity-filters"', family)
        self.assertIn("family-nav-more__menu", family)
        components = (ROOT / "templates" / "components" / "ui.html").read_text()
        self.assertIn("emotional_post.post_type == 'achievement'", components)
        script = (ROOT / "static" / "js" / "app.js").read_text()
        self.assertIn("data-image-crop-modal", script)
        self.assertIn("new DataTransfer()", script)

    def test_changed_templates_compile(self):
        environment = Environment(loader=FileSystemLoader(ROOT / "templates"))
        for name in (
            "base.html", "feed.html", "profile.html", "post_detail.html",
            "family_detail.html", "admin_users.html", "admin_families.html",
            "admin_reports.html", "admin_encouragement_reports.html", "admin_help.html",
        ):
            environment.get_template(name)


if __name__ == "__main__":
    unittest.main()
