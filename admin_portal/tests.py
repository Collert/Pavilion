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
from pos_server import dish_info
from pos_server.models import (CashDrawerSession, CashMovement, Combo, ComponentIngredient, Dish, DishComponent,
                               Ingredient, Menu, MenuRule, Option, OptionGroup, Order, OrderPayment, OrderStation, ReceiptLog, Refund)
from pos_server.orders import collect_order, create_order
from pos_server.pricing import price_cart
from pos_server.tests import MenuFixture

from . import combos, dashboard, dishes, reports
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


class MenuEditorTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.client.force_login(self.owner)

    def open(self, menu, tab="items"):
        """The editor's context, as GET shows it."""
        return self.client.get(reverse("admin_menu", args=[menu.id]) + f"?tab={tab}").context

    def post(self, menu, context, op, **fields):
        """Post the editor back: its state, plus `fields` and the button pressed. Returns the response."""
        return self.client.post(reverse("admin_menu", args=[menu.id]), {"state": context["state"], "op": op, **fields})

    def test_new_menus_start_as_drafts_and_publish_on_save(self):
        response = self.client.post(reverse("admin_menu_new"))
        menu = Menu.objects.latest("id")
        self.assertRedirects(response, reverse("admin_menu", args=[menu.id]))
        self.assertTrue(menu.draft)
        context = self.open(menu, "schedule")
        context = self.post(menu, context, "manual").context
        self.assertFalse(Menu.objects.get(pk=menu.pk).manual_on)  # not saved yet
        self.assertEqual(context["save_status"], "Unsaved changes")
        self.post(menu, context, "save", title="Brunch")
        menu.refresh_from_db()
        self.assertEqual((menu.title, menu.draft, menu.manual_on), ("Brunch", False, True))
        self.assertTrue(menu.is_live())

    def test_items_toggle_filter_and_add_all_shown(self):
        menu = Menu.objects.create(title="Empty")
        context = self.post(menu, self.open(menu), "toggle:d:%d" % self.burger.id).context
        context = self.post(menu, context, "toggle:c:%d" % self.combo.id).context
        self.assertEqual((context["d"]["dishes"], context["d"]["combos"]), ([self.burger.id], [self.combo.id]))
        context = self.post(menu, context, "group:%d" % self.bar.id).context
        context = self.post(menu, context, "preview", q="tea").context
        self.assertEqual([r["obj"] for r in context["c"]["rows"]], [self.tea])
        context = self.post(menu, context, "add_shown").context
        self.post(menu, context, "save")
        self.assertEqual(set(menu.dishes.all()), {self.burger, self.tea})
        self.assertEqual(list(menu.combos.all()), [self.combo])

    def test_schedule_rules_are_saved_together(self):
        menu = Menu.objects.create(title="Brunch")
        context = self.post(menu, self.open(menu, "schedule"), "auto").context
        self.assertEqual([(r["kind"], r["days"], r["time"]) for r in context["d"]["rules"]], [("on", "01234", "11:00"), ("off", "01234", "15:00")])
        context = self.post(menu, context, "day:0:5").context
        context = self.post(menu, context, "add_rule:off:dates", **{"rule-0-time": "09:30"}).context
        dates = len(context["d"]["rules"]) - 1
        context = self.post(menu, context, "preview", **{f"rule-{dates}-start": "2026-12-26", f"rule-{dates}-end": "2026-12-24"}).context
        self.assertEqual(MenuRule.objects.filter(menu=menu).count(), 0)  # nothing saved yet
        self.post(menu, context, "save")
        menu.refresh_from_db()
        self.assertTrue(menu.auto)
        on = menu.rules.get(kind="on")
        self.assertEqual((on.days, on.time), ("012345", datetime.time(9, 30)))
        span = menu.rules.get(type="dates")
        self.assertEqual((span.start_date, span.end_date), (datetime.date(2026, 12, 24), datetime.date(2026, 12, 26)))

    def test_display_pages_slots_look_and_timing(self):
        menu = self.menu
        context = self.open(menu, "display")
        v = context["v"]
        self.assertEqual(v["template"].name, "menu_showcase")
        self.assertEqual(v["d"]["pages"][0]["slots"], [f"s:{self.kitchen.id}", f"s:{self.bar.id}", "combos"])  # filled in order
        context = self.post(menu, context, "add_page").context
        self.assertEqual(context["d"]["ui"]["page"], 1)
        context = self.post(menu, context, "preview", **{"slot-0": "combos", "featured": f"d:{self.burger.id}", "look-present": "1",
                                                          "look-accent": "#123456", "look-badge": "Open late", "look-loved_count": "9"}).context
        page = context["d"]["display"]["pages"][1]
        self.assertEqual((page["slots"][0], page["featured"]), ("combos", f"d:{self.burger.id}"))
        look = context["d"]["display"]["look"]
        self.assertEqual((look["accent"], look["badge"], look["loved_count"]), ("#123456", "Open late", 3))  # 9 is out of range
        self.assertContains(self.post(menu, context, "preview"), "Open late")  # the preview is the real board
        # A group can't take longer to turn over than its page (the bar's 10 drinks need two screens)
        for n in range(8):
            drink = Dish.objects.create(title=f"Drink {n}", price=Decimal("3.00"), station=self.bar)
            context = self.post(menu, context, f"toggle:d:{drink.id}").context
        context = self.post(menu, context, "tab:display").context
        context = self.post(menu, context, "preview", page_sec="10", group_sec="20").context
        self.assertTrue(context["bad_timing"])
        self.assertFalse(context["can_save"])
        context = self.post(menu, context, "sec:group:-15").context
        self.assertFalse(context["bad_timing"])
        self.post(menu, context, "save")
        menu.refresh_from_db()
        self.assertEqual((len(menu.display["pages"]), menu.display["group_sec"], menu.display["look"]["accent"]), (2, 5, "#123456"))

    def test_leave_delete_and_priority_arrows(self):
        first = Menu.objects.get()  # the fixture's menu
        second = Menu.objects.create(title="Second", position=1, manual_on=True)
        self.assertRedirects(self.post(second, self.open(second), "leave"), reverse("admin_menus"))
        self.assertTrue(self.post(second, self.open(second), "leave", title="Changed").context["leave_ask"])
        self.client.post(reverse("admin_menu_action", args=[second.id]), {"action": "up"})
        self.assertEqual(list(Menu.objects.all()), [second, first])
        self.assertEqual(Menu.active(), second)
        self.assertContains(self.client.get(reverse("admin_menus")), "Showcase")  # the Display column
        self.assertRedirects(self.post(second, self.open(second), "delete"), reverse("admin_menus"))
        self.assertFalse(Menu.objects.filter(pk=second.pk).exists())

    def test_broken_state_falls_back_to_the_saved_menu(self):
        response = self.client.post(reverse("admin_menu", args=[self.menu.id]), {"state": "{nope", "op": "preview"})
        self.assertEqual(response.context["d"]["title"], "Test menu")


def form_fields(draft, **changes):
    """The dish editor's POST fields for a draft (admin_portal.dishes.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "price", "description", "ticket_name", "prep_minutes", "station", "availability", "catering_min")}
    data["menus"] = [str(m) for m in draft["menus"]]
    if draft["catering"]:
        data["catering"] = "1"
    data["tier-count"] = len(draft["tiers"])
    for i, t in enumerate(draft["tiers"]):
        data[f"tier-{i}-quantity"], data[f"tier-{i}-price"] = t["quantity"], t["price"]
    data["part-count"] = len(draft["parts"])
    for i, p in enumerate(draft["parts"]):
        data.update({f"part-{i}-id": p["id"], f"part-{i}-component": p["component"], f"part-{i}-quantity": p["quantity"]})
        if p["adjustable"]:
            data[f"part-{i}-adjustable"] = "1"
    data["group-count"] = len(draft["groups"])
    for i, g in enumerate(draft["groups"]):
        data.update({f"group-{i}-id": g["id"], f"group-{i}-title": g["title"], f"group-{i}-pick": g["pick"],
                     f"group-{i}-opt-count": len(g["options"])})
        if g["required"]:
            data[f"group-{i}-required"] = "1"
        if g["addons"]:
            data[f"group-{i}-addons"] = "1"
        for j, o in enumerate(g["options"]):
            data.update({f"group-{i}-opt-{j}-id": o["id"], f"group-{i}-opt-{j}-title": o["title"], f"group-{i}-opt-{j}-price": o["price"],
                         f"group-{i}-opt-{j}-component": o["component"], f"group-{i}-opt-{j}-quantity": o["quantity"]})
    return data


class DishTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()
        flour = Ingredient.objects.create(title="Flour", unit_of_measurement="g", allergens=["Gluten"], cost=Decimal("0.002"))
        beef = Ingredient.objects.create(title="Beef", unit_of_measurement="g", meat=True, cost=Decimal("0.015"))
        cheese = Ingredient.objects.create(title="Cheese", unit_of_measurement="g", allergens=["Dairy"], cost=Decimal("0.02"))
        ComponentIngredient.objects.create(component=cls.bun, ingredient=flour, quantity=60)  # $0.12 a bun
        ComponentIngredient.objects.create(component=cls.patty, ingredient=beef, quantity=120)  # $1.80 a patty
        ComponentIngredient.objects.create(component=cls.onion, ingredient=cheese, quantity=10)  # $0.20 (a cheesy onion)

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, dish):
        return dishes.draft_from_dish(dishes.editor_queryset().get(pk=dish.pk))

    def post(self, dish, op, **changes):
        return self.client.post(reverse("admin_dish", args=[dish.id]), {**form_fields(self.draft(dish), **changes), "op": op})

    def test_cost_allergens_and_diets_come_from_the_recipe(self):
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertEqual(dish_info.plate_cost(burger), Decimal("2.12"))
        self.assertEqual(dish_info.food_cost_pct(Decimal("2.12"), burger.price), 14.6)
        self.assertEqual(dish_info.dish_allergens(burger), ["Gluten", "Dairy"])
        self.assertEqual(dish_info.dish_diets(burger), [])  # meat, gluten, dairy
        self.assertIsNone(dish_info.plate_cost(self.tea))  # no recipe
        self.assertEqual(dish_info.dish_diets(self.tea), [])
        # Dairy makes it an animal product: vegetarian and pescatarian, not vegan
        DishComponent.objects.filter(dish=burger, component__in=[self.patty, self.bun]).delete()
        self.assertEqual([d["key"] for d in dish_info.dish_diets(burger)], ["vegetarian", "pescatarian", "gluten_free"])

    def test_list_filters_and_searches(self):
        page = self.client.get(reverse("admin_dishes")).content.decode()
        self.assertIn("Burger", page)
        self.assertIn("Gluten · Dairy", page)
        self.assertIn("No recipe", page)  # tea
        self.assertEqual(self.client.get(reverse("admin_dishes") + "?show=nomenu").context["l"]["sections"], [])
        self.menu.dishes.remove(self.tea)
        rows = [r["dish"] for s in self.client.get(reverse("admin_dishes") + "?show=nomenu").context["l"]["sections"] for r in s["rows"]]
        self.assertEqual(rows, [self.tea])
        found = self.client.get(reverse("admin_dishes") + "?q=coff").context["l"]["sections"]
        self.assertEqual([r["dish"] for s in found for r in s["rows"]], [self.coffee])

    def test_nothing_is_saved_until_save(self):
        response = self.post(self.burger, "preview", title="Smash burger", price="15.25")
        e = response.context["e"]
        self.assertEqual((e["status"], e["can_save"]), ("Unsaved changes", True))
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).title, "Burger")
        response = self.post(self.burger, "save", title="Smash burger", price="15.25", ticket_name="smash", prep_minutes="7",
                             station=str(self.bar.id), availability="always", menus=[])
        self.assertRedirects(response, reverse("admin_dish", args=[self.burger.id]))
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertEqual((burger.title, burger.price, burger.ticket_name, burger.prep_minutes, burger.station, burger.availability),
                         ("Smash burger", Decimal("15.25"), "SMASH", 7, self.bar, "always"))
        self.assertFalse(burger.menu.exists())
        self.assertEqual(self.client.get(reverse("admin_dish", args=[self.burger.id])).context["e"]["status"], "All changes saved")

    def test_a_dish_needs_a_name_and_a_price(self):
        for changes in ({"title": "  "}, {"price": "0"}, {"price": "abc"}):
            response = self.post(self.burger, "save", **changes)
            self.assertEqual(response.status_code, 200, changes)
            self.assertFalse(response.context["e"]["can_save"])
            self.assertEqual(response.context["e"]["status"], "Needs a name and a price")
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).title, "Burger")

    def test_rows_are_added_and_removed_in_the_draft(self):
        e = self.post(self.burger, "catering").context["e"]
        self.assertTrue(e["d"]["catering"])
        self.assertEqual(e["d"]["tiers"], [{"quantity": "10", "price": "13.25"}])  # 92% of retail, to the quarter
        draft = e["d"]
        data = {**form_fields(draft), "op": "add_tier"}
        draft = self.client.post(reverse("admin_dish", args=[self.burger.id]), data).context["e"]["d"]
        self.assertEqual(draft["tiers"][1], {"quantity": "20", "price": "12.75"})
        data = {**form_fields(draft), "op": "add_group", "add_component": str(self.avocado.id)}
        draft = self.client.post(reverse("admin_dish", args=[self.burger.id]), data).context["e"]["d"]
        self.assertEqual([g["title"] for g in draft["groups"]], ["New group", "Add-ons"])  # Add-ons stays last
        self.assertEqual(draft["parts"][-1]["component"], str(self.avocado.id))
        data = {**form_fields(draft), "op": "remove_part:0"}
        draft = self.client.post(reverse("admin_dish", args=[self.burger.id]), data).context["e"]["d"]
        self.assertNotIn(str(self.bun.id), [p["component"] for p in draft["parts"]])
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).dishcomponent_set.count(), 3)  # still unsaved

    def test_save_writes_catering_recipe_and_modifiers(self):
        draft = self.draft(self.burger)
        draft["catering"], draft["catering_min"] = True, "12"
        draft["tiers"] = [{"quantity": "25", "price": "11.95"}, {"quantity": "10", "price": "13.25"}]
        draft["parts"] = [p for p in draft["parts"] if p["component"] != str(self.onion.id)]
        draft["parts"][0]["quantity"] = "2"
        draft["parts"].append({"id": "", "component": str(self.avocado.id), "quantity": "1", "adjustable": True})
        draft["groups"].insert(0, {"id": "", "title": "Doneness", "pick": "one", "required": True, "addons": False, "options": [
            {"id": "", "title": "Medium", "price": "0", "component": "", "quantity": "1"},
            {"id": "", "title": "Well done", "price": "0", "component": str(self.onion.id), "quantity": "2"}]})
        # Adding an add-on picks a component: it's named and priced after it
        draft = self.client.post(reverse("admin_dish", args=[self.burger.id]),
                                 {**form_fields(draft), "op": "preview", "group-1-add": str(self.patty.id)}).context["e"]["d"]
        self.assertEqual(draft["groups"][1]["options"][-1], {"id": "", "title": "Patty", "price": "4.00", "component": str(self.patty.id), "quantity": "1"})
        self.client.post(reverse("admin_dish", args=[self.burger.id]), {**form_fields(draft), "op": "save"})
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertTrue(burger.catering)
        self.assertEqual(burger.catering_price(11), Decimal("13.25"))
        self.assertEqual(burger.catering_price(30), Decimal("11.95"))
        self.assertEqual([(dc.component, dc.quantity, dc.adjustable) for dc in burger.dishcomponent_set.all()],
                         [(self.bun, 2, True), (self.patty, 1, True), (self.avocado, 1, True)])
        self.assertTrue(Option.objects.filter(pk=self.avocado_addon.pk).exists())  # kept, not recreated
        groups = {g.title: g for g in burger.option_groups.all()}
        self.assertEqual([(o.title, o.component) for o in groups["Add-ons"].options.all()], [("Avocado", self.avocado), ("Patty", self.patty)])
        self.assertTrue(groups["Add-ons"].addons)
        self.assertEqual((groups["Doneness"].options.last().component, groups["Doneness"].options.last().quantity), (self.onion, 2))
        self.assertEqual((groups["Doneness"].pick, groups["Doneness"].required, groups["Doneness"].default_option().title),
                         ("one", True, "Medium"))
        notes = self.client.get(reverse("admin_dish", args=[self.burger.id])).context["e"]["catering"]["notes"]
        self.assertEqual(notes, ["The first break starts below the minimum order of 12."])

    def test_leaving_asks_only_with_unsaved_changes(self):
        self.assertRedirects(self.post(self.burger, "leave"), reverse("admin_dishes"))
        response = self.post(self.burger, "leave", title="Changed")
        self.assertTrue(response.context["e"]["leave_ask"])
        self.assertContains(response, "You have unsaved changes to this dish.")

    def test_duplicate_archive_and_restore(self):
        response = self.post(self.burger, "duplicate", title="Burger deluxe")
        copy = Dish.objects.latest("id")
        self.assertRedirects(response, reverse("admin_dish", args=[copy.id]))
        self.assertEqual((copy.title, copy.menu.count()), ("Burger deluxe (copy)", 0))
        self.assertEqual(copy.dishcomponent_set.count(), 3)
        self.assertEqual(copy.option_groups.get(addons=True).options.get().component, self.avocado)
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).title, "Burger")  # the original is untouched
        self.assertRedirects(self.post(self.burger, "archive"), reverse("admin_dishes"))
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertTrue(burger.archived)
        self.assertFalse(burger.available)
        self.assertFalse(burger.menu.exists())
        listed = [r["dish"] for s in self.client.get(reverse("admin_dishes")).context["l"]["sections"] for r in s["rows"]]
        self.assertNotIn(burger, listed)
        self.post(self.burger, "restore")
        self.assertFalse(Dish.objects.get(pk=self.burger.pk).archived)

    def test_every_dish_has_an_add_ons_group(self):
        draft = self.draft(self.tea)
        self.assertEqual([(g["title"], g["addons"], g["options"]) for g in draft["groups"]], [("Add-ons", True, [])])
        self.post(self.tea, "remove_group:0")  # can't be removed
        self.post(self.tea, "save", title="Green tea")
        self.assertFalse(OptionGroup.objects.filter(dish=self.tea).exists())  # an empty one isn't stored
        addons = self.draft(self.tea)["groups"][0]
        addons["options"].append({"id": "", "title": "Honey", "price": "0.50", "component": "", "quantity": "1"})
        response = self.post(self.tea, "save", groups=[addons])
        self.assertEqual(response.context["e"]["status"], "Every add-on needs a component")

    def test_new_dish_and_editor_render(self):
        response = self.client.post(reverse("admin_dish_new"))
        dish = Dish.objects.latest("id")
        self.assertRedirects(response, reverse("admin_dish", args=[dish.id]))
        for target in (dish, self.burger, self.coffee):
            self.assertEqual(self.client.get(reverse("admin_dish", args=[target.id])).status_code, 200)

    def test_ticket_name_shows_on_kitchen_tickets(self):
        Dish.objects.filter(pk=self.burger.pk).update(ticket_name="BRGR")
        order = self.place([MenuFixture.dish(self.burger), MenuFixture.dish(self.tea)], timezone.now())
        names = [d["name"] for d in collect_order(Order.objects.get(pk=order.pk))["dishes"]]
        self.assertEqual(sorted(names), ["BRGR", "Tea"])

    def test_last_week_counts_combos_in_sold_only(self):
        now = timezone.now()
        self.place([MenuFixture.dish(self.coffee, qty=2)], now)
        self.place([self.lunch(self.coffee)], now)
        week = self.client.get(reverse("admin_dish", args=[self.coffee.id])).context["e"]["week"]
        self.assertEqual(week["sold"], 3)
        self.assertEqual(week["sales"], Decimal("5.00"))


def combo_fields(draft, **changes):
    """The combo editor's POST fields for a draft (admin_portal.combos.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "ticket_name", "description", "pricing", "price", "percent_off")}
    data["menus"] = [str(m) for m in draft["menus"]]
    if draft["active"]:
        data["active"] = "1"
    data["slot-count"] = len(draft["slots"])
    for i, s in enumerate(draft["slots"]):
        data.update({f"slot-{i}-id": s["id"], f"slot-{i}-title": s["title"], f"slot-{i}-picks": s["picks"],
                     f"slot-{i}-default": s["default"], f"slot-{i}-opt-count": len(s["options"])})
        if s["required"]:
            data[f"slot-{i}-required"] = "1"
        for j, o in enumerate(s["options"]):
            data.update({f"slot-{i}-opt-{j}-id": o["id"], f"slot-{i}-opt-{j}-dish": o["dish"], f"slot-{i}-opt-{j}-price": o["price"]})
    return data


class ComboTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self):
        return combos.draft_from_combo(Combo.objects.get(pk=self.combo.pk))

    def post(self, draft, op, **extra):
        return self.client.post(reverse("admin_combo", args=[self.combo.id]), {**combo_fields(draft), "op": op, **extra})

    def test_list_kpis_and_filters(self):
        self.place([self.lunch(self.tea)], timezone.now())  # tea is a 0.50 upgrade
        self.place([self.lunch()], timezone.now())
        data = self.client.get(reverse("admin_combos")).context["l"]
        row = data["rows"][0]
        self.assertEqual((row["stats"]["sold"], row["stats"]["upcharges"], row["sold_sub"]), (2, Decimal("0.50"), "50% upgraded"))
        self.assertEqual(row["p"]["ala"], Decimal("17.00"))  # burger + coffee bought separately
        self.assertEqual(row["p"]["save"], Decimal("1.00"))
        self.assertEqual(row["price_sub"], "up to $16.50")
        self.assertEqual([k["value"] for k in data["kpis"]], ["2", "$1.00", "$0.50", "Lunch combo"])
        Combo.objects.filter(pk=self.combo.pk).update(active=False)
        self.assertEqual(self.client.get(reverse("admin_combos") + "?show=active").context["l"]["rows"], [])
        self.assertEqual(len(self.client.get(reverse("admin_combos") + "?q=tea").context["l"]["rows"]), 1)  # by dish name

    def test_editor_draft_and_save(self):
        draft = self.draft()
        e = self.post(draft, "add_slot").context["e"]
        self.assertEqual(len(e["d"]["slots"]), 3)
        self.assertEqual(e["status"], "2 things to fix")  # no name, no dish
        draft = e["d"]
        draft["slots"][2]["title"] = "Treat"
        draft["slots"][2]["required"] = False
        e = self.post(draft, "preview", **{"slot-2-add": str(self.tea.id)}).context["e"]
        self.assertEqual(e["d"]["slots"][2]["options"][0]["dish"], str(self.tea.id))
        self.assertTrue(e["can_save"])
        draft = e["d"]
        draft["slots"][2]["options"][0]["price"] = "1.25"
        draft["slots"][1]["default"] = str(self.tea.id)
        draft["ticket_name"] = "lunch"
        self.assertEqual(Combo.objects.get(pk=self.combo.pk).slots.count(), 2)  # nothing saved yet
        self.assertRedirects(self.post(draft, "save"), reverse("admin_combo", args=[self.combo.id]))
        combo = Combo.objects.get(pk=self.combo.pk)
        self.assertEqual(combo.ticket_name, "LUNCH")
        treat = combo.slots.get(title="Treat")
        self.assertEqual((treat.required, treat.options.get().price), (False, Decimal("1.25")))
        self.assertEqual(combo.slots.get(title="Drink").default_option().dish, self.tea)
        self.assertEqual(price_cart([{"type": "combo", "id": combo.id, "qty": 1, "slots": {}}]).subtotal, Decimal("16.50"))

    def test_steppers_and_moves(self):
        draft = self.draft()
        e = self.post(draft, "picks_more:1").context["e"]
        self.assertEqual(e["d"]["slots"][1]["picks"], "2")  # the drink has two dishes
        e = self.client.post(reverse("admin_combo", args=[self.combo.id]), {**combo_fields(e["d"]), "op": "picks_more:1"}).context["e"]
        self.assertEqual(e["d"]["slots"][1]["picks"], "2")  # not more than its dishes
        e = self.client.post(reverse("admin_combo", args=[self.combo.id]), {**combo_fields(e["d"]), "op": "slot_up:1"}).context["e"]
        self.assertEqual([s["title"] for s in e["d"]["slots"]], ["Drink", "Main"])
        e = self.client.post(reverse("admin_combo", args=[self.combo.id]), {**combo_fields(e["d"]), "op": "remove_option:0:0"}).context["e"]
        self.assertEqual([o["dish"] for o in e["d"]["slots"][0]["options"]], [str(self.tea.id)])
        self.assertEqual(e["d"]["slots"][0]["default"], str(self.tea.id))  # the default moves on
        self.assertEqual(e["d"]["slots"][0]["picks"], "1")

    def test_percent_pricing_and_problems(self):
        draft = self.draft()
        e = self.post(draft, "preview", pricing="percent", percent_off="10").context["e"]
        self.assertEqual(e["p"]["combo"], Decimal("15.30"))  # (14.50 + 2.50 coffee) less 10%
        e = self.post(draft, "save", price="0").context["e"]
        self.assertFalse(e["can_save"])
        self.assertEqual(Combo.objects.get(pk=self.combo.pk).price, Decimal("16.00"))

    def test_new_duplicate_leave_and_archive(self):
        response = self.client.post(reverse("admin_combo_new"))
        new = Combo.objects.latest("id")
        self.assertRedirects(response, reverse("admin_combo", args=[new.id]))
        self.assertEqual([s.title for s in new.slots.all()], ["Main", "Side", "Drink"])
        self.assertFalse(new.active)
        self.assertRedirects(self.post(self.draft(), "leave"), reverse("admin_combos"))
        self.assertTrue(self.post(self.draft(), "leave", title="Changed").context["e"]["leave_ask"])
        response = self.post(self.draft(), "duplicate")
        copy = Combo.objects.latest("id")
        self.assertRedirects(response, reverse("admin_combo", args=[copy.id]))
        self.assertEqual((copy.title, copy.active, copy.slots.count()), ("Lunch combo (copy)", False, 2))
        self.assertRedirects(self.post(self.draft(), "archive"), reverse("admin_combos"))
        combo = Combo.objects.get(pk=self.combo.pk)
        self.assertTrue(combo.archived)
        self.assertFalse(combo.available)
        self.assertFalse(combo.menu.exists())
        self.assertEqual(self.client.get(reverse("admin_combo", args=[combo.id])).status_code, 200)


class UnitTests(TestCase):
    def test_big_numbers_show_in_the_bigger_unit(self):
        self.assertEqual(fmt_qty(2500, "g"), "2.5 kg")
        self.assertEqual(fmt_qty(400, "ml"), "400 ml")
        self.assertEqual(fmt_qty(12000, "ml"), "12 L")
        self.assertEqual(fmt_qty(12, "ea"), "12 ea")
