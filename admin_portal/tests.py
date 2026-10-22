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
from pos_server.models import (CashDrawerSession, CashMovement, Combo, Component, ComponentIngredient, Condiment, Disposable, Extra, Dish, DishComponent,
                               Ingredient, Menu, MenuRule, Option, OptionGroup, Order, OrderPayment, OrderStation, ReceiptLog, Refund, Station, StorageLocation, Supplier)
from pos_server.orders import collect_order, create_order
from pos_server.pricing import price_cart
from pos_server.tests import MenuFixture

from catering.models import CateringOrder, CateringSettings
from catering.tests import CateringFixture

from . import catering, combos, components, condiments, dashboard, dishes, disposables, ingredients, recipes, reports, restocks, services
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
        data.update({f"part-{i}-id": p["id"], f"part-{i}-component": p["component"], f"part-{i}-quantity": p["quantity"],
                     f"part-{i}-label": p.get("label", ""), f"part-{i}-choice-count": len(p.get("choices", []))})
        for j, ch in enumerate(p.get("choices", [])):
            data.update({f"part-{i}-choice-{j}-id": ch["id"], f"part-{i}-choice-{j}-component": ch["component"], f"part-{i}-choice-{j}-price": ch["price"]})
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


def recipe_fields(draft, **changes):
    """The recipe editor's POST fields for a draft (admin_portal.recipes.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    total = int(draft["total"] or 0)
    data = {"title": draft["title"], "target": draft["target"], "yield": draft["yield"], "total_h": total // 60, "total_m": total % 60,
            "sel": draft["sel"], "ing-count": len(draft["ings"]), "step-count": len(draft["steps"])}
    for i, g in enumerate(draft["ings"]):
        data.update({f"ing-{i}-id": g["id"], f"ing-{i}-kind": g["kind"], f"ing-{i}-item": g["item"], f"ing-{i}-qty": g["qty"]})
    for i, st in enumerate(draft["steps"]):
        data.update({f"step-{i}-id": st["id"], f"step-{i}-title": st["title"], f"step-{i}-text": st["text"],
                     f"step-{i}-scene": st["scene"], f"step-{i}-mins": st["mins"]})
        if st["timer"]:
            data[f"step-{i}-timer"] = "1"
    return data


class RecipeTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        from inventory.models import Recipe, RecipeIngredient, RecipeStep, Transformation
        cls.build_admin()
        cls.flour = Ingredient.objects.create(title="Flour", unit_of_measurement="g", inventory=5000)
        cls.dough = Recipe.objects.create(title="Bun dough", yield_quantity=24, total_minutes=90)
        RecipeIngredient.objects.create(recipe=cls.dough, ingredient=cls.flour, quantity=1000)
        RecipeStep.objects.create(recipe=cls.dough, title="Mix", scene="prep")
        RecipeStep.objects.create(recipe=cls.dough, title="Rise", scene="rise", duration=datetime.timedelta(minutes=60), position=1)
        Component.objects.filter(pk=cls.bun.pk).update(recipe=cls.dough)
        cls.frozen = Component.objects.create(title="Bun, frozen", unit_of_measurement="ea", crafting_option="transform")
        cls.freeze = Transformation.objects.create(name="Freeze buns", from_component=cls.bun, to_component=cls.frozen,
                                                   duration=datetime.timedelta(hours=1))
        cls.loose = Recipe.objects.create(title="Spare sauce", yield_quantity=5)

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, recipe):
        from inventory.models import Recipe
        return recipes.draft_from_recipe(Recipe.objects.get(pk=recipe.pk))

    def post(self, recipe, op, draft=None, **extra):
        return self.client.post(reverse("admin_recipe", args=[recipe.id]), {**recipe_fields(draft or self.draft(recipe)), "op": op, **extra})

    def test_list_sections_and_filters(self):
        data = self.client.get(reverse("admin_recipes")).context["l"]
        self.assertEqual([s["key"] for s in data["sections"]], ["component", "none"])
        row = data["sections"][0]["rows"][0]
        self.assertEqual((row["title"], row["makes"], row["time"]), ("Bun dough", "Makes 24 pieces of Bun", "1 h 30 min"))
        self.assertEqual([p["label"] for p in row["processes"]], ["Prep work", "Rise / proof"])
        only = self.client.get(reverse("admin_recipes") + "?show=none").context["l"]["sections"]
        self.assertEqual([r["recipe"] for s in only for r in s["rows"]], [self.loose])
        self.assertEqual(len(self.client.get(reverse("admin_recipes") + "?q=bun").context["l"]["sections"]), 1)  # by what it makes

    def test_steps_timers_and_total_time(self):
        e = self.post(self.dough, "add_step").context["e"]
        self.assertEqual(e["status"], "1 step needs a title")
        draft = e["d"]
        draft["steps"][2]["title"] = "Bake"
        e = self.post(self.dough, "timer:2", draft).context["e"]
        self.assertEqual((e["d"]["steps"][2]["timer"], e["d"]["steps"][2]["mins"], e["d"]["sel"]), (True, "10", 2))
        draft = e["d"]
        draft["steps"][2]["mins"] = "100"
        e = self.post(self.dough, "preview", draft).context["e"]
        self.assertEqual(e["status"], "Total time is shorter than a timer")
        self.assertFalse(e["can_save"])
        e = self.post(self.dough, "use_timers", e["d"]).context["e"]
        self.assertEqual(e["d"]["total"], "160")
        e = self.post(self.dough, "step_up:2", e["d"]).context["e"]
        self.assertEqual([s["title"] for s in e["d"]["steps"]], ["Mix", "Bake", "Rise"])
        self.assertEqual(self.dough.steps.count(), 2)  # nothing saved yet
        self.post(self.dough, "save", e["d"])
        steps = list(self.dough.steps.all())
        self.assertEqual([(s.title, s.duration) for s in steps],
                         [("Mix", None), ("Bake", datetime.timedelta(minutes=100)), ("Rise", datetime.timedelta(minutes=60))])
        self.dough.refresh_from_db()
        self.assertEqual(self.dough.total_minutes, 160)

    def test_ingredients_and_attaching(self):
        from inventory.models import Recipe
        e = self.post(self.loose, "preview", add_item=f"c:{self.onion.id}").context["e"]
        e = self.client.post(reverse("admin_recipe", args=[self.loose.id]),
                             {**recipe_fields(e["d"]), "op": "preview", "add_item": f"i:{self.flour.id}"}).context["e"]
        self.assertEqual([(g["kind"], g["item"]) for g in e["d"]["ings"]], [("c", str(self.onion.id)), ("i", str(self.flour.id))])
        draft = e["d"]
        draft["target"] = f"t:{self.freeze.id}"
        draft["total"] = "45"
        draft["steps"] = [{"id": "", "title": "Tray up", "text": "", "scene": "freeze", "timer": True, "mins": "40"}]
        e = self.post(self.loose, "preview", draft).context["e"]
        self.assertEqual(e["kind_text"], "Transformation recipe")
        self.assertIn("Each batch moves 5 pieces: bun to bun, frozen.", e["kind_hint"])
        self.post(self.loose, "save", e["d"])
        self.freeze.refresh_from_db()
        self.assertEqual(self.freeze.recipe_id, self.loose.id)
        self.assertEqual(self.freeze.takes, datetime.timedelta(minutes=45))
        self.assertEqual(Recipe.objects.get(pk=self.loose.pk).ingredients.filter(component=self.onion).count(), 1)
        # Attaching to a component with a recipe takes it over; the old recipe is left unattached
        draft = self.draft(self.loose)
        draft["target"] = f"c:{self.bun.id}"
        self.post(self.loose, "save", draft)
        self.assertEqual(Component.objects.get(pk=self.bun.pk).recipe_id, self.loose.id)
        self.assertIsNone(Recipe.objects.get(pk=self.dough.pk).target())

    def test_new_leave_and_delete(self):
        from inventory.models import Recipe
        response = self.client.post(reverse("admin_recipe_new"))
        new = Recipe.objects.latest("id")
        self.assertRedirects(response, reverse("admin_recipe", args=[new.id]))
        page = self.client.get(reverse("admin_recipe", args=[new.id]))
        self.assertEqual(page.context["e"]["status"], "Set the total time")
        self.assertContains(page, "In Cookery")
        self.assertRedirects(self.post(self.dough, "leave"), reverse("admin_recipes"))
        self.assertTrue(self.post(self.dough, "leave", title="Changed").context["e"]["leave_ask"])
        self.assertRedirects(self.post(new, "delete"), reverse("admin_recipes"))
        self.assertFalse(Recipe.objects.filter(pk=new.pk).exists())


def service_fields(draft, **changes):
    """The service editor's POST fields for a draft (admin_portal.services.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "description", "category", "pricing", "price", "minimum", "tax", "deposit_amount",
                                  "deposit_per", "lead_days", "cancellation", "qa", "qb")}
    data["category_was"] = draft.get("category_was", draft["category"])
    data["deposit_was"] = draft.get("deposit_was", "1" if draft["deposit"] else "0")
    data["includes"] = draft["includes"]
    data.update({f"detail-{k}": v for k, v in draft["details"].items()})
    for flag in ("deposit", "needs_approval", "at_counter", "online", "catering", "active"):
        if draft[flag]:
            data[flag] = "1"
    return data


class ServiceTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        from services.models import Booking, Service
        cls.build_admin()
        cls.urn = Service.objects.create(title="Coffee urn", category=Service.EQUIPMENT, pricing=Service.DAY, price=Decimal("45"),
                                         minimum=1, details={"owned": 3}, deposit=True, deposit_amount=Decimal("75"),
                                         description="Percolator for 100 cups")
        cls.room = Service.objects.create(title="Private room", category=Service.VENUE, pricing=Service.FLAT, price=Decimal("300"),
                                          details={"seated": 18, "standing": 25, "min_spend": 600, "hours": 0}, active=False,
                                          includes=["Tables and chairs"], needs_approval=True, lead_days=7)
        cls.old = Service.objects.create(title="Photo booth", category=Service.EXTRA, pricing=Service.FLAT, price=Decimal("150"), archived=True)
        today = timezone.localdate()
        Booking.objects.create(service=cls.urn, name="Book club", date=today + datetime.timedelta(days=3), amount=2, count=2)
        Booking.objects.create(service=cls.urn, name="PAC", date=today + datetime.timedelta(days=9), status=Booking.PENDING)
        Booking.objects.create(service=cls.urn, name="Declined", date=today, status=Booking.DECLINED)
        Booking.objects.create(service=cls.urn, name="Long ago", date=today - datetime.timedelta(days=60),
                               created_at=timezone.now() - datetime.timedelta(days=60))

    def setUp(self):
        self.client.force_login(self.owner)

    def post(self, service, op, draft=None, **changes):
        draft = draft or services.draft_from_service(service)
        return self.client.post(reverse("admin_service", args=[service.id]), {**service_fields(draft, **changes), "op": op})

    def test_list_sections_chips_and_search(self):
        l = self.client.get(reverse("admin_services")).context["l"]
        self.assertEqual([s["key"] for s in l["sections"]], ["equipment", "venue"])
        self.assertEqual({c["key"]: c["count"] for c in l["chips"]}["equipment"], 1)
        self.assertEqual(l["archived"], 1)
        urn = l["sections"][0]["rows"][0]
        self.assertEqual((urn["price"], urn["price_sub"], urn["booked"]), ("$45 / day", "min 1 day", 2))  # not the declined or old one
        self.assertEqual([t["label"] for t in urn["tags"]], ["3 owned", "$75 deposit each"])
        room = l["sections"][1]["rows"][0]
        self.assertEqual(room["price_sub"], "$600 food and drink minimum")
        self.assertEqual([t["label"] for t in room["tags"]], ["18 seated · 25 standing", "Needs approval", "7 days notice"])
        only = self.client.get(reverse("admin_services") + "?cat=venue").context["l"]["sections"]
        self.assertEqual([s["key"] for s in only], ["venue"])
        self.assertEqual(len(self.client.get(reverse("admin_services") + "?q=percolator").context["l"]["sections"]), 1)
        archived = self.client.get(reverse("admin_services") + "?show=archived").context["l"]["sections"]
        self.assertEqual([r["service"] for s in archived for r in s["rows"]], [self.old])

    def test_editor_quote_and_bookings(self):
        e = self.client.get(reverse("admin_service", args=[self.urn.id])).context["e"]
        self.assertEqual((e["status"], e["price_line"]), ("All changes saved", "Customers see: $45 / day · min 1 day"))
        self.assertEqual([(l["label"], l["value"]) for l in e["quote"]["lines"]],
                         [("$45 × 1 day × 2", "$90.00"), ("Total", "$90.00"), ("Deposit held, refundable", "$150.00")])
        self.assertEqual([(b["who"], b["state"]) for b in e["bookings"]], [("Book club", "Confirmed"), ("PAC", "Needs approval")])
        e = self.post(self.urn, "preview", qa="2", qb="5").context["e"]
        self.assertEqual(e["quote"]["lines"][0]["label"], "$45 × 2 days × 5")
        self.assertEqual(e["quote"]["notes"], ["Only 3 units owned."])
        self.assertEqual(e["status"], "All changes saved")  # the quote's inputs aren't part of the service

    def test_switching_category_starts_its_details_and_pricing(self):
        draft = services.draft_from_service(self.urn)
        e = self.post(self.urn, "preview", draft, category="staff", category_was="equipment").context["e"]
        self.assertEqual((e["d"]["pricing"], e["d"]["details"]), ("hour", {"staff": "1", "gratuity": "0"}))
        self.assertEqual([f["key"] for f in e["fields"]], ["staff", "gratuity"])
        self.assertEqual(e["status"], "Unsaved changes")

    def test_save_validation_and_deposit_default(self):
        from services.models import Service
        self.assertEqual(self.post(self.urn, "preview", price="0").context["e"]["status"], "Needs a name and a price")
        draft = services.draft_from_service(self.room)
        draft.update(category="equipment", category_was="equipment", details={"owned": "4"}, pricing="day", price="60", deposit=True, deposit_amount="0")
        draft["deposit_was"] = "0"
        e = self.post(self.room, "preview", draft).context["e"]
        self.assertEqual(e["d"]["deposit_amount"], "120")  # two days of rent, rounded to $5
        e["d"].update(title="Grill", minimum="1", active=True, online=False)
        self.assertRedirects(self.post(self.room, "save", e["d"]), reverse("admin_service", args=[self.room.id]))
        room = Service.objects.get(pk=self.room.pk)
        self.assertEqual((room.title, room.category, room.details, room.includes, room.deposit_amount, room.active, room.online),
                         ("Grill", "equipment", {"owned": 4}, [], Decimal("120.00"), True, False))

    def test_new_duplicate_leave_and_archive(self):
        from services.models import Service
        response = self.client.post(reverse("admin_service_new"))
        new = Service.objects.latest("id")
        self.assertRedirects(response, reverse("admin_service", args=[new.id]))
        self.assertEqual(self.client.get(response.url).context["e"]["status"], "Needs a name and a price")
        self.assertRedirects(self.post(self.urn, "leave"), reverse("admin_services"))
        self.assertTrue(self.post(self.urn, "leave", title="Changed").context["e"]["leave_ask"])
        response = self.post(self.urn, "duplicate")
        copy = Service.objects.latest("id")
        self.assertEqual((copy.title, copy.active, copy.deposit_amount), ("Coffee urn (copy)", False, Decimal("75.00")))
        self.assertRedirects(response, reverse("admin_service", args=[copy.id]))
        self.assertRedirects(self.post(self.urn, "archive"), reverse("admin_services"))
        self.assertTrue(Service.objects.get(pk=self.urn.pk).archived)
        self.assertContains(self.client.get(reverse("admin_service", args=[self.urn.id])), "Restore this service")


def component_fields(draft, **changes):
    """The component editor's POST fields for a draft (admin_portal.components.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "unit", "category", "recipe", "storage", "keeps_days", "inventory",
                                  "inventory_was", "par", "extra_label", "extra_price", "extra_max")}
    data["offer"] = draft["offer"]
    data["extra_was"] = draft.get("extra_was", "1" if draft["extra_on"] else "0")
    if draft["extra_on"]:
        data["extra_on"] = "1"
    data["group-count"] = len(draft.get("groups", []))
    for i, g in enumerate(draft.get("groups", [])):
        data.update({f"group-{i}-id": g["id"], f"group-{i}-title": g["title"], f"group-{i}-pick": g["pick"], f"group-{i}-note": g["note"],
                     f"group-{i}-opt-count": len(g["options"])})
        if g["required"]:
            data[f"group-{i}-required"] = "1"
        for j, o in enumerate(g["options"]):
            data.update({f"group-{i}-opt-{j}-id": o["id"], f"group-{i}-opt-{j}-title": o["title"], f"group-{i}-opt-{j}-price": o["price"]})
    return data


class ComponentTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        from inventory.models import Recipe, RecipeIngredient, Transformation
        cls.build_admin()
        cls.beef = Ingredient.objects.create(title="Ground beef", unit_of_measurement="g", cost=Decimal("0.015"))
        cls.salt = Ingredient.objects.create(title="Salt", unit_of_measurement="g", cost=Decimal("0.002"), allergens=["Mustard"])
        ComponentIngredient.objects.create(component=cls.patty, ingredient=cls.beef, quantity=120)
        Component.objects.filter(pk=cls.patty.pk).update(category="protein", par=250, extra_label="Extra patty")
        cls.frozen = Component.objects.create(title="Patty, frozen", unit_of_measurement="ea", crafting_option="transform",
                                              category="protein", storage=StorageLocation.named("Freezer"), keeps_days=90, inventory=80)
        Transformation.objects.create(name="Thaw", from_component=cls.frozen, to_component=cls.patty, duration=datetime.timedelta(hours=8))
        Transformation.objects.create(name="Freeze", from_component=cls.patty, to_component=cls.frozen, duration=datetime.timedelta(hours=1))
        cls.recipe = Recipe.objects.create(title="Smash patties", yield_quantity=40)
        RecipeIngredient.objects.create(recipe=cls.recipe, ingredient=cls.beef, quantity=4400)
        RecipeIngredient.objects.create(recipe=cls.recipe, ingredient=cls.salt, quantity=30)
        cls.spare = Component.objects.create(title="Spare sauce", unit_of_measurement="ml")

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, component):
        return components.draft_from_component(Component.objects.get(pk=component.pk))

    def post(self, component, op, draft=None, **changes):
        return self.client.post(reverse("admin_component", args=[component.id]), {**component_fields(draft or self.draft(component), **changes), "op": op})

    def test_costs_always_come_from_ingredient_prices(self):
        self.assertEqual(dish_info.component_cost(self.patty), Decimal("1.800"))
        self.assertEqual(dish_info.component_cost(self.frozen), Decimal("1.800"))  # made from patties by a transform
        self.assertEqual(dish_info.component_cost(self.spare), Decimal("0"))  # no recipe or ingredients: unknown
        Component.objects.filter(pk=self.patty.pk).update(recipe=self.recipe)
        patty = Component.objects.get(pk=self.patty.pk)
        self.assertEqual(dish_info.component_cost(patty), Decimal("66.06") / 40)
        self.assertEqual(dish_info.component_allergens(patty), ["Mustard"])

    def test_list_groups_filters_kpis_and_extras_sold(self):
        self.place([{"type": "dish", "id": self.burger.id, "qty": 2, "cust": {"parts": {str(self.dc_patty.id): 3}}}], timezone.now())
        self.assertEqual(components.extras_sold()[self.patty.id]["count"], 4)  # two extra patties on each of two burgers
        l = self.client.get(reverse("admin_components")).context["l"]
        self.assertEqual(l["groups"][0]["name"], "Proteins")
        row = next(r for g in l["groups"] for r in g["rows"] if r["c"] == self.patty)
        self.assertEqual((row["cost"], row["extra"], row["state"], row["used"]), ("$1.80", True, "low", "1 dish"))
        self.assertEqual({f["key"]: f["count"] for f in l["filters"]}["extra"], 3)  # bun, onion and patty are adjustable
        self.assertEqual(l["kpis"][3]["value"], "$16.00")
        only = self.client.get(reverse("admin_components") + "?show=low").context["l"]["groups"]
        self.assertEqual([r["c"] for g in only for r in g["rows"]], [Component.objects.get(pk=self.patty.pk)])
        self.assertEqual(len(self.client.get(reverse("admin_components") + "?q=frozen").context["l"]["groups"]), 1)

    def test_editor_cards(self):
        e = self.client.get(reverse("admin_component", args=[self.patty.id])).context["e"]
        self.assertEqual((e["status"], e["source"], e["cost_big"], e["chip"]), ("All changes saved", "per_unit", "$1.80", "Way under par"))
        self.assertEqual([o["c"] for o in e["others"]], [self.frozen])
        self.assertEqual([m["value"] for m in e["margin"]][:3], ["$4.00", "$1.80", "$2.20 · 55%"])
        self.assertTrue(e["archive_off"])
        e = self.post(self.patty, "preview", recipe=str(self.recipe.id)).context["e"]
        self.assertEqual((e["source"], e["batch_line"], e["status"]), ("recipe", "Batch costs $66.06 and makes 40 ea", "Unsaved changes"))
        e = self.client.get(reverse("admin_component", args=[self.frozen.id])).context["e"]
        self.assertEqual((e["source"], e["cost_big"]), ("transform", "$1.80"))

    def test_extras_drive_the_dish_parts(self):
        draft = self.draft(self.patty)
        self.assertEqual((draft["extra_on"], draft["offer"], draft["extra_max"]), (True, [str(self.dc_patty.id)], "10"))
        e = self.post(self.patty, "extra_less", draft).context["e"]
        self.assertEqual(e["max_n"], 9)
        self.post(self.patty, "save", e["d"])
        self.dc_patty.refresh_from_db()
        self.assertEqual((self.dc_patty.adjustable, self.dc_patty.max_extra), (True, 9))
        self.post(self.patty, "save", self.draft(self.patty), offer=[])
        self.dc_patty.refresh_from_db()
        self.assertEqual((self.dc_patty.adjustable, self.dc_patty.max_extra), (True, 0))  # can still be left out
        # Turning it on for an onion fills in a label, a price and its dishes
        e = self.post(self.onion, "preview", self.draft(self.onion), extra_on=True, extra_was="0").context["e"]
        self.assertEqual((e["d"]["extra_label"], e["d"]["extra_price"], e["d"]["offer"]), ("Extra onion", "0.5", [str(self.dc_onion.id)]))

    def test_save_keeps_stock_counted_elsewhere_and_checks_names(self):
        draft = self.draft(self.patty)
        Component.objects.filter(pk=self.patty.pk).update(inventory=55)  # counted in Cookery meanwhile
        self.post(self.patty, "save", draft, par="150", storage="line fridge")
        self.post(self.frozen, "save", self.draft(self.frozen), storage="freezer")  # an existing location, any case
        patty = Component.objects.get(pk=self.patty.pk)
        self.assertEqual((patty.inventory, patty.par, patty.storage.name), (55, 150, "line fridge"))
        self.assertEqual(StorageLocation.objects.filter(name__iexact="freezer").count(), 1)
        self.post(self.patty, "save", self.draft(self.patty), inventory="60")
        self.assertEqual(Component.objects.get(pk=self.patty.pk).inventory, 60)
        e = self.post(self.spare, "preview", title="patty").context["e"]
        self.assertEqual((e["status"], e["can_save"]), ("Another component has this name", False))

    def test_new_duplicate_and_archive(self):
        response = self.client.post(reverse("admin_component_new"))
        new = Component.objects.latest("id")
        self.assertRedirects(response, reverse("admin_component", args=[new.id]))
        self.assertRedirects(self.post(self.patty, "leave"), reverse("admin_components"))
        self.assertTrue(self.post(self.patty, "leave", title="Changed").context["e"]["leave_ask"])
        self.post(self.patty, "archive")
        self.assertFalse(Component.objects.get(pk=self.patty.pk).archived)  # it's in the burger
        self.post(self.patty, "duplicate")
        copy = Component.objects.latest("id")
        self.assertEqual((copy.title, copy.inventory, copy.recipe_id), ("Patty (copy)", 0, None))
        self.assertRedirects(self.post(self.spare, "archive"), reverse("admin_components"))
        self.assertTrue(Component.objects.get(pk=self.spare.pk).archived)
        self.assertNotIn(self.spare, [r["c"] for g in self.client.get(reverse("admin_components")).context["l"]["groups"] for r in g["rows"]])


def ingredient_fields(draft, **changes):
    """The ingredient editor's POST fields for a draft (admin_portal.ingredients.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "unit", "category", "supplier", "pack_size", "pack_price", "inventory", "inventory_was", "par", "storage")}
    data["allergens"] = draft["allergens"]
    data["gluten_was"] = draft.get("gluten_was", "1" if "Gluten" in draft["allergens"] else "")
    for flag in ("meat", "fish", "animal"):
        if draft[flag]:
            data[flag] = "1"
    if draft.get("gluten", "Gluten" in draft["allergens"]):
        data["gluten"] = "1"
    return data


class IngredientTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        from inventory.models import Recipe, RecipeIngredient
        cls.build_admin()
        cls.supplier = Supplier.objects.create(name="Valley Meats")
        cls.beef = Ingredient.objects.create(title="Ground beef", unit_of_measurement="g", category="meat", supplier=cls.supplier,
                                             pack_size=5000, pack_price=Decimal("75.00"), previous_cost=Decimal("0.0144"),
                                             inventory=3000, par=8000, meat=True)
        cls.flour = Ingredient.objects.create(title="Flour", unit_of_measurement="g", category="bakery", pack_size=20000,
                                              pack_price=Decimal("40.00"), inventory=18000, par=10000, allergens=["Gluten"])
        cls.saffron = Ingredient.objects.create(title="Saffron", unit_of_measurement="g", pack_size=10, pack_price=Decimal("28"))
        ComponentIngredient.objects.create(component=cls.patty, ingredient=cls.beef, quantity=120)
        recipe = Recipe.objects.create(title="Bun dough", yield_quantity=24)
        RecipeIngredient.objects.create(recipe=recipe, ingredient=cls.flour, quantity=1000)
        Component.objects.filter(pk=cls.bun.pk).update(recipe=recipe)

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, ingredient):
        return ingredients.draft_from_ingredient(Ingredient.objects.get(pk=ingredient.pk))

    def post(self, ingredient, op, draft=None, **changes):
        return self.client.post(reverse("admin_ingredient", args=[ingredient.id]), {**ingredient_fields(draft or self.draft(ingredient), **changes), "op": op})

    def test_cost_is_the_pack_price_over_the_pack_size_and_flows_into_components(self):
        self.assertEqual(self.beef.cost, Decimal("0.015"))
        self.assertEqual(dish_info.component_cost(self.patty), Decimal("1.8"))
        self.post(self.beef, "save", pack_price="100")
        beef = Ingredient.objects.get(pk=self.beef.pk)
        self.assertEqual((beef.cost, beef.previous_cost), (Decimal("0.02"), Decimal("0.015")))
        self.assertEqual(dish_info.component_cost(Component.objects.get(pk=self.patty.pk)), Decimal("2.4"))
        bun = Component.objects.get(pk=self.bun.pk)  # through its recipe: 1 kg of flour makes 24
        self.assertEqual(dish_info.component_cost(bun).quantize(Decimal("0.0001")), Decimal("0.0833"))

    def test_list_groups_filters_and_kpis(self):
        l = self.client.get(reverse("admin_ingredients")).context["l"]
        self.assertEqual([g["name"] for g in l["groups"]][:2], ["Meat and poultry", "Bread and baking"])
        beef = l["groups"][0]["rows"][0]
        self.assertEqual((beef["cost"], beef["change_text"], beef["state"], beef["sub"]), ("$15.00 / kg", "▲ 4.2%", "low", "Valley Meats · $75.00 for 5 kg"))
        self.assertEqual([d["on"] for d in beef["diets"]], [False, False, False, True])
        counts = {f["key"]: f["count"] for f in l["filters"]}
        self.assertEqual((counts["up"], counts["alg"], counts["gf"], counts["low"]), (1, 1, 2, 1))
        self.assertEqual(l["kpis"][3]["sub"], "Biggest: Ground beef +4.2%")
        self.assertEqual(len(self.client.get(reverse("admin_ingredients") + "?q=valley").context["l"]["groups"]), 1)

    def test_allergens_and_switches_stay_in_step(self):
        draft = self.draft(self.flour)
        e = self.post(self.flour, "preview", draft, gluten=False).context["e"]  # switching gluten off drops the allergen
        self.assertEqual(e["d"]["allergens"], [])
        e = self.post(self.flour, "preview", draft, allergens=["Gluten", "Dairy"]).context["e"]
        switches = {s["key"]: s for s in e["switches"]}
        self.assertTrue(switches["animal"]["on"] and switches["animal"]["locked"])
        self.assertEqual([d["on"] for d in e["diets"]], [True, False, True, False])  # vegetarian, not vegan, pescatarian, has gluten

    def test_new_supplier_validation_stock_and_archive(self):
        self.assertEqual(self.post(self.saffron, "preview", pack_size="0").context["e"]["status"], "Set a pack size")
        self.assertEqual(self.post(self.saffron, "preview", title="flour").context["e"]["status"], "Another ingredient has this name")
        draft = self.draft(self.saffron)
        Ingredient.objects.filter(pk=self.saffron.pk).update(inventory=7)  # counted elsewhere meanwhile
        self.post(self.saffron, "save", draft, supplier="Spice House", par="5", storage="Spice rack")
        saffron = Ingredient.objects.get(pk=self.saffron.pk)
        self.assertEqual((saffron.supplier.name, saffron.par, saffron.inventory, saffron.storage.name), ("Spice House", 5, 7, "Spice rack"))
        self.assertIn("Spice rack", [p.name for p in self.client.get(reverse("admin_ingredient", args=[self.beef.id])).context["e"]["places"]])
        self.post(self.beef, "archive")
        self.assertFalse(Ingredient.objects.get(pk=self.beef.pk).archived)  # used in the patty
        self.assertRedirects(self.post(self.saffron, "archive"), reverse("admin_ingredients"))
        self.assertTrue(Ingredient.objects.get(pk=self.saffron.pk).archived)
        response = self.client.post(reverse("admin_ingredient_new"))
        self.assertRedirects(response, reverse("admin_ingredient", args=[Ingredient.objects.latest("id").id]))


def condiment_fields(draft, **changes):
    """The condiment editor's POST fields for a draft (admin_portal.condiments.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "served_as", "serving", "source", "recipe", "supplier", "case_size", "case_price",
                                  "inventory", "inventory_was", "par", "storage", "keeps_days", "free", "price", "dine_in")}
    data.update(allergens=draft["allergens"], pairs=draft["pairs"], gluten_was="1" if "Gluten" in draft["allergens"] else "")
    for flag in ("meat", "fish", "animal", "ask"):
        if draft[flag]:
            data[flag] = "1"
    if "Gluten" in draft["allergens"]:
        data["gluten"] = "1"
    return data


class CondimentTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        from inventory.models import Recipe, RecipeIngredient
        cls.build_admin()
        cls.mayo = Ingredient.objects.create(title="Mayonnaise", unit_of_measurement="g", pack_size=1000, pack_price=Decimal("10.00"),
                                             allergens=["Egg"], animal=True)
        cls.recipe = Recipe.objects.create(title="Garlic aioli", yield_quantity=50)
        RecipeIngredient.objects.create(recipe=cls.recipe, ingredient=cls.mayo, quantity=1500)
        aioli = Component.objects.create(title="Garlic aioli", unit_of_measurement="ea", crafting_option="craft", recipe=cls.recipe, inventory=38, par=50)
        cls.aioli = Condiment.objects.create(component=aioli, served_as="cup", serving="2 oz cup", free_per_order=1, price=Decimal("0.75"))
        Extra.objects.create(dish=cls.burger, component=aioli)
        packets = Ingredient.objects.create(title="Ketchup packets", unit_of_measurement="ea", pack_size=1000, pack_price=Decimal("32.00"))
        ComponentIngredient.objects.create(component=cls.ketchup, ingredient=packets, quantity=1)
        cls.ketchup_c = Condiment.objects.create(component=cls.ketchup, free_per_order=3, price=Decimal("0.10"), dine_in="station")

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, condiment):
        return condiments.draft_from_condiment(Condiment.objects.get(pk=condiment.pk))

    def post(self, condiment, op, draft=None, **changes):
        return self.client.post(reverse("admin_condiment", args=[condiment.id]), {**condiment_fields(draft or self.draft(condiment), **changes), "op": op})

    def test_list_groups_costs_and_rules(self):
        self.place([{"type": "dish", "id": self.burger.id, "qty": 1, "cust": {"condiments": {str(self.ketchup_side.id): 4}}}], timezone.now())
        l = self.client.get(reverse("admin_condiments")).context["l"]
        self.assertEqual([g["name"] for g in l["groups"]], ["House-made", "Bought"])
        aioli, ketchup = l["groups"][0]["rows"][0], l["groups"][1]["rows"][0]
        self.assertEqual((aioli["cost"], aioli["rule"], aioli["allergens"], aioli["state"]), ("$0.30", "1 free, then $0.75", ["Egg"], "under"))
        self.assertEqual((ketchup["cost"], ketchup["rule"], ketchup["given"], ketchup["charged"]), ("$0.032", "3 free, then $0.10", 4, 1))
        self.assertEqual(l["kpis"][2]["value"], "$0.10")
        self.assertNotIn(self.ketchup, [r["c"] for g in self.client.get(reverse("admin_components")).context["l"]["groups"] for r in g["rows"]])

    def test_bought_condiments_edit_their_ingredient(self):
        draft = self.draft(self.ketchup_c)
        self.assertEqual((draft["source"], draft["case_size"], draft["case_price"]), ("bought", "1000", "32.00"))
        self.post(self.ketchup_c, "save", draft, case_price="40", supplier="Pacific Foodservice", allergens=["Sulphites"])
        packets = Ingredient.objects.get(title="Ketchup packet")
        self.assertEqual((packets.cost, packets.previous_cost, packets.allergens, packets.supplier.name),
                         (Decimal("0.04"), Decimal("0.032"), ["Sulphites"], "Pacific Foodservice"))
        self.assertEqual(dish_info.component_cost(Component.objects.get(pk=self.ketchup.pk)), Decimal("0.04"))

    def test_free_stepper_pairs_and_switching_source(self):
        e = self.post(self.aioli, "free_less").context["e"]
        self.assertEqual((e["d"]["free"], e["rule_line"]), ("0", "Every one is charged at $0.75."))
        e = self.post(self.aioli, "free_more", e["d"], free="5").context["e"]
        self.assertEqual(e["free_text"], "∞")
        self.post(self.aioli, "save", e["d"], pairs=[str(self.coffee.id)], ask=False, dine_in="table")
        aioli = Condiment.objects.get(pk=self.aioli.pk)
        self.assertEqual((aioli.free_per_order, aioli.ask_on_takeout, aioli.dine_in), (Condiment.NO_LIMIT, False, "table"))
        self.assertEqual(list(Extra.objects.filter(component=aioli.component).values_list("dish_id", flat=True)), [self.coffee.id])
        e = self.post(self.aioli, "preview", source="bought", case_size="0").context["e"]
        self.assertEqual((e["bought"], e["status"]), (True, "Set servings per case"))

    def test_new_duplicate_and_archive(self):
        response = self.client.post(reverse("admin_condiment_new"))
        new = Condiment.objects.latest("id")
        self.assertRedirects(response, reverse("admin_condiment", args=[new.id]))
        self.assertEqual(self.client.get(response.url).context["e"]["cost_big"], "—")  # no recipe yet
        self.post(self.ketchup_c, "duplicate")
        copy = Condiment.objects.latest("id")
        self.assertEqual((copy.component.title, copy.free_per_order, copy.component.inventory), ("Ketchup packet (copy)", 3, 0))
        self.assertEqual(copy.component.componentingredient_set.get().ingredient.title, "Ketchup packet (copy)")
        self.assertRedirects(self.post(self.aioli, "archive"), reverse("admin_condiments"))
        self.assertTrue(Component.objects.get(pk=self.aioli.component_id).archived)
        self.assertFalse(Extra.objects.filter(component_id=self.aioli.component_id).exists())


def disposable_fields(draft, **changes):
    """The disposable editor's POST fields for a draft (admin_portal.disposables.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("title", "size", "category", "material", "material_was", "bin", "counted", "count", "fee_label", "fee_price",
                                  "supplier", "case_size", "case_price", "inventory", "inventory_was", "par", "storage")}
    data.update(order_types=draft["order_types"], dishes=draft["dishes"], fee_was=draft.get("fee_was", "1" if draft["fee"] else "0"))
    if draft["fee"]:
        data["fee"] = "1"
    return data


class DisposableAdminTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()
        shell = Ingredient.objects.create(title="Clamshell", unit_of_measurement="ea", pack_size=500, pack_price=Decimal("62.50"), inventory=300, par=500)
        cls.shell = Disposable.objects.create(ingredient=shell, size="6 in", material="bagasse", order_types=["pickup", "delivery"])
        cls.shell.dishes.add(cls.burger)
        bag = Ingredient.objects.create(title="Paper bag", unit_of_measurement="ea", pack_size=250, pack_price=Decimal("55.00"), inventory=400, par=100)
        cls.bag = Disposable.objects.create(ingredient=bag, category="bags", material="kraft", bin="recycle", order_types=["pickup"],
                                            counted=Disposable.PER_ORDER, fee=True, fee_label="Paper bag", fee_price=Decimal("0.25"))

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, disposable):
        return disposables.draft_from_disposable(Disposable.objects.get(pk=disposable.pk))

    def post(self, disposable, op, draft=None, **changes):
        return self.client.post(reverse("admin_disposable", args=[disposable.id]), {**disposable_fields(draft or self.draft(disposable), **changes), "op": op})

    def test_list_counts_what_orders_used(self):
        order = create_order(lines=[self.dish(self.burger, 2)], channel="web", to_go_order=True, initial_status=OrderStation.STATUS_PENDING,
                             verify_payment=lambda remaining: None)
        self.assertEqual(order.fees, Decimal("0.25"))
        l = self.client.get(reverse("admin_disposables")).context["l"]
        shell = next(r for g in l["groups"] for r in g["rows"] if r["d"] == self.shell)
        self.assertEqual((shell["cost"], shell["rule"], shell["types"], shell["used"]["used"], shell["state"]), ("$0.125", "1 per burger", ["Pickup", "Delivery"], 2, "under"))
        bag = next(r for g in l["groups"] for r in g["rows"] if r["d"] == self.bag)
        self.assertEqual((bag["fee"], bag["used"]["charged"], bag["bin_label"]), ("$0.25 fee", Decimal("0.25"), "Recycling"))
        self.assertEqual(l["kpis"][0]["value"], "$0.47")  # (2 × 0.125 + 0.22) for one takeout order
        self.assertEqual({f["key"]: f["count"] for f in l["filters"]}["fee"], 1)
        self.assertEqual(Ingredient.objects.get(pk=self.shell.ingredient_id).inventory, 298)
        self.assertNotIn("Clamshell", [r["i"].title for g in self.client.get(reverse("admin_ingredients")).context["l"]["groups"] for r in g["rows"]])

    def test_editor_material_fee_and_counting(self):
        e = self.post(self.shell, "preview", material="pet").context["e"]
        self.assertEqual(e["d"]["bin"], "recycle")  # plastic suggests recycling
        e = self.post(self.shell, "preview", fee=True, fee_was="0").context["e"]
        self.assertEqual((e["d"]["fee_label"], e["d"]["fee_price"]), ("Clamshell", "0.25"))
        e = self.post(self.bag, "count_more").context["e"]
        self.assertEqual((e["d"]["count"], e["cost_lines"][0]["label"]), ("2", "Adds to every pickup order"))
        self.assertEqual(self.post(self.bag, "preview", fee_label="").context["e"]["status"], "Name the receipt line")
        self.post(self.shell, "save", self.draft(self.shell), counted="order", order_types=["delivery"], case_price="70")
        shell = Disposable.objects.get(pk=self.shell.pk)
        self.assertEqual((shell.counted, shell.order_types, list(shell.dishes.all()), shell.ingredient.cost, shell.ingredient.previous_cost),
                         ("order", ["delivery"], [], Decimal("0.14"), Decimal("0.125")))

    def test_new_duplicate_and_archive(self):
        response = self.client.post(reverse("admin_disposable_new"))
        new = Disposable.objects.latest("id")
        self.assertRedirects(response, reverse("admin_disposable", args=[new.id]))
        self.post(self.bag, "duplicate")
        copy = Disposable.objects.latest("id")
        self.assertEqual((copy.ingredient.title, copy.fee, copy.ingredient.inventory), ("Paper bag (copy)", True, 0))
        self.assertNotEqual(copy.ingredient_id, self.bag.ingredient_id)
        self.assertRedirects(self.post(self.bag, "archive"), reverse("admin_disposables"))
        self.assertEqual(price_cart([self.dish(self.burger)], channel="web", to_go=True).fees[0].label, "Paper bag")  # the copy still charges
        Disposable.objects.filter(pk=copy.pk).update(fee=False)
        self.assertEqual(price_cart([self.dish(self.burger)], channel="web", to_go=True).fees, [])  # archived ones don't


class SubstituteEditorTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()
        cls.wrap = Component.objects.create(title="Lettuce wrap", unit_of_measurement="ea", crafting_option="buy")

    def setUp(self):
        self.client.force_login(self.owner)

    def test_dish_parts_get_substitutes_and_show_component_groups(self):
        url = reverse("admin_dish", args=[self.burger.id])
        e = self.client.get(url).context["e"]
        self.assertEqual({(h["component"].title, h["group"].title) for h in e["inherited"]}, {("Patty", "Temperature"), ("Bun", "Bun")})
        fields = form_fields(e["d"])
        bun = next(i for i, p in enumerate(e["d"]["parts"]) if p["component"] == str(self.bun.id))
        e = self.client.post(url, {**fields, "op": "preview", f"part-{bun}-add_choice": str(self.wrap.id)}).context["e"]
        part = e["d"]["parts"][bun]
        self.assertEqual(([c["component"] for c in part["choices"]], part["label"]), ([str(self.wrap.id)], "Choice"))
        part.update(label="Bun")
        part["choices"][0]["price"] = "0.50"
        self.client.post(url, {**form_fields(e["d"]), "op": "save"})
        dc = DishComponent.objects.get(pk=self.dc_bun.pk)
        self.assertEqual((dc.choice_title, [(c.component, c.price) for c in dc.choices.all()]), ("Bun", [(self.wrap, Decimal("0.50"))]))

    def test_component_modifier_groups(self):
        url = reverse("admin_component", args=[self.patty.id])
        draft = components.draft_from_component(Component.objects.get(pk=self.patty.pk))
        self.assertEqual([g["title"] for g in draft["groups"]], ["Temperature"])
        e = self.client.post(url, {**component_fields(draft), "op": "add_group"}).context["e"]
        e["d"]["groups"][1].update(title="Doneness note", required=True)
        e["d"]["groups"][0]["options"][0]["price"] = "0.25"
        self.client.post(url, {**component_fields(e["d"], extra_label="Extra patty"), "op": "save"})
        groups = list(Component.objects.get(pk=self.patty.pk).option_groups.all())
        self.assertEqual([(g.title, g.required, [o.title for o in g.options.all()]) for g in groups],
                         [("Temperature", True, ["Medium", "Well done"]), ("Doneness note", True, ["Option"])])
        self.assertEqual(groups[0].options.first().price, Decimal("0.25"))


def restock_fields(draft, **changes):
    """The restock editor's POST fields for a draft (admin_portal.restocks.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("supplier", "status", "created_on", "placed_on", "expected_on", "delivered_on", "fee", "tax", "notes")}
    data["line-count"] = len(draft["lines"])
    for i, l in enumerate(draft["lines"]):
        data.update({f"line-{i}-{k}": l[k] for k in ("id", "ingredient", "cases", "price", "was", "pack")})
    return data


class RestockTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        from inventory.models import Restock, RestockLine
        cls.build_admin()
        cls.meats = Supplier.objects.create(name="Valley Meats")
        cls.beef = Ingredient.objects.create(title="Ground beef", unit_of_measurement="g", supplier=cls.meats, pack_size=5000,
                                             pack_price=Decimal("75.00"), inventory=3000, par=8000)
        cls.pork = Ingredient.objects.create(title="Pork shoulder", unit_of_measurement="g", supplier=cls.meats, pack_size=10000,
                                             pack_price=Decimal("100.00"), inventory=1000, par=12000)
        cls.restock = Restock.objects.create(supplier=cls.meats, created_by=cls.owner)
        RestockLine.objects.create(restock=cls.restock, ingredient=cls.pork, cases=1, case_price=Decimal("100.00"),
                                   was_price=Decimal("100.00"), pack_size=10000)

    def setUp(self):
        self.client.force_login(self.owner)

    def draft(self, restock=None):
        from inventory.models import Restock
        return restocks.draft_from_restock(Restock.objects.get(pk=(restock or self.restock).pk))

    def post(self, op, draft=None, restock=None, **changes):
        restock = restock or self.restock
        return self.client.post(reverse("admin_restock", args=[restock.id]), {**restock_fields(draft or self.draft(restock), **changes), "op": op})

    def test_list_suggests_what_is_under_par_and_drafts_it(self):
        l = self.client.get(reverse("admin_restocks")).context["l"]
        self.assertEqual([(g["supplier"], [i.title for i in g["items"]]) for g in l["suggest"]], [(self.meats, ["Ground beef"])])  # pork is on the draft
        self.assertEqual((l["filters"][1]["count"], l["kpis"][3]["value"]), (1, "1"))
        response = self.client.post(reverse("admin_restocks"), {"supplier": self.meats.id})
        from inventory.models import Restock
        new = Restock.objects.latest("id")
        self.assertRedirects(response, reverse("admin_restock", args=[new.id]))
        self.assertEqual([(line.ingredient, line.cases) for line in new.lines.all()], [(self.beef, 1)])  # 5 kg short: one 5 kg case

    def test_delivering_adds_stock_and_takes_the_new_price(self):
        e = self.post("fill").context["e"]
        self.assertEqual([l["ingredient"].title for l in e["lines"]], ["Pork shoulder", "Ground beef"])
        draft = e["d"]
        draft["lines"][1]["price"] = "80.00"
        e = self.post("status:delivered", draft).context["e"]
        self.assertEqual((e["d"]["status"], e["note_kind"]), ("delivered", "go"))
        self.assertEqual([c["name"] for c in e["changes"]], ["Ground beef"])
        self.post("save", e["d"])
        beef, pork = Ingredient.objects.get(pk=self.beef.pk), Ingredient.objects.get(pk=self.pork.pk)
        self.assertEqual((beef.inventory, beef.pack_price, beef.cost, beef.previous_cost), (8000, Decimal("80.00"), Decimal("0.016"), Decimal("0.015")))
        self.assertEqual(pork.inventory, 11000)
        # Locked now: saving again adds nothing, and it can't go back to placed or be deleted
        self.post("status:placed")
        self.post("save", self.draft(), notes="Checked")
        self.assertEqual(Ingredient.objects.get(pk=self.beef.pk).inventory, 8000)
        from inventory.models import Restock
        self.assertEqual(Restock.objects.get(pk=self.restock.pk).status, "delivered")
        self.post("delete")
        self.assertTrue(Restock.objects.filter(pk=self.restock.pk).exists())

    def test_add_line_receipt_duplicate_and_delete(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from inventory.models import Restock
        e = self.client.post(reverse("admin_restock", args=[self.restock.id]),
                             {**restock_fields(self.draft()), "op": "preview", "add_line": str(self.beef.id)}).context["e"]
        self.assertEqual(e["lines"][1]["l"]["cases"], "1")
        fields = {**restock_fields(self.draft()), "op": "preview", "receipt": SimpleUploadedFile("invoice.pdf", b"%PDF-1.4", content_type="application/pdf")}
        e = self.client.post(reverse("admin_restock", args=[self.restock.id]), fields).context["e"]
        self.assertEqual((e["receipt"]["name"][:7], e["receipt"]["kind"]), ("invoice", "PDF"))
        self.post("remove_receipt")
        self.assertFalse(Restock.objects.get(pk=self.restock.pk).receipt)
        response = self.post("duplicate")
        copy = Restock.objects.latest("id")
        self.assertRedirects(response, reverse("admin_restock", args=[copy.id]))
        self.assertEqual((copy.status, [l.ingredient for l in copy.lines.all()]), ("draft", [self.pork]))
        self.assertEqual(self.post("preview", restock=copy, status="placed", lines=[]).context["e"]["status"], "Add at least one item first")
        self.assertRedirects(self.post("delete", restock=copy), reverse("admin_restocks"))
        self.assertFalse(Restock.objects.filter(pk=copy.pk).exists())


class UnitTests(TestCase):
    def test_big_numbers_show_in_the_bigger_unit(self):
        self.assertEqual(fmt_qty(2500, "g"), "2.5 kg")
        self.assertEqual(fmt_qty(400, "ml"), "400 ml")
        self.assertEqual(fmt_qty(12000, "ml"), "12 L")
        self.assertEqual(fmt_qty(12, "ea"), "12 ea")


def catering_fields(draft, **changes):
    """The catering editor's POST fields for a draft (admin_portal.catering.draft_from_post reads them back)."""
    draft = {**draft, **changes}
    data = {k: draft[k] for k in ("add_kind", "add_ref", "add_qty", "add_price", "add_last", "msg")}
    data["line-count"] = len(draft["lines"])
    for i, line in enumerate(draft["lines"]):
        data.update({f"line-{i}-{k}": line[k] for k in ("id", "kind", "ref", "quantity", "amount", "price")})
        data[f"line-{i}-removed"] = "1" if line.get("removed") else ""
    return data


def catering_settings_fields(d, **changes):
    d = {**d, **changes}
    data = {k: "1" if d[k] else "" for k in catering.SWITCHES}
    data.update({k: d[k] for k in ("lead", "lead_unit", "max_ahead", "big_hours", "min_subtotal", "earliest", "latest", "hold_days", "policy", "auto_reply")})
    data.update({f"day-{i}": "1" if on else "" for i, on in enumerate(d["days"])})
    data["service"] = [str(x) for x in d["services"]]
    return data


@override_settings(TAX_RATE="0")
@mock.patch("catering.flow.send_email")
class CateringAdminTests(CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()
        cups = Ingredient.objects.create(title="Cold cup", unit_of_measurement="ea", pack_size=100, pack_price=Decimal("9.60"))
        cls.cup = Disposable.objects.create(ingredient=cups, size="16 oz")

    def setUp(self):
        self.client.force_login(self.owner)

    def test_inbox_sheets_and_badge(self, send):
        order = self.make_order()
        self.make_order(days=20, status=CateringOrder.CONFIRMED)
        response = self.client.get(reverse("admin_catering"))
        c = response.context["c"]
        self.assertEqual(([t["count"] for t in c["tabs"]], c["selected"]), ([1, 0, 1, 0], order))
        orders = next(g for g in response.context["rail"] if g["id"] == "orders")
        self.assertEqual(orders["badge"], 1)
        d = self.client.get(reverse("admin_catering_order", args=[order.id]) + "?sheet=accept").context["d"]
        self.assertEqual((d["sheet"]["kind"], d["t"]["deposit"], d["bar"]["kind"]), ("accept", Decimal("120"), "new"))

    def test_editing_and_sending_changes(self, send):
        order = self.make_order()
        url = reverse("admin_catering_order", args=[order.id])
        draft = self.client.get(url + "?edit=1").context["d"]["draft"]
        d = self.client.post(url, {**catering_fields(draft), "op": "more:0"}).context["d"]
        self.assertEqual((d["draft"]["lines"][0]["quantity"], d["draft"]["lines"][0]["price"]), (25, "12.50"))
        d = self.client.post(url, {**catering_fields(d["draft"]), "op": "add_kind:disposable"}).context["d"]
        self.assertEqual([o["id"] for o in d["add"]["options"]], [self.cup.id])  # clamshells are already on it
        d = self.client.post(url, {**catering_fields(d["draft"], add_ref=str(self.cup.id), add_qty="40"), "op": "preview"}).context["d"]
        self.assertEqual(d["draft"]["add_price"], "0.20")  # 9.6¢ each, about twice that
        d = self.client.post(url, {**catering_fields(d["draft"]), "op": "add"}).context["d"]
        d = self.client.post(url, {**catering_fields(d["draft"]), "op": "remove:3"}).context["d"]
        self.assertEqual((len(d["draft"]["lines"]), d["edit"]["count"], d["edit"]["can_send"]), (6, 3, False))
        response = self.client.post(url, {**catering_fields(d["draft"]), "op": "send"})
        self.assertEqual(response.context["d"]["draft"]["lines"][0]["quantity"], 25)  # still editing: no reason given
        self.client.post(url, {**catering_fields(d["draft"], msg="More people coming"), "op": "send"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.lines.count()), (CateringOrder.WAITING, 5))
        self.assertEqual(send.call_args.args[0], "catering_changes")
        d = self.client.get(url).context["d"]
        self.assertEqual(d["head"], "Your proposed changes")
        self.assertIn("Removed", [x["tag"] for g in d["groups"] for x in g["rows"]])

    def test_accept_finish_and_reject(self, send):
        order = self.make_order(days=0)
        url = reverse("admin_catering_order", args=[order.id])
        response = self.client.post(url, {"op": "accept", "message": "See you at noon"})
        self.assertRedirects(response, url + "?tab=upcoming", fetch_redirect_response=False)
        bar = self.client.get(url).context["d"]["bar"]
        self.assertEqual([b.get("op") for b in bar["buttons"]], ["keep", "finish"])  # event day, deposit held
        self.client.post(url, {"op": "finish"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.hold_state), (CateringOrder.DONE, CateringOrder.RELEASED))
        other = self.make_order()
        url = reverse("admin_catering_order", args=[other.id])
        self.assertEqual(self.client.post(url, {"op": "reject", "reason": "Too large for us"}).context["d"]["sheet"]["kind"], "reject")
        self.client.post(url, {"op": "reject", "reason": "Too large for us", "message": "Sorry, Ben"})
        self.assertEqual(CateringOrder.objects.get(pk=other.pk).status, CateringOrder.REJECTED)
        self.client.post(url, {"op": "reply", "text": "One more thing"})
        self.assertFalse(other.messages.filter(text="One more thing").exists())  # closed

    def test_settings(self, send):
        url = reverse("admin_catering_settings")
        s = self.client.get(url).context["s"]
        self.assertFalse(s["dirty"])
        s = self.client.post(url, {**catering_settings_fields(s["d"]), "op": "toggle:rush"}).context["s"]
        self.assertTrue(s["dirty"])
        self.client.post(url, {**catering_settings_fields(s["d"], days=[False] * 7), "op": "save"})
        self.assertTrue(CateringSettings.load().rush)  # not saved: no days
        services = [x for x in s["d"]["services"] if x != self.chafer.id]
        self.client.post(url, {**catering_settings_fields(s["d"], lead="3", services=services), "op": "save"})
        cfg = CateringSettings.load()
        self.assertEqual((cfg.rush, cfg.lead), (False, 3))
        self.chafer.refresh_from_db()
        self.assertFalse(self.chafer.catering)


class BrandingTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()
        store = StoreSettings.load()
        store.name, store.address = "Corner Kitchen", "1 Main St"
        store.save()

    def setUp(self):
        self.client.force_login(self.owner)

    def fields(self, b, **changes):
        """The Branding form's POST fields for what the page shows (branding.html)."""
        data = {}
        for g in b["groups"]:
            for name, value in g.get("hidden", []):
                data[f"v-{name}"] = value
            for f in g["fields"]:
                value = f["value"]
                data[f["name"]] = ("1" if value else "") if f["p"].type == "bool" else value
        data.update(changes)
        return data

    def test_drafts_publish_and_reset(self):
        from admin_portal.models import StoreTheme
        url = reverse("admin_branding")
        b = self.client.get(url).context["b"]
        self.assertEqual((b["theme"].name, b["can_publish"], b["status"]), ("neighbourhood", False, "Everything is live"))
        b = self.client.post(url, {**self.fields(b, **{"s-name": "Juniper Lane"}), "op": "set:accent:#1F4A3A"}).context["b"]
        self.assertEqual((b["values"]["accent"], b["store"]["name"], b["can_publish"]), ("#1F4A3A", "Juniper Lane", True))
        self.assertEqual(StoreTheme.objects.get().draft["accent"], "#1F4A3A")  # kept for the preview
        self.assertEqual(StoreSettings.load().name, "Corner Kitchen")  # not live yet
        b = self.client.post(url, {**self.fields(b), "op": "toggle:banner"}).context["b"]
        self.assertNotIn("banner_text", [f["p"].name for g in b["groups"] for f in g["fields"]])  # shown only with the banner
        b = self.client.post(url, {**self.fields(b, **{"v-ink": "#F6F0E4"}), "op": "preview"}).context["b"]
        self.assertIn("Aim for 4.5:1", next(f["warn"] for g in b["groups"] for f in g["fields"] if f["p"].name == "ink"))
        self.client.post(url, {**self.fields(b, **{"v-ink": "#221E18"}), "op": "publish"})
        live = StoreTheme.objects.get()
        self.assertEqual((live.live["accent"], live.live["banner"], StoreSettings.load().name), ("#1F4A3A", False, "Juniper Lane"))
        b = self.client.get(url).context["b"]
        self.assertFalse(b["can_publish"])
        self.client.post(url, {**self.fields(b), "op": "reset"})
        self.assertEqual(StoreTheme.objects.get().draft["accent"], "#CF4A2C")

    def test_upload_and_remove_images(self):
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            self.upload_and_remove()

    def upload_and_remove(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = reverse("admin_branding")
        b = self.client.get(url).context["b"]
        png = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        b = self.client.post(url, {**self.fields(b), "op": "preview", "upload-s.logo": png}).context["b"]
        self.assertTrue(b["store"]["logo"].startswith("files/branding/logo-"))
        bad = SimpleUploadedFile("hero.exe", b"MZ", content_type="application/octet-stream")
        b = self.client.post(url, {**self.fields(b), "op": "preview", "upload-hero": bad}).context["b"]
        self.assertEqual(b["values"]["hero"], "")
        b = self.client.post(url, {**self.fields(b), "op": "clear:s.logo"}).context["b"]
        self.assertEqual(b["store"]["logo"], "")


class StoreHoursTests(AdminFixture, TestCase):
    """The Store hours page (admin_portal/hours.py): the week, special dates, cutoffs, ordering ahead and pausing."""
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse("admin_hours")

    def fields(self, h, **changes):
        """The form's POST fields for what the page shows (hours.html)."""
        data = {k: "1" if v else "" for k, v in h["switches"]}
        data.update({k: str(v) for k, v in h["steps"]})
        for x in h["days"]:
            data[f"d{x['i']}-open"] = "1" if x["open"] else ""
            pairs = [(r["a"], r["b"]) for r in x["ranges"]] or x["hidden"]
            data[f"d{x['i']}-from"] = [str(a) for a, _b in pairs]
            data[f"d{x['i']}-to"] = [str(b) for _a, b in pairs]
        for key, col in (("id", "id"), ("date", "date"), ("from", "opens"), ("to", "closes"), ("note", "note")):
            data[f"sp-{key}"] = [str(s[col]) for s in h["specials"]]
        data["sp-closed"] = ["1" if s["closed"] else "" for s in h["specials"]]
        data.update(changes)
        return data

    def post(self, h, op="preview", **changes):
        return self.client.post(self.url, self.fields(h, op=op, **changes))

    def test_week_breaks_copy_and_save(self):
        from events.models import StoreHours
        h = self.client.get(self.url).context["h"]
        self.assertEqual((h["status"], h["can_save"]), ("All changes saved", False))
        h = self.post(h, "add:0").context["h"]  # a second period on Monday, an hour after the first ends
        self.assertEqual([(r["a"], r["b"]) for r in h["days"][0]["ranges"]], [(480, 1260), (1320, 1500)])
        h = self.post(h, **{"d0-to": ["780", "1260"], "d0-from": ["480", "840"]}).context["h"]
        self.assertEqual(h["days"][0]["ranges"][1]["break"], "Break 1 PM to 2 PM · 1 h")
        self.assertEqual(h["days"][0]["sub"], "12 h · 1 break")
        h = self.post(h, **{"copy-0": "week"}).context["h"]
        self.assertEqual([len(d["ranges"]) for d in h["days"]], [2, 2, 2, 2, 2, 1, 1])
        h = self.post(h, "day:6").context["h"]
        self.assertEqual((h["days"][6]["open"], h["days"][6]["hidden"]), (False, [[480, 1260]]))
        h = self.post(h, **{"d1-from": ["480", "700"], "d1-to": ["780", "1260"]}).context["h"]  # overlaps
        self.assertEqual((h["status"], h["can_save"]), ("1 thing to fix", False))
        self.post(h, "save")
        self.assertEqual(StoreHours.load().week[1]["r"], [[480, 1260]])  # not saved
        h = self.post(h, **{"d1-from": ["480", "840"], "d1-to": ["780", "1260"]}).context["h"]
        h = self.post(h, "less:pickup_before").context["h"]
        h = self.post(h, "toggle:around_breaks").context["h"]
        self.assertEqual((h["status"], h["can_save"]), ("Unsaved changes", True))
        self.assertRedirects(self.post(h, "save"), self.url, fetch_redirect_response=False)
        cfg = StoreHours.load()
        self.assertEqual((cfg.week[0], cfg.week[6]["open"], cfg.pickup_before, cfg.around_breaks),
                         ({"open": True, "r": [[480, 780], [840, 1260]]}, False, 15, False))
        h = self.client.get(self.url).context["h"]
        self.assertEqual(h["status"], "All changes saved")
        pick = [b for b in h["chart"][0]["bars"] if b["kind"] == "pick"]
        self.assertEqual(len(pick), 2)  # around breaks off: one bar each side of the break, cut only at open and close

    def test_special_dates(self):
        from events.models import SpecialHours
        h = self.client.get(self.url).context["h"]
        h = self.post(h, "spadd").context["h"]
        day = (timezone.localdate() + datetime.timedelta(days=7)).isoformat()
        self.assertEqual([(s["date"], s["closed"]) for s in h["specials"]], [(day, True)])
        h = self.post(h, "spkind:0:hours", **{"sp-note": ["Closing early"]}).context["h"]
        h = self.post(h, **{"sp-from": ["600"], "sp-to": ["540"]}).context["h"]
        self.assertTrue(h["specials"][0]["errs"])
        h = self.post(h, **{"sp-from": ["600"], "sp-to": ["900"]}).context["h"]
        self.post(h, "save")
        sp = SpecialHours.objects.get()
        self.assertEqual((sp.date.isoformat(), sp.closed, sp.opens, sp.closes, sp.note), (day, False, 600, 900, "Closing early"))
        h = self.client.get(self.url).context["h"]
        self.post(self.post(h, "spdel:0").context["h"], "save")
        self.assertFalse(SpecialHours.objects.exists())

    def test_pause_and_resume(self):
        from events.models import StoreHours
        from online_store import storefront
        h = self.client.get(self.url).context["h"]
        self.post(h, "pause:30")
        self.assertIsNotNone(StoreHours.load().paused_until)
        self.assertTrue(self.client.get(self.url).context["h"]["paused"])
        self.assertTrue(storefront.open_now()["text"].startswith("Back soon"))
        self.post(h, "resume")
        self.assertIsNone(StoreHours.load().paused_until)

    def test_ordering_ahead_limits_checkout(self):
        h = self.client.get(self.url).context["h"]
        h = self.post(h, "less:ahead_days").context["h"]
        self.assertEqual(h["ahead_rows"][0]["value"], "2 days")
        self.post(self.post(h, "toggle:ahead").context["h"], "save")
        from online_store.pages import _schedule
        when = _schedule({"when": "later", "date": timezone.localdate().isoformat(), "time": ""}, [], False)
        self.assertEqual((when["ahead"], len(when["days"])), (False, 1))


class StationsTests(AdminFixture, TestCase):
    """The Stations page (admin_portal/stations.py): editing, reordering, new stations, taking tickets, archiving."""
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse("admin_stations")

    def fields(self, s, **changes):
        data = {"count": str(s["count"]), "sel": str(s["d"]["sel"]), "gone": s["gone"]}
        for x in s["items"]:
            for f in ("id", "name", "tag", "icon", "color", "target"):
                data[f"st-{x['k']}-{f}"] = str(x[f])
            data[f"st-{x['k']}-active"] = "1" if x["active"] else ""
        data.update(changes)
        return data

    def post(self, s, op="preview", **changes):
        return self.client.post(self.url, self.fields(s, op=op, **changes))

    def test_edit_reorder_and_save(self):
        s = self.client.get(self.url).context["s"]
        names = [x["name"] for x in s["items"]]
        self.assertEqual((s["status"], s["e"]["r"]["name"]), ("All changes saved", names[0]))
        s = self.post(s, "icon:basket", **{"st-0-tag": "grl", "st-0-name": "Grill"}).context["s"]
        self.assertEqual((s["items"][0]["tag"], s["items"][0]["icon"], s["status"]), ("GRL", "basket", "Unsaved changes"))
        s = self.post(s, "more").context["s"]
        s = self.post(s, "down:0").context["s"]
        self.assertEqual((s["items"][1]["name"], s["d"]["sel"]), ("Grill", 1))
        s = self.post(s, **{"st-0-tag": "GRL"}).context["s"]  # the same code twice
        self.assertEqual((s["status"], s["can_save"]), ("Each station needs its own name and code", False))
        s = self.post(s, **{"st-0-tag": "BAR"}).context["s"]
        self.post(s, "save")
        self.kitchen.refresh_from_db()
        self.assertEqual((self.kitchen.name, self.kitchen.ticket_code, self.kitchen.icon, self.kitchen.target_minutes, self.kitchen.position),
                         ("Grill", "GRL", "basket", 11, 1))
        self.assertEqual(self.kitchen.as_dict()["tag"], "GRL")

    def test_new_station_and_taking_tickets(self):
        s = self.client.get(self.url).context["s"]
        s = self.post(s, "new").context["s"]
        k = s["d"]["sel"]
        s = self.post(s, **{f"st-{k}-name": "Fry", f"st-{k}-tag": "FRY"}).context["s"]
        self.post(s, "save")
        fry = Station.objects.get(name="Fry")
        self.assertEqual((fry.code, fry.ticket_code, fry.position), ("fry", "FRY", k))
        s = self.client.get(self.url).context["s"]
        self.post(self.post(s, "active").context["s"], "save")  # the first station stops taking tickets
        self.kitchen.refresh_from_db()
        self.assertFalse(self.kitchen.taking_tickets)
        self.assertFalse(Dish.objects.get(pk=self.burger.pk).available)

    def test_archive_moves_dishes(self):
        s = self.client.get(self.url).context["s"]
        s = self.post(s, "archive", move_to=str(self.bar.id)).context["s"]  # archive the first (kitchen)
        self.assertNotIn(self.kitchen.id, [x["id"] for x in s["items"]])
        self.assertTrue(s["gone"])
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).station, self.kitchen)  # not saved yet
        self.post(s, "save")
        self.kitchen.refresh_from_db()
        self.assertTrue(self.kitchen.archived)
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).station, self.bar)
        from pos_server.orders import station_list
        self.assertNotIn("kitchen", [x["code"] for x in station_list()])
