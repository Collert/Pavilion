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



class AccessTests(TestCase):
    """users/access.py: codes from groups, superusers have them all."""

    def test_codes_come_from_groups(self):
        from django.contrib.auth.models import Group
        from . import access
        from .models import GroupInfo
        self.assertEqual(access.tidy(["admin.dishes.delete", "nonsense", "app.register"]),
                         ["app.register", "admin.dishes.view", "admin.dishes.delete"])  # view comes with delete
        self.assertEqual(access.page_levels("reports"), ("view",))
        dana = User.objects.create_user("dana2", is_staff=True)
        self.assertFalse(access.can(dana, "app.register"))
        bar = Group.objects.create(name="Bar")
        GroupInfo.objects.create(group=bar, perms=["app.register", "admin.dishes.view"])
        dana.groups.add(bar)
        dana = User.objects.get(pk=dana.pk)
        self.assertTrue(access.can(dana, "app.register"))
        self.assertFalse(access.can_page(dana, "dishes"))  # pages need the Admin app too
        boss = User.objects.create_superuser("boss2", password="x")
        self.assertTrue(access.can_page(boss, "groups", "delete"))
        self.assertFalse(access.can_page(boss, "reports", "edit"))  # reports have no edit
        dana.is_active = False
        self.assertFalse(access.can(dana, "app.register"))  # turned off: nothing

    def test_the_usual_groups_start_with_sensible_permissions(self):
        from .models import GroupInfo
        perms = {i.group.name: set(i.perms) for i in GroupInfo.objects.select_related("group")}
        self.assertIn("app.admin", perms["Managers"])
        self.assertNotIn("admin.groups.edit", perms["Managers"])
        self.assertIn("app.register", perms["Front of house"])
        self.assertNotIn("act.discount", perms["Front of house"])
        self.assertEqual(perms["Delivery"], {"app.courier", "app.audio"})
