import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EmotionalNavigationPolishTests(unittest.TestCase):
    def test_desktop_primary_navigation_is_visible_with_secondary_more_menu(self):
        base = (ROOT / "templates/base.html").read_text()
        css = (ROOT / "static/css/styles.css").read_text()
        self.assertIn('class="desktop-nav-more"', base)
        for label in ("Feed", "People", "Families", "Messages", "Notifications", "Profile"):
            self.assertIn(f"<span>{label}</span>", base)
        self.assertIn("@media (min-width: 901px)", css)
        self.assertIn(".desktop-nav-more-panel", css)

    def test_message_name_and_row_open_the_conversation(self):
        template = (ROOT / "templates/messages.html").read_text()
        app_js = (ROOT / "static/js/app.js").read_text()
        self.assertIn("data-conversation-url", template)
        self.assertIn("message-profile-link", template)
        self.assertIn("url_for('chat.direct_chat', user_id=item.user.id)", template)
        self.assertIn('querySelectorAll("[data-conversation-url]")', app_js)

    def test_post_purposes_have_matching_emotional_reactions(self):
        routes = (ROOT / "routes/main.py").read_text()
        components = (ROOT / "templates/components/ui.html").read_text()
        for purpose in ("feeling", "humour", "encouragement", "advice", "listen", "practical_help", "celebrating"):
            self.assertIn(purpose, routes + components)
        for label in ("Made me smile", "That was funny", "Thinking with you", "Here with you", "Sending strength"):
            self.assertIn(label, components)

    def test_daily_checkin_support_updates_real_impact_and_confirmation(self):
        routes = (ROOT / "routes/main.py").read_text()
        self.assertIn("You encouraged {supported_name} today.", routes)
        self.assertIn("current_user.checkin_responses", routes)
        self.assertIn('url_for("main.impact")', routes)

    def test_flash_feedback_floats_and_entry_pages_show_legal_links(self):
        css = (ROOT / "static/css/styles.css").read_text()
        app_js = (ROOT / "static/js/app.js").read_text()
        self.assertIn(".flash-container {\n  position: fixed;", css)
        self.assertIn("[data-flash-alert]", app_js)
        for template_name in ("landing.html", "login.html", "signup.html"):
            template = (ROOT / "templates" / template_name).read_text()
            self.assertIn("main.privacy_policy", template)
            self.assertIn("main.terms_of_use", template)


if __name__ == "__main__":
    unittest.main()
