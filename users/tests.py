from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ChangePasswordTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="old-password")
        self.url = reverse("change_password")

    def test_login_required_and_link_visible_to_owner(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.url), "Change password")

    def test_short_password_changes_and_signs_user_out(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            "current_password": "old-password",
            "new_password1": "x",
            "new_password2": "x",
        })
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("x"))
        self.assertFalse(self.client.login(username="owner", password="old-password"))
        self.assertTrue(self.client.login(username="owner", password="x"))

    def test_wrong_current_password_and_mismatch_do_not_change_password(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            "current_password": "wrong",
            "new_password1": "new",
            "new_password2": "different",
        })
        self.assertContains(response, "current password is incorrect")
        self.assertContains(response, "new passwords do not match")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_superuser_cannot_use_owner_password_form(self):
        admin = get_user_model().objects.create_superuser(username="admin", password="old-password")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(self.url).status_code, 403)
