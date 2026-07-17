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
        self.assertIn("queue_device_push(notification.id)", chat)

    def test_people_states_and_received_video_download_are_explicit(self):
        main = (ROOT / "routes/main.py").read_text()
        people = (ROOT / "templates/people.html").read_text()
        chat = (ROOT / "templates/chat.html").read_text()
        self.assertIn("friendship_states=friendship_states", main)
        for label in ("Friend", "Pending", "Add friend"):
            self.assertIn(label, people)
        self.assertIn("message.sender_id != current_user.id and not message.view_once", chat)
        self.assertIn("Download video", chat)

    def test_push_activation_moves_management_to_settings(self):
        messages = (ROOT / "templates/messages.html").read_text()
        settings = (ROOT / "templates/settings.html").read_text()
        app_js = (ROOT / "static/js/app.js").read_text()
        chat_routes = (ROOT / "routes/chat.py").read_text()
        self.assertNotIn("data-push-enable", messages)
        self.assertIn('id="device-notifications"', settings)
        self.assertIn('button.textContent = "Enabled"', app_js)
        self.assertIn('dedupe_key=f"device-notifications:', chat_routes)
        self.assertIn('url_for("main.settings") + "#device-notifications"', chat_routes)

    def test_friend_point_gifts_and_family_certificate_upgrade_are_connected(self):
        main = (ROOT / "routes/main.py").read_text()
        points = (ROOT / "templates/point_history.html").read_text()
        upgrades = (ROOT / "family_upgrades.py").read_text()
        certificate = (ROOT / "templates/components/ui.html").read_text()
        self.assertIn("def gift_friend_points", main)
        self.assertIn("users_are_friends(current_user.id, recipient.id)", main)
        self.assertIn("spend_personal_points(", main)
        self.assertIn('source_type="friend_point_gift_received"', main)
        self.assertIn("Share points with a friend", points)
        self.assertIn('"celebration_certificates"', upgrades)
        for style in ("certificate_sunrise", "certificate_unity", "certificate_excellence", "certificate_legacy"):
            self.assertIn(style, upgrades)
        self.assertIn("achievement-post-card--decorated", certificate)

    def test_people_can_remove_an_accepted_friend_with_matching_controls(self):
        main = (ROOT / "routes/main.py").read_text()
        people = (ROOT / "templates/people.html").read_text()
        self.assertIn('def remove_friend(user_id):', main)
        self.assertIn('FriendRequest.status == "accepted"', main)
        self.assertIn("Remove friend", people)
        self.assertIn("connection-button", people)

    def test_inbox_unread_state_and_audio_quality_controls_are_present(self):
        messages = (ROOT / "templates/messages.html").read_text()
        socket_js = (ROOT / "static/js/socket.js").read_text()
        self.assertIn("item.unread", messages)
        self.assertIn("message-unread-count", messages)
        for setting in ("echoCancellation", "noiseSuppression", "autoGainControl"):
            self.assertIn(setting, socket_js)
        self.assertIn("audioBitsPerSecond: 96000", socket_js)

    def test_profile_privacy_saves_immediately_and_view_once_is_per_recipient(self):
        main = (ROOT / "routes/main.py").read_text()
        edit_profile = (ROOT / "templates/edit_profile.html").read_text()
        app_js = (ROOT / "static/js/app.js").read_text()
        chat = (ROOT / "routes/chat.py").read_text()
        socket_js = (ROOT / "static/js/socket.js").read_text()
        self.assertIn('def update_profile_privacy():', main)
        self.assertIn("data-profile-privacy-list", edit_profile)
        self.assertIn("Saving privacy choice", app_js)
        viewed_route = chat[chat.index("def viewed_once_message"):chat.index("def forward_message")]
        self.assertIn("MessageDeletion", viewed_route)
        self.assertNotIn("db.session.delete(message)", viewed_route)
        self.assertIn('frame.closest(".chat-message")', socket_js)
        self.assertIn("def paged_chat_messages", chat)


if __name__ == "__main__":
    unittest.main()
