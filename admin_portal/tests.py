"""Tests for the admin portal (/admin/). Run with: venv\\Scripts\\python manage.py test admin_portal"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pos_server.models import CashDrawerSession, Order, OrderStation
from pos_server.orders import create_order
from pos_server.tests import MenuFixture

from . import dashboard
from .models import StoreSettings
from .units import fmt_qty


class AdminFixture(MenuFixture):
    @classmethod
    def build_admin(cls):
        cls.build_menu()
        cls.owner = User.objects.create_superuser("owner", password="x", first_name="Priya", last_name="Shah")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.customer = User.objects.create_user("customer", password="x")

    def place(self, lines, when, channel="store", prep_minutes=None):
        """An order placed at `when` (aware datetime), finished if `prep_minutes` is given."""
        order = create_order(lines=lines, channel=channel, initial_status=OrderStation.STATUS_APPROVED,
                             verify_payment=lambda remaining: None)
        fields = {"timestamp": when, "start_time": when}
        if prep_minutes is not None:
            fields.update(picked_up=True, prep_time=datetime.timedelta(minutes=prep_minutes))
        Order.objects.filter(pk=order.pk).update(**fields)
        return order


class AccessTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def test_only_superusers_get_in(self):
        for user in (self.customer, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("admin_home")).status_code, 302, user.username)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("admin_home")).status_code, 200)

    def test_rail_shows_the_store_name_with_pavilion_under_it(self):
        settings = StoreSettings.load()
        settings.name = "Corner Café"
        settings.save()
        self.client.force_login(self.owner)
        html = self.client.get(reverse("admin_home")).content.decode()
        self.assertIn('<span class="rail-store">Corner Café</span>', html)
        self.assertIn('<span class="rail-sub">Pavilion</span>', html)
        self.assertIn("Priya Shah", html)

    def test_every_range_renders(self):
        self.client.force_login(self.owner)
        for query in ("", "?range=7d", "?range=30d&open=1", "?range=nonsense"):
            self.assertEqual(self.client.get(reverse("admin_home") + query).status_code, 200, query)


class DashboardTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        # A Thursday, mid-afternoon (local time)
        self.now = timezone.make_aware(datetime.datetime(2026, 10, 22, 15, 30))

    def at(self, days_ago, hour, minute=0):
        day = self.now.date() - datetime.timedelta(days=days_ago)
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))

    def test_today_compares_with_the_same_weekday_up_to_now(self):
        self.place([self.dish(self.burger, 2)], self.at(0, 9), prep_minutes=6)   # 29.00 today
        self.place([self.dish(self.coffee)], self.at(7, 10), prep_minutes=4)     # 2.50 last week, before now
        self.place([self.dish(self.burger)], self.at(7, 18))                      # after "now": not in the KPI
        data = dashboard.dashboard("today", now=self.now)
        sales, orders, avg, ticket = data["kpis"]
        self.assertEqual(sales["value"], "$29")
        self.assertEqual(sales["delta"], "1060.0%")
        self.assertEqual(sales["tone"], "good")
        self.assertEqual(orders["value"], "1")
        self.assertEqual(ticket["value"], "6m 00s")
        self.assertEqual(ticket["tone"], "bad")  # slower than last week
        # The chart still shows all of last week's day, and nothing yet for later today
        hours = {h["title"].split(" · ")[0]: h for h in data["chart"]["hits"]}
        self.assertEqual(hours["6 PM"]["prev"], "$15")
        self.assertEqual(hours["6 PM"]["cur"], "Not yet")

    def test_open_days_only_skips_days_without_orders(self):
        for days_ago in (0, 1, 3, 4, 8, 9):
            self.place([self.dish(self.tea)], self.at(days_ago, 12))
        calendar = dashboard.dashboard("7d", now=self.now)
        open_only = dashboard.dashboard("7d", open_only=True, now=self.now)
        self.assertEqual(sum(1 for b in calendar["chart"]["bars"] if b.get("closed")), 3)
        self.assertFalse(any(b.get("closed") for b in open_only["chart"]["bars"]))
        # The 7 open days before today's: 6 days with orders, all in range
        self.assertEqual(open_only["kpis"][1]["value"], "6")

    def test_top_sellers_and_channel_mix(self):
        self.place([self.dish(self.burger, 3), self.dish(self.coffee)], self.at(0, 9))
        self.place([self.lunch()], self.at(0, 10), channel="web")
        data = dashboard.dashboard("today", now=self.now)
        self.assertEqual([(s["name"], s["n"]) for s in data["sellers"]], [("Burger", 3), ("Coffee", 1), ("Lunch combo", 1)])
        mix = {m["label"]: m for m in data["mix"]}
        self.assertEqual(mix["In person"]["money"], Decimal("46.00"))
        self.assertEqual(mix["Online pickup"]["pct"], 26)

    def test_alerts_for_low_stock_and_drawer_differences(self):
        self.avocado.par = 30
        self.avocado.save()
        CashDrawerSession.objects.create(opening_amount=Decimal("100"), expected_amount=Decimal("150"),
                                         counted_amount=Decimal("145.75"), closed_at=self.now - datetime.timedelta(hours=2))
        titles = [a["title"] for a in dashboard.dashboard("today", now=self.now)["alerts"]]
        self.assertIn("Avocado below par", titles)
        self.assertIn("Drawer counted $4.25 short", titles)

    def test_live_panel_counts_open_orders_by_channel(self):
        self.place([self.dish(self.burger)], self.now - datetime.timedelta(minutes=10))
        self.place([self.dish(self.coffee)], self.now - datetime.timedelta(minutes=4), channel="web")
        live = dashboard.dashboard("today", now=self.now)["live"]
        self.assertEqual(live["open"], 2)
        self.assertEqual({c["label"]: c["n"] for c in live["channels"]}["Online pickup"], 1)


class UnitTests(TestCase):
    def test_big_numbers_show_in_the_bigger_unit(self):
        self.assertEqual(fmt_qty(2500, "g"), "2.5 kg")
        self.assertEqual(fmt_qty(400, "ml"), "400 ml")
        self.assertEqual(fmt_qty(12000, "ml"), "12 L")
        self.assertEqual(fmt_qty(12, "ea"), "12 ea")
