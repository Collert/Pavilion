"""Tests for the admin portal (/admin/). Run with: venv\\Scripts\\python manage.py test admin_portal"""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gift_cards.models import GiftCard
from payments import square
from pos_server import refunds
from pos_server.models import CashDrawerSession, CashMovement, Order, OrderPayment, OrderStation, ReceiptLog, Refund
from pos_server.orders import create_order
from pos_server.tests import MenuFixture

from . import dashboard, reports
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


class ReportTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def at(self, day, hour, minute=0):
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))

    def test_month_lists_business_days_and_the_best_one(self):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        days = [month_start - datetime.timedelta(days=n) for n in (1, 2, 3)]  # the end of last month
        self.place([self.dish(self.burger)], self.at(days[0], 12), prep_minutes=5)
        self.place([self.dish(self.burger, 3)], self.at(days[2], 12, 5))
        self.place([self.dish(self.tea)], self.at(days[2], 12, 10))
        r = reports.month_report(days[0].year, days[0].month)
        self.assertEqual([row["day"] for row in r["rows"]], [days[0], days[2]])
        self.assertEqual(r["rows"][1]["busy"], "12:00 PM · 2 orders")
        self.assertEqual(r["rows"][1]["top"], "Burger")
        self.assertEqual(r["stats"][1]["value"], "2")
        self.assertEqual(r["stats"][3]["value"], Decimal("45.50"))
        kinds = {c["day"]: c["kind"] for c in r["cells"] if "day" in c}
        self.assertEqual(kinds[days[1]], "closed")  # between two business days, no orders
        self.assertEqual(kinds[days[2] - datetime.timedelta(days=1)], "out")  # before the first order ever
        self.assertTrue(next(c for c in r["cells"] if c.get("day") == days[2])["best"])

    def test_day_report_follows_recipes_and_splits_combos_by_station(self):
        day = timezone.localdate() - datetime.timedelta(days=1)
        self.place([self.dish(self.burger, 2)], self.at(day, 11, 50), prep_minutes=12)
        self.place([self.lunch(self.tea)], self.at(day, 12, 20), prep_minutes=4)
        r = reports.day_report(day)
        sold = {d["name"]: d["qty"] for d in r["dishes"]}
        self.assertEqual(sold, {"Burger": 2, "Lunch combo": 1})
        self.assertEqual(r["items_made"], 4)  # 2 burgers, then a burger and a tea from the combo
        stations = {s["name"]: s for s in r["stations"]}
        self.assertEqual(stations[self.kitchen.name]["items"], 3)
        self.assertEqual(stations[self.bar.name]["items"], 1)
        # The combo's $16.50 splits by the picks' own prices: burger 14.50, tea 2.00
        self.assertEqual(stations[self.bar.name]["sales"].quantize(Decimal("0.01")), Decimal("2.00"))
        patties = next(c for c in r["components"] if c["name"] == "Patty")
        self.assertEqual((patties["qty"], patties["used_in"]), ("3", "1 dish"))
        self.assertTrue(next(p for p in r["prep"] if p["time"].startswith("11:45"))["over"])
        csv = reports.day_csv(r)
        self.assertIn("Lunch combo,Combo,1,16.50", csv)
        self.assertIn("Patty,3.0,ea", csv)

    def test_report_pages_and_csv_download(self):
        day = timezone.localdate() - datetime.timedelta(days=2)
        self.place([self.dish(self.coffee)], self.at(day, 9))
        self.client.force_login(self.owner)
        for url in (reverse("admin_reports"), reverse("admin_reports") + "?view=calendar&month=1999-13",
                    reverse("admin_report_day", args=[day.isoformat()])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertRedirects(self.client.get(reverse("admin_report_last")), reverse("admin_report_day", args=[day.isoformat()]))
        response = self.client.get(reverse("admin_report_csv", args=[day.isoformat()]))
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(f"-{day.isoformat()}.csv", response["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("admin_report_csv", args=["nope"])).status_code, 404)


@override_settings(TAX_RATE="0.05")
class RefundTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.drawer = CashDrawerSession.objects.create(opening_amount=Decimal("100"))

    def rung_up(self, lines, payments):
        order = create_order(lines=lines, channel="store", initial_status=OrderStation.STATUS_APPROVED, payments=payments,
                             drawer=self.drawer, cashier=self.owner)
        order.payments.filter(method=OrderPayment.CARD).update(square_payment_id="sq-pay-1", card_brand="VISA", card_last4="4242")
        return order

    def test_an_item_refund_includes_its_share_of_tax(self):
        order = self.rung_up([self.dish(self.burger, 2), self.dish(self.coffee)], [{"method": "card", "amount": "33.08"}])
        self.assertEqual(order.total, Decimal("33.08"))
        burger = order.items.get(title="Burger")
        amount, _lines = refunds.quote(order, {burger.id: 1})
        self.assertEqual(amount, Decimal("15.23"))  # 14.50 + 5% tax

    @mock.patch("pos_server.refunds.square.refund_payment", return_value="sq-refund-1")
    def test_card_refunds_go_through_square_then_the_rest(self, refund_payment):
        order = self.rung_up([self.dish(self.burger, 2)], [{"method": "card", "amount": "30.45"}])
        burger = order.items.get()
        first = refunds.refund_order(order, picks={burger.id: 1}, reason="Quality issue", to=Refund.ORIGINAL, user=self.owner)
        refund_payment.assert_called_once_with("sq-pay-1", Decimal("15.23"), f"refund-{first.id}-{order.payments.get().id}", "Quality issue")
        self.assertEqual(first.parts[0]["label"], "Visa •••• 4242")
        self.assertEqual(refunds.status(order), "partial")
        whole = refunds.refund_order(order, whole=True, reason="Wrong order", to=Refund.ORIGINAL, user=self.owner)
        self.assertEqual(whole.amount, Decimal("15.22"))  # what's left, rounding included
        self.assertEqual(refunds.status(order), "refunded")
        with self.assertRaises(refunds.RefundError):
            refunds.quote(order, whole=True)

    def test_cash_refunds_come_out_of_an_open_drawer(self):
        order = self.rung_up([self.dish(self.tea)], [{"method": "cash", "amount": "2.10", "tendered": "5"}])
        with self.assertRaises(refunds.RefundError):  # cash can't go back to "the original payment"
            refunds.refund_order(order, whole=True, reason="Wrong order", to=Refund.ORIGINAL, user=self.owner)
        refunds.refund_order(order, whole=True, reason="Wrong order", to=Refund.CASH, user=self.owner)
        movement = CashMovement.objects.get(drawer=self.drawer)
        self.assertEqual(movement.amount, Decimal("-2.10"))
        self.drawer.closed_at = timezone.now()
        self.drawer.save()
        order2 = self.rung_up([self.dish(self.tea)], [{"method": "card", "amount": "2.10"}])
        with self.assertRaisesMessage(refunds.RefundError, "Open a cash drawer"):
            refunds.refund_order(order2, whole=True, reason="Wrong order", to=Refund.CASH, user=self.owner)

    def test_gift_card_payments_are_put_back_on_the_card(self):
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        order = create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED,
                             payments=[], gift_card_payments=[(card.number, "2.10")], drawer=self.drawer)
        refunds.refund_order(order, whole=True, reason="Changed their mind", to=Refund.ORIGINAL, user=self.owner)
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("20.00"))

    @mock.patch("pos_server.refunds.square.refund_payment", side_effect=square.SquareError("declined"))
    def test_a_square_refusal_records_nothing(self, refund_payment):
        order = self.rung_up([self.dish(self.tea)], [{"method": "card", "amount": "2.10"}])
        with self.assertRaisesMessage(refunds.RefundError, "Square refused"):
            refunds.refund_order(order, whole=True, reason="Wrong order", to=Refund.ORIGINAL, user=self.owner)
        self.assertFalse(Refund.objects.exists())

    def test_card_details_come_from_the_terminal_checkout(self):
        order = create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED,
                             payments=[{"method": "card", "amount": "2.10", "checkoutId": "chk-9"}], drawer=self.drawer)
        payment = order.payments.get()
        self.assertEqual(payment.square_checkout_id, "chk-9")

        def ok(body):
            return mock.Mock(is_success=lambda: True, body=body)

        with mock.patch.object(square, "square_client") as client:
            client.terminal.get_terminal_checkout.return_value = ok({"checkout": {"payment_ids": ["pay-7"]}})
            client.payments.get_payment.return_value = ok({"payment": {"card_details": {
                "entry_method": "CONTACTLESS", "card": {"card_brand": "MASTERCARD", "last_4": "8810"}}}})
            square.fill_card_details(payment)
        payment.refresh_from_db()
        self.assertEqual((payment.square_payment_id, payment.card_label, payment.entry_method), ("pay-7", "Mastercard •••• 8810", "tap"))


class OrderPageTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.client.force_login(self.owner)
        self.order = self.place([self.dish(self.burger), self.dish(self.coffee)], timezone.now() - datetime.timedelta(minutes=5))

    def test_list_filters_and_search(self):
        older = self.place([self.dish(self.tea)], timezone.now() - datetime.timedelta(days=3))
        today = self.client.get(reverse("admin_orders")).content.decode()
        self.assertIn(f"#{self.order.id}<", today)
        self.assertNotIn(f"#{older.id}<", today)
        week = self.client.get(reverse("admin_orders") + f"?date=week&q={older.id}").content.decode()
        self.assertIn(f"#{older.id}<", week)
        self.assertNotIn(f"#{self.order.id}<", week)

    def test_order_page_and_its_sheets(self):
        url = reverse("admin_order", args=[self.order.id])
        item = self.order.items.first()
        for query in ("", "?sheet=send", "?sheet=refund", f"?sheet=refund&mode=items&q_{item.id}=1&reason=Long+wait"):
            self.assertEqual(self.client.get(url + query).status_code, 200, query)
        self.assertContains(self.client.get(url + "?sheet=send"), "Soon")  # the not-yet channels are placeholders

    def test_staff_note_and_print(self):
        self.client.post(reverse("admin_order_note", args=[self.order.id]), {"note": "Regular. Medium-well."})
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_note, "Regular. Medium-well.")
        response = self.client.post(reverse("admin_order_print", args=[self.order.id]))
        self.assertRedirects(response, reverse("admin_order_receipt", args=[self.order.id]))
        self.assertEqual(ReceiptLog.objects.get().channel, ReceiptLog.PRINT)

    def test_sending_a_receipt(self):
        url = reverse("admin_order_send", args=[self.order.id])
        self.client.post(url, {"to": "not an email"})
        self.client.post(url, {"to": "jo@example.com"})  # listmonk isn't configured in tests: nothing is sent
        self.assertFalse(ReceiptLog.objects.exists())
        with mock.patch("admin_portal.views.send_email", return_value=True) as send:
            self.client.post(url, {"to": "jo@example.com"})
        kind, to, data = send.call_args.args
        self.assertEqual((kind, to, data["number"]), ("receipt", "jo@example.com", self.order.id))
        self.assertEqual(ReceiptLog.objects.get().to, "jo@example.com")
        self.order.refresh_from_db()
        self.assertEqual(self.order.email, "jo@example.com")

    def test_refund_form(self):
        url = reverse("admin_order_refund", args=[self.order.id])
        response = self.client.post(url, {"mode": "whole", "reason": "", "to": "cash"})
        self.assertIn("sheet=refund", response["Location"])
        CashDrawerSession.objects.create(opening_amount=Decimal("50"))
        self.client.post(url, {"mode": "whole", "reason": "Wrong order", "to": "cash"})
        self.assertEqual(Refund.objects.get().amount, self.order.total)


class UnitTests(TestCase):
    def test_big_numbers_show_in_the_bigger_unit(self):
        self.assertEqual(fmt_qty(2500, "g"), "2.5 kg")
        self.assertEqual(fmt_qty(400, "ml"), "400 ml")
        self.assertEqual(fmt_qty(12000, "ml"), "12 L")
        self.assertEqual(fmt_qty(12, "ea"), "12 ea")
