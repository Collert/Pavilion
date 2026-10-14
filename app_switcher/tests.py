import uuid

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from pos_server.devices import DEVICE_COOKIE, set_device_cookie
from pos_server.models import EligibleDevice


@override_settings(ENFORCE_DEVICE_CHECK=False)
class AppSwitcherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("dana", password="x", is_staff=True, first_name="Dana")
        cls.boss = User.objects.create_user("boss", password="x", is_staff=True, is_superuser=True)
        cls.customer = User.objects.create_user("cust", password="x")

    def test_staff_only(self):
        url = reverse("app_switcher_index")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.customer)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_apps_by_role(self):
        self.client.force_login(self.staff)
        titles = [str(app["title"]) for app in self.client.get(reverse("app_switcher_index")).context["apps"]]
        self.assertIn("Order marking", titles)
        self.assertNotIn("Admin", titles)
        self.client.force_login(self.boss)
        titles = [str(app["title"]) for app in self.client.get(reverse("app_switcher_index")).context["apps"]]
        self.assertIn("Admin", titles)

    def test_barely_touches_the_database(self):
        self.client.force_login(self.staff)
        with self.assertNumQueries(2):  # the session and the user
            response = self.client.get(reverse("app_switcher_index"))
        self.assertContains(response, "Dana")
        self.assertContains(response, "This device")

        device = EligibleDevice.objects.create(name="Front counter", token=uuid.uuid4())
        signed = set_device_cookie(HttpResponse(), device.token).cookies[DEVICE_COOKIE].value
        self.client.cookies[DEVICE_COOKIE] = signed
        with self.assertNumQueries(3):  # plus the device's name
            response = self.client.get(reverse("app_switcher_index"))
        self.assertContains(response, "Front counter")


@override_settings(ENFORCE_DEVICE_CHECK=False)
class RailFooterTests(TestCase):
    """Every staff screen ends its rail the same way: Apps, Lock (register screens), the clock."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("dana", password="x", is_staff=True)

    def positions(self, name):
        self.client.force_login(self.staff)
        html = self.client.get(reverse(name)).content.decode()
        nav = html[html.index('class="rail"'):html.index("</nav>")]
        return {key: nav.find(marker) for key, marker in [
            ("apps", f'href="{reverse("app_switcher_index")}"'),
            ("lock", f'action="{reverse("register_lock")}"'),
            ("clock", 'id="rail-time"'),
        ]}

    def test_register_screens_have_lock_between_apps_and_clock(self):
        for name in ("pos", "cash_drawer"):
            at = self.positions(name)
            self.assertTrue(0 <= at["apps"] < at["lock"] < at["clock"], (name, at))

    def test_kitchen_display_has_apps_and_clock(self):
        at = self.positions("order_marking")
        self.assertEqual(at["lock"], -1)
        self.assertTrue(0 <= at["apps"] < at["clock"])
