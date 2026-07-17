import os
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ownership import is_platform_owner_username, platform_owner_username


ROOT = Path(__file__).resolve().parents[1]


class OwnerFamilyControlsTests(unittest.TestCase):
    def test_owner_identity_is_case_insensitive_and_configurable(self):
        previous = os.environ.get("PLATFORM_SUPER_ADMIN_USERNAME")
        try:
            os.environ["PLATFORM_SUPER_ADMIN_USERNAME"] = "Kakwikiri"
            self.assertEqual(platform_owner_username(), "kakwikiri")
            self.assertTrue(is_platform_owner_username("KAKWIKIRI"))
            self.assertFalse(is_platform_owner_username("another-admin"))
        finally:
            if previous is None:
                os.environ.pop("PLATFORM_SUPER_ADMIN_USERNAME", None)
            else:
                os.environ["PLATFORM_SUPER_ADMIN_USERNAME"] = previous

    def test_role_changes_and_owner_deletion_are_server_protected(self):
        moderation = (ROOT / "routes" / "moderation.py").read_text()
        main = (ROOT / "routes" / "main.py").read_text()
        app = (ROOT / "app.py").read_text()
        role_route = moderation[moderation.index("def set_website_role"):moderation.index("def admin_user_action")]
        self.assertIn("if not is_platform_owner(current_user)", role_route)
        self.assertIn('new_role not in {"", "moderator", "admin"}', role_route)
        self.assertIn("if is_platform_owner(current_user)", main[main.index("def delete_account"):])
        self.assertIn("protect_risetogether_owner_delete", app)
        self.assertNotIn("WITH first_admin AS", app)

    def test_family_and_profile_templates_compile(self):
        environment = Environment(loader=FileSystemLoader(ROOT / "templates"))
        for template_name in ("admin_users.html", "admin_families.html", "family_detail.html", "edit_profile.html"):
            environment.get_template(template_name)

        family_template = (ROOT / "templates" / "family_detail.html").read_text()
        self.assertIn('class="family-tabbar-secondary"', family_template)
        self.assertNotIn('class="family-nav-more"', family_template)
        self.assertIn(">Verify</a>", family_template)
        self.assertIn("Trusted Family verification", (ROOT / "templates" / "admin_families.html").read_text())


if __name__ == "__main__":
    unittest.main()
