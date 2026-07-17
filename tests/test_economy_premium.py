import unittest
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask

from extensions import db
from family_upgrades import family_has_upgrade, upgrade_can_be_targeted, upgrade_is_available
from feature_flags import feature_flag_key
from models import (
    Family, FamilyUpgradePurchase, PointTransaction, PremiumSubscription,
    SiteSetting, User, VerificationApplication,
)
from premium import (
    active_family_subscription, active_user_subscription, family_has_premium,
    subscription_is_active, upload_limit_for, user_has_premium,
)


ROOT = Path(__file__).resolve().parents[1]


class EconomyPremiumTests(unittest.TestCase):
    def make_app(self):
        app = Flask(__name__)
        app.config.update(
            SECRET_KEY="test",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            IMAGE_UPLOAD_LIMIT=5 * 1024 * 1024,
            VIDEO_UPLOAD_LIMIT=25 * 1024 * 1024,
            FILE_UPLOAD_LIMIT=10 * 1024 * 1024,
            PREMIUM_IMAGE_UPLOAD_LIMIT=15 * 1024 * 1024,
            PREMIUM_VIDEO_UPLOAD_LIMIT=75 * 1024 * 1024,
            PREMIUM_FILE_UPLOAD_LIMIT=30 * 1024 * 1024,
        )
        db.init_app(app)
        return app

    def create_economy_tables(self):
        for table in (
            User.__table__, Family.__table__, SiteSetting.__table__,
            PremiumSubscription.__table__, FamilyUpgradePurchase.__table__,
            PointTransaction.__table__, VerificationApplication.__table__,
        ):
            table.create(db.engine)

    def test_active_expired_and_cancelled_premium_entitlements(self):
        app = self.make_app()
        with app.app_context():
            self.create_economy_tables()
            active_user = User(username="active", email="active@example.com", password_hash="x")
            expired_user = User(username="expired", email="expired@example.com", password_hash="x")
            family = Family(name="Hope", owner_id=None, member_limit=50)
            db.session.add_all((active_user, expired_user, family))
            db.session.flush()
            active = PremiumSubscription(
                user_id=active_user.id, plan="personal", billing_period="monthly",
                status="active", expires_at=datetime.utcnow() + timedelta(days=10),
            )
            expired = PremiumSubscription(
                user_id=expired_user.id, plan="personal", billing_period="monthly",
                status="active", expires_at=datetime.utcnow() - timedelta(seconds=1),
            )
            family_subscription = PremiumSubscription(
                family_id=family.id, plan="family", billing_period="yearly",
                status="active", expires_at=datetime.utcnow() + timedelta(days=200),
            )
            db.session.add_all((active, expired, family_subscription))
            db.session.commit()
            self.assertTrue(subscription_is_active(active))
            self.assertFalse(subscription_is_active(expired))
            self.assertIsNotNone(active_user_subscription(active_user.id))
            self.assertIsNone(active_user_subscription(expired_user.id))
            self.assertIsNotNone(active_family_subscription(family.id))
            self.assertTrue(user_has_premium(active_user))
            self.assertFalse(user_has_premium(expired_user))
            self.assertTrue(family_has_premium(family))
            self.assertEqual(PointTransaction.query.count(), 0)

    def test_premium_limits_and_flags_do_not_create_competitive_rewards(self):
        app = self.make_app()
        with app.app_context():
            self.create_economy_tables()
            user = User(username="supporter", email="supporter@example.com", password_hash="x")
            family = Family(name="Together", member_limit=50)
            db.session.add_all((user, family))
            db.session.flush()
            db.session.add_all((
                PremiumSubscription(user_id=user.id, plan="personal", billing_period="monthly", status="active"),
                PremiumSubscription(family_id=family.id, plan="family", billing_period="monthly", status="active"),
            ))
            db.session.commit()
            self.assertEqual(upload_limit_for("video", user), 75 * 1024 * 1024)
            self.assertTrue(family_has_upgrade(family.id, "custom_banner"))
            self.assertFalse(family_has_upgrade(family.id, "challenge_slots"))
            db.session.add(SiteSetting(key=feature_flag_key("premium_challenges"), value="true"))
            db.session.commit()
            self.assertTrue(family_has_upgrade(family.id, "challenge_slots"))
            self.assertEqual(PointTransaction.query.count(), 0)

    def test_family_can_save_for_an_upgrade_before_reaching_its_level(self):
        app = self.make_app()
        with app.app_context():
            self.create_economy_tables()
            family = Family(name="Growing Together", member_limit=50)
            db.session.add(family)
            db.session.commit()
            self.assertTrue(upgrade_can_be_targeted(family, "custom_banner"))
            self.assertFalse(upgrade_is_available(family, "custom_banner"))
            db.session.add(PointTransaction(
                family_id=family.id,
                amount=100,
                reason="Verified Family progress",
                source_type="test_progress",
                source_id=family.id,
                transaction_kind="award",
                unique_reward_key=f"test-family-level:{family.id}",
            ))
            db.session.commit()
            self.assertTrue(upgrade_is_available(family, "custom_banner"))

    def test_subscription_and_verification_constraints_are_auditable(self):
        subscription_constraints = {item.name for item in PremiumSubscription.__table__.constraints}
        application_constraints = {item.name for item in VerificationApplication.__table__.constraints}
        self.assertIn("ck_premium_subscription_one_subject", subscription_constraints)
        self.assertIn("ck_premium_subscription_period", subscription_constraints)
        self.assertIn("ck_verification_application_one_subject", application_constraints)
        self.assertIn("ck_verification_application_status", application_constraints)

    def test_economy_ui_never_claims_payment_is_live(self):
        premium = (ROOT / "templates/premium.html").read_text()
        checkout = (ROOT / "templates/premium_checkout.html").read_text()
        economy = (ROOT / "templates/admin_economy.html").read_text()
        routes = (ROOT / "routes/moderation.py").read_text()
        self.assertIn("Activity earns rewards", premium)
        self.assertIn("No payment has been taken", checkout)
        self.assertIn("Premium does not guarantee verification", (ROOT / "routes/main.py").read_text())
        self.assertIn("Economy Dashboard", economy)
        self.assertIn("require_admin_role(\"super_admin\")", routes)


if __name__ == "__main__":
    unittest.main()
