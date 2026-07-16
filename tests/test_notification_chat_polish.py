import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NotificationChatPolishTests(unittest.TestCase):
    def test_push_activation_validates_server_keys_and_recovers_old_subscription(self):
        api = (ROOT / "routes/api.py").read_text()
        app_js = (ROOT / "static/js/app.js").read_text()
        self.assertIn("len(decoded) == 65 and decoded[0] == 4", api)
        self.assertIn("Phone notifications are not configured correctly", api)
        self.assertIn("keyChanged", app_js)
        self.assertIn("await subscription.unsubscribe()", app_js)
        self.assertIn("navigator.serviceWorker.ready", app_js)

    def test_open_chat_suppresses_alert_and_marks_message_read(self):
        chat = (ROOT / "routes/chat.py").read_text()
        socket_js = (ROOT / "static/js/socket.js").read_text()
        self.assertIn("user_has_open_chat", chat)
        self.assertIn("message.read_at = datetime.utcnow()", chat)
        self.assertIn('@socketio.on("mark_messages_read")', chat)
        self.assertIn("isOpenPrivateConversation", socket_js)
        self.assertIn('socket.emit("mark_messages_read"', socket_js)

    def test_message_reactions_update_without_page_reload_and_can_notify_offline(self):
        chat = (ROOT / "routes/chat.py").read_text()
        socket_js = (ROOT / "static/js/socket.js").read_text()
        self.assertIn('category="message_reaction"', chat)
        self.assertIn('"message_reaction_updated"', chat)
        self.assertIn('[data-message-reaction-form]', socket_js)
        self.assertIn('socket.on("message_reaction_updated"', socket_js)

    def test_inbox_unread_state_and_audio_quality_controls_are_present(self):
        messages = (ROOT / "templates/messages.html").read_text()
        socket_js = (ROOT / "static/js/socket.js").read_text()
        self.assertIn("item.unread", messages)
        self.assertIn("message-unread-count", messages)
        for setting in ("echoCancellation", "noiseSuppression", "autoGainControl"):
            self.assertIn(setting, socket_js)
        self.assertIn("audioBitsPerSecond: 96000", socket_js)


if __name__ == "__main__":
    unittest.main()
