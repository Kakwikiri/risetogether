import unittest
from datetime import date, timedelta
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import (
    Family, FamilyMember, PointTransaction, ReferralCode, ReferralConversion,
    SiteSetting, User, UserActivityDay,
)
from referrals import process_referral_qualification, register_referral_signup


class ReferralRewardTests(unittest.TestCase):
    def make_app(self):
        app = Flask(__name__)
        app.config.update(
            SECRET_KEY="test",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        return app

    def create_tables(self):
        for table in (
            User.__table__, Family.__table__, FamilyMember.__table__,
            SiteSetting.__table__, ReferralCode.__table__,
            ReferralConversion.__table__, UserActivityDay.__table__,
            PointTransaction.__table__,
        ):
            table.create(db.engine)

    @patch("referrals.smart_notify")
    def test_family_referral_requires_three_distinct_days_and_cannot_repeat(self, _notify):
        app = self.make_app()
        with app.app_context():
            self.create_tables()
            inviter = User(username="inviter", email="inviter@example.com", password_hash="x")
            referred = User(username="newmember", email="new@example.com", password_hash="x")
            family = Family(name="Hope Family", member_limit=50, is_active=True)
            db.session.add_all((inviter, referred, family))
            db.session.flush()
            code = ReferralCode(inviter_id=inviter.id, family_id=family.id, token="safe-referral-token")
            db.session.add(code)
            db.session.flush()

            conversion = register_referral_signup(referred, code.token)
            db.session.flush()
            self.assertIsNotNone(conversion)
            self.assertIsNotNone(FamilyMember.query.filter_by(
                family_id=family.id, user_id=referred.id
            ).first())

            today = date.today()
            db.session.add_all([
                UserActivityDay(user_id=referred.id, activity_date=today - timedelta(days=offset))
                for offset in (0, 1)
            ])
            db.session.flush()
            self.assertFalse(process_referral_qualification(referred.id))
            self.assertEqual(PointTransaction.query.count(), 0)

            db.session.add(UserActivityDay(
                user_id=referred.id, activity_date=today - timedelta(days=2)
            ))
            db.session.flush()
            self.assertTrue(process_referral_qualification(referred.id))
            db.session.commit()
            self.assertEqual(
                sorted(row.amount for row in PointTransaction.query.all()), [20, 20]
            )
            self.assertFalse(process_referral_qualification(referred.id))
            self.assertEqual(PointTransaction.query.count(), 2)


if __name__ == "__main__":
    unittest.main()
