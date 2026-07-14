import unittest
from pathlib import Path

from models import Appreciation, MessageReaction, Post, PostSupportResponse


ROOT = Path(__file__).resolve().parents[1]


class ConnectionHistoryIntegrationTests(unittest.TestCase):
    def test_new_history_tables_preserve_unique_human_actions(self):
        self.assertIn("uq_appreciation_response_sender", {item.name for item in Appreciation.__table__.constraints})
        self.assertIn("uq_message_reaction_user", {item.name for item in MessageReaction.__table__.constraints})
        self.assertIn("uq_post_support_action_user", {item.name for item in PostSupportResponse.__table__.constraints})
        self.assertEqual(Post.__table__.c.purpose.default.arg, "normal")

    def test_people_suggestions_are_private_and_truthful(self):
        source = (ROOT / "routes/main.py").read_text()
        self.assertIn('EncouragementRequest.visibility == "identity"', source)
        self.assertIn("~EncouragementRequest.responses.any()", source)
        self.assertIn("EncouragementRequest.family_id.in_(memberships)", source)

    def test_listening_requires_acceptance_before_chat(self):
        source = (ROOT / "routes/main.py").read_text()
        self.assertIn('response.status = "pending" if action == "listen"', source)
        self.assertIn('decision not in {"accept", "decline"}', source)
        self.assertIn('url_for("chat.direct_chat"', source)

    def test_action_responses_require_explanations(self):
        source = (ROOT / "routes/main.py").read_text()
        self.assertIn('action in {"idea", "may_help"} and not explanation', source)
        template = (ROOT / "templates/components/ui.html").read_text()
        self.assertIn('name="explanation" maxlength="1000" required', template)


if __name__ == "__main__":
    unittest.main()
