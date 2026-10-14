"""Tests for password sign-in (the register's PIN sign-in is tested in pos_server.tests.LockScreenTests)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("dana", email="Dana@example.com", password="secret-pass", is_staff=True)
        cls.customer = User.objects.create_user("cara", email="cara@example.com", password="secret-pass")

    def sign_in(self, username, password="secret-pass", **extra):
        return self.client.post(reverse("login_view"), {"username": username, "password": password, "remember": "1", **extra})

    def test_page(self):
        self.assertContains(self.client.get(reverse("login_view")), "PIN")

    def test_username_or_email(self):
        self.assertRedirects(self.sign_in("dana"), reverse("pos"), fetch_redirect_response=False)
        self.client.logout()
        self.assertRedirects(self.sign_in("dana@EXAMPLE.com"), reverse("pos"), fetch_redirect_response=False)

    def test_customers_go_to_the_store(self):
        self.assertRedirects(self.sign_in("cara"), reverse("store_home"), fetch_redirect_response=False)

    def test_wrong_password(self):
        response = self.sign_in("dana", "nope")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "password don't match", status_code=400)
        self.assertContains(response, 'value="dana"', status_code=400)

    def test_next_must_be_on_this_site(self):
        self.assertRedirects(self.sign_in("dana", next="/en/restaurant/drawer"), "/en/restaurant/drawer", fetch_redirect_response=False)
        self.client.logout()
        self.assertRedirects(self.sign_in("dana", next="https://evil.example/"), reverse("pos"), fetch_redirect_response=False)

    def test_not_staying_signed_in_ends_with_the_browser(self):
        self.client.post(reverse("login_view"), {"username": "dana", "password": "secret-pass"})
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.client.logout()
        self.sign_in("dana")
        self.assertFalse(self.client.session.get_expire_at_browser_close())
