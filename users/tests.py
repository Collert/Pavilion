"""Tests for password sign-in (the register's PIN sign-in is tested in pos_server.tests.LockScreenTests)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
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


@override_settings(SETUP_REDIRECT=True)
class SetupTests(TestCase):
    """First-time setup (users/setup.py, users.views.setup): until there's an account, every page asks for one."""
    def setUp(self):
        from django.core.cache import cache
        from users import setup
        cache.delete_many([setup.DONE_KEY, setup.CODE_KEY])

    def place(self, **fields):
        return self.client.post(reverse("first_setup"), {"op": "next", "name": "Juniper Lane", "phone": "604 555 0123",
                                                         "address": "1200 Main St", "city": "Vancouver, BC",
                                                         "time_zone": "America/Vancouver", "tax_rate": "5", **fields})

    def you(self, **fields):
        from users import setup
        return self.client.post(reverse("first_setup"), {"op": "finish", "first": "Priya", "last": "Shah",
                                                         "email": "Priya@Example.com", "password1": "a-long-kitchen-pass",
                                                         "password2": "a-long-kitchen-pass", "pin": "2468",
                                                         "code": setup.setup_code().lower(), **fields})

    def test_every_page_goes_to_setup_until_there_is_an_account(self):
        self.assertRedirects(self.client.get("/en/"), reverse("first_setup"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("login_view")), reverse("first_setup"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("first_setup")).status_code, 200)
        self.assertNotEqual(self.client.get("/static/users/setup.css").status_code, 302)  # files aren't sent there

    def test_setting_up_makes_the_owner_and_the_store(self):
        from admin_portal.models import StoreSettings
        from users.models import GroupInfo
        self.assertContains(self.place(name=""), "The store needs a name.", status_code=400)
        self.assertContains(self.place(), "Your account")
        self.assertContains(self.you(code="WRONG-CODE"), "not the setup code", status_code=400)
        self.assertContains(self.you(password2="something-else"), "don’t match", status_code=400)
        self.assertContains(self.you(pin="12"), "4 digits", status_code=400)
        self.assertRedirects(self.you(), reverse("admin_home"), fetch_redirect_response=False)
        owner = User.objects.get()
        self.assertEqual((owner.username, owner.first_name, owner.is_superuser, owner.is_staff), ("priya@example.com", "Priya", True, True))
        self.assertTrue(owner.check_password("a-long-kitchen-pass"))
        self.assertTrue(owner.groups.filter(info__in=GroupInfo.objects.filter(owners=True)).exists())
        self.assertTrue(owner.staff_profile.check_pin("2468"))
        store = StoreSettings.objects.get()
        self.assertEqual((store.name, store.address, store.tax_rate, store.email), ("Juniper Lane", "1200 Main St", Decimal("5"), "priya@example.com"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), owner.id)  # signed in
        self.assertEqual(self.client.get("/en/").status_code, 200)  # no more setup
        self.assertRedirects(self.client.get(reverse("first_setup")), reverse("login_view"), fetch_redirect_response=False)

    def test_back_keeps_the_place(self):
        self.place()
        response = self.client.post(reverse("first_setup"), {"op": "back"})
        self.assertContains(response, 'value="Juniper Lane"')
        self.assertContains(response, "Step 1 of 2")
