import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EmotionalNavigationPolishTests(unittest.TestCase):
    def test_desktop_primary_navigation_is_visible_with_secondary_more_menu(self):
        base = (ROOT / "templates/base.html").read_text()
        css = (ROOT / "static/css/styles.css").read_text()
        self.assertIn('class="desktop-nav-more"', base)
        for label in ("For you", "People", "Families", "Messages", "Notifications", "Profile", "Goals"):
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

    def test_entry_and_feed_layouts_are_compact_and_distinctive(self):
        landing = (ROOT / "templates/landing.html").read_text()
        login = (ROOT / "templates/login.html").read_text()
        signup = (ROOT / "templates/signup.html").read_text()
        css = (ROOT / "static/css/styles.css").read_text()
        self.assertIn("connection-canvas", landing)
        self.assertIn("A calmer kind of social community", landing)
        self.assertIn("auth-shell--branded", login)
        self.assertIn("auth-shell--branded", signup)
        self.assertIn('body[data-page="main.home"][data-authenticated="1"] .panel-feed', css)
        self.assertIn("max-width: 620px", css)

    def test_entry_pages_fit_viewport_and_dark_heading_stays_readable(self):
        css = (ROOT / "static/css/styles.css").read_text()
        self.assertIn('body[data-page="main.home"][data-authenticated="0"]', css)
        self.assertIn("height: 100dvh", css)
        self.assertIn('body[data-page^="auth."]', css)
        self.assertIn(":root[data-theme=\"dark\"] .landing-copy h1", css)
        self.assertIn("color: #f2fffb", css)

    def test_desktop_feed_is_a_continuous_compact_stream(self):
        css = (ROOT / "static/css/styles.css").read_text()
        self.assertIn("border-width: 0 0 1px", css)
        self.assertIn("border-radius: 0", css)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", css)


if __name__ == "__main__":
    unittest.main()
