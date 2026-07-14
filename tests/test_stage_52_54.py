import unittest
from pathlib import Path

from models import Message, Notification, Profile, ReturnCheckIn, ReturnSuggestionDismissal, User
from notifications_service import IMPORTANT_EVENT_CATEGORIES, NOTIFICATION_CATEGORIES


ROOT = Path(__file__).resolve().parents[1]


class FinalConnectionAndPushTests(unittest.TestCase):
    def test_activity_and_return_fields_are_additive(self):
        self.assertIn("last_active_at", User.__table__.c)
        self.assertIn("return_summary_since", User.__table__.c)
        self.assertIn("read_at", Message.__table__.c)
        self.assertIn("important", Notification.__table__.c)
        for field in ("checkin_suggestions_enabled", "miss_you_notifications_enabled", "return_summaries_enabled"):
            self.assertIn(field, Profile.__table__.c)

    def test_return_checkins_have_cascade_safety_and_dismissal_uniqueness(self):
        self.assertEqual(next(iter(ReturnCheckIn.__table__.c.sender_id.foreign_keys)).ondelete, "CASCADE")
        self.assertIn("uq_return_suggestion_dismissal", {item.name for item in ReturnSuggestionDismissal.__table__.constraints})

    def test_push_preferences_are_the_final_seven_categories(self):
        self.assertEqual(set(NOTIFICATION_CATEGORIES), {"message", "friends", "families", "support", "challenges", "reminders", "admin"})
        self.assertIn("message", IMPORTANT_EVENT_CATEGORIES)
        self.assertIn("return_checkin", IMPORTANT_EVENT_CATEGORIES)
        self.assertNotIn("reaction", IMPORTANT_EVENT_CATEGORIES)
        self.assertNotIn("family_chat", IMPORTANT_EVENT_CATEGORIES)

    def test_checkin_flow_is_private_truthful_and_rate_limited(self):
        source = (ROOT / "routes/main.py").read_text()
        for expected in (
            "trusted_connection_ids", "Block.query.filter", "timedelta(days=3)",
            "timedelta(days=7)", "recent_total >= 3", "miss_you_notifications_enabled",
        ):
            self.assertIn(expected, source)
        feed = (ROOT / "templates/feed.html").read_text()
        self.assertNotIn("last seen", feed.lower())
        self.assertIn("has been away for a few days", feed)

    def test_push_and_badging_fail_safely(self):
        app_js = (ROOT / "static/js/app.js").read_text()
        worker = (ROOT / "static/service-worker.js").read_text()
        self.assertIn('"setAppBadge" in navigator', app_js)
        self.assertIn('"clearAppBadge" in navigator', app_js)
        self.assertIn("/api/unread-counts", app_js)
        self.assertIn('event.notification.data && event.notification.data.url', worker)
        self.assertIn('targetUrl.origin !== self.location.origin', worker)
        self.assertIn('"setAppBadge" in self.registration', worker)

    def test_notification_permission_is_user_initiated(self):
        app_js = (ROOT / "static/js/app.js").read_text()
        permission_index = app_js.index("Notification.requestPermission()")
        click_index = app_js.rfind('pushEnable.addEventListener("click"', 0, permission_index)
        self.assertGreaterEqual(click_index, 0)


if __name__ == "__main__":
    unittest.main()
