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
from pos_server.models import (CashDrawerSession, CashMovement, ComponentIngredient, Dish, DishCategory, DishComponent, Extra,
                               Ingredient, Menu, Option, OptionGroup, Order, OrderPayment, OrderStation, ReceiptLog, Refund)
from pos_server.orders import collect_order, create_order
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


class MenuEditorTests(AdminFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_admin()

    def setUp(self):
        self.client.force_login(self.owner)

    def act(self, menu, **data):
        return self.client.post(reverse("admin_menu_action", args=[menu.id]), data)

    def test_new_menus_start_as_drafts_and_publish(self):
        response = self.client.post(reverse("admin_menu_new"))
        menu = Menu.objects.latest("id")
        self.assertRedirects(response, reverse("admin_menu", args=[menu.id]))
        self.assertTrue(menu.draft)
        self.act(menu, action="manual")
        self.assertFalse(menu.is_live())  # drafts are never live
        self.act(menu, action="publish")
        menu.refresh_from_db()
        self.assertTrue(menu.is_live())

    def test_items_toggle_and_add_all_shown(self):
        menu = Menu.objects.create(title="Empty")
        self.act(menu, action="toggle", kind="dish", id=self.burger.id)
        self.act(menu, action="toggle", kind="combo", id=self.combo.id)
        self.assertEqual(list(menu.dishes.all()), [self.burger])
        self.assertEqual(list(menu.combos.all()), [self.combo])
        self.act(menu, action="toggle", kind="dish", id=self.burger.id)
        self.assertFalse(menu.dishes.exists())
        self.act(menu, action="add_shown", dish=[self.coffee.id, self.tea.id])
        self.assertEqual(menu.dishes.count(), 2)
        page = self.client.get(reverse("admin_menu", args=[menu.id]) + f"?group={self.bar.id}&q=tea").content.decode()
        self.assertIn("Tea", page)
        self.assertNotIn(">Burger<", page)

    def test_schedule_rules(self):
        menu = Menu.objects.create(title="Brunch")
        self.act(menu, action="auto")
        menu.refresh_from_db()
        self.assertTrue(menu.auto)
        on = menu.rules.get(kind="on")
        self.assertEqual((on.days, on.time), ("01234", datetime.time(11)))  # a starting point
        self.act(menu, action="rule_day", rule=on.id, day=5)
        self.act(menu, action="rule_time", rule=on.id, time="09:30")
        on.refresh_from_db()
        self.assertEqual((on.days, on.time), ("012345", datetime.time(9, 30)))
        self.act(menu, action="add_rule", kind="off", type="dates")
        dates = menu.rules.get(type="dates")
        self.act(menu, action="rule_dates", rule=dates.id, start="2026-12-26", end="2026-12-24")
        dates.refresh_from_db()
        self.assertEqual((dates.start_date, dates.end_date), (datetime.date(2026, 12, 24), datetime.date(2026, 12, 26)))
        self.act(menu, action="rule_remove", rule=dates.id)
        self.assertFalse(menu.rules.filter(type="dates").exists())
        self.assertEqual(self.client.get(reverse("admin_menu", args=[menu.id]) + "?tab=schedule").status_code, 200)

    def test_priority_arrows_and_the_list(self):
        first = Menu.objects.get()  # the fixture's menu
        second = Menu.objects.create(title="Second", position=1, manual_on=True)
        self.act(second, action="up")
        self.assertEqual(list(Menu.objects.all()), [second, first])
        self.assertEqual(Menu.active(), second)
        self.assertContains(self.client.get(reverse("admin_menus")), "Second")

    def test_back_only_goes_to_this_site(self):
        menu = Menu.objects.create(title="M")
        response = self.act(menu, action="manual", back="https://evil.example.com/")
        self.assertRedirects(response, reverse("admin_menu", args=[menu.id]))


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

    def act(self, dish, **data):
        return self.client.post(reverse("admin_dish_action", args=[dish.id]), data)

    def test_cost_allergens_and_diets_come_from_the_recipe(self):
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertEqual(dish_info.plate_cost(burger), Decimal("2.12"))
        self.assertEqual(dish_info.food_cost_pct(Decimal("2.12"), burger.price), 14.6)
        self.assertEqual(dish_info.dish_allergens(burger), ["Gluten", "Dairy"])
        self.assertEqual(dish_info.dish_diets(burger), [])  # meat, gluten, dairy
        self.assertIsNone(dish_info.plate_cost(self.tea))  # no recipe
        self.assertEqual(dish_info.dish_diets(self.tea), [])
        # Dairy makes it an animal product: vegetarian and pescatarian, not vegan
        DishComponent.objects.filter(dish=burger, component=self.patty).delete()
        DishComponent.objects.filter(dish=burger, component=self.bun).delete()
        self.assertEqual([d["key"] for d in dish_info.dish_diets(burger)], ["vegetarian", "pescatarian", "gluten_free"])

    def test_list_groups_filters_and_searches(self):
        page = self.client.get(reverse("admin_dishes")).content.decode()
        self.assertIn("Burger", page)
        self.assertIn("Gluten · Dairy", page)
        self.assertIn("No recipe", page)  # tea
        nomenu = self.client.get(reverse("admin_dishes") + "?show=nomenu").context["l"]
        self.assertEqual(nomenu["sections"], [])
        self.menu.dishes.remove(self.tea)
        rows = [r["dish"] for s in self.client.get(reverse("admin_dishes") + "?show=nomenu").context["l"]["sections"] for r in s["rows"]]
        self.assertEqual(rows, [self.tea])
        by_category = self.client.get(reverse("admin_dishes") + "?by=category&q=coff").context["l"]
        self.assertEqual([s["name"] for s in by_category["sections"]], ["No category"])
        self.assertEqual(by_category["sections"][0]["rows"][0]["dish"], self.coffee)

    def test_basics_save_what_is_valid(self):
        mains = DishCategory.objects.create(name="Mains")
        self.act(self.burger, action="basics", title="Smash burger", price="15.25", description="Two patties",
                 ticket_name="smash", prep_minutes="7", station=self.bar.id, category=mains.id)
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertEqual((burger.title, burger.price, burger.ticket_name, burger.prep_minutes, burger.station, burger.category),
                         ("Smash burger", Decimal("15.25"), "SMASH", 7, self.bar, mains))
        self.act(self.burger, action="basics", title="  ", price="abc", prep_minutes="0", station="999", category="")
        burger.refresh_from_db()
        self.assertEqual((burger.title, burger.price, burger.prep_minutes, burger.station, burger.category),
                         ("Smash burger", Decimal("15.25"), 7, self.bar, None))

    def test_ticket_name_shows_on_kitchen_tickets(self):
        Dish.objects.filter(pk=self.burger.pk).update(ticket_name="BRGR")
        order = self.place([MenuFixture.dish(self.burger), MenuFixture.dish(self.tea)], timezone.now())
        names = [d["name"] for d in collect_order(Order.objects.get(pk=order.pk))["dishes"]]
        self.assertEqual(sorted(names), ["BRGR", "Tea"])

    def test_catering_breaks(self):
        self.act(self.burger, action="catering")
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertTrue(burger.catering)
        first = burger.catering_prices.get()
        self.assertEqual((first.quantity, first.price), (10, Decimal("13.25")))  # 92% of retail, to the quarter
        self.act(self.burger, action="tier_add")
        second = burger.catering_prices.last()
        self.assertEqual((second.quantity, second.price), (20, Decimal("12.75")))
        self.act(self.burger, action="tier_set", tier=second.id, quantity="25", price="11.95")
        self.assertEqual(burger.catering_price(9), Decimal("14.50"))
        self.assertEqual(burger.catering_price(10), Decimal("13.25"))
        self.assertEqual(burger.catering_price(30), Decimal("11.95"))
        self.act(self.burger, action="catering_min", min="12")
        notes = self.client.get(reverse("admin_dish", args=[self.burger.id])).context["e"]["catering"]["notes"]
        self.assertEqual(notes, ["The first break starts below the minimum order of 12."])
        self.act(self.burger, action="tier_remove", tier=first.id)
        self.assertEqual(list(burger.catering_prices.values_list("quantity", flat=True)), [25])

    def test_recipe_rows(self):
        self.act(self.tea, action="part_add", component=self.avocado.id)
        part = DishComponent.objects.get(dish=self.tea)
        self.act(self.tea, action="part_set", part=part.id, quantity="2.5")
        self.act(self.tea, action="part_adjustable", part=part.id)
        part.refresh_from_db()
        self.assertEqual((part.quantity, part.adjustable), (2.5, True))
        self.act(self.tea, action="part_set", part=part.id, quantity="0")  # ignored: remove it instead
        part.refresh_from_db()
        self.assertEqual(part.quantity, 2.5)
        self.act(self.tea, action="part_remove", part=part.id)
        self.assertFalse(DishComponent.objects.filter(dish=self.tea).exists())

    def test_modifier_groups_and_addons(self):
        self.act(self.tea, action="group_add")
        group = OptionGroup.objects.get(dish=self.tea)
        self.act(self.tea, action="group_rename", group=group.id, title="Milk")
        self.act(self.tea, action="option_add", group=group.id)
        first, second = group.options.all()
        self.assertTrue(first.is_default)
        self.act(self.tea, action="option_set", option=second.id, title="Oat", price="0.75")
        self.act(self.tea, action="option_default", option=second.id)
        second.refresh_from_db()
        self.assertEqual((second.title, second.price, second.is_default), ("Oat", Decimal("0.75"), True))
        self.act(self.tea, action="option_remove", option=second.id)
        self.assertTrue(group.options.get().is_default)  # someone is always the standard
        self.act(self.tea, action="addon_add", component=self.avocado.id)
        addon = Extra.objects.get(dish=self.tea)
        self.act(self.tea, action="addon_set", addon=addon.id, title="Avo", price="2")
        addon.refresh_from_db()
        self.assertEqual((addon.label, addon.price, addon.kind), ("Avo", Decimal("2.00"), Extra.ADDON))
        group.refresh_from_db()
        self.assertEqual(group.title, "Milk")
        # Another dish's options can't be touched from here
        self.act(self.tea, action="option_remove", option=self.large.id)
        self.assertTrue(Option.objects.filter(pk=self.large.pk).exists())

    def test_menus_availability_duplicate_and_archive(self):
        self.act(self.tea, action="menu", menu=self.menu.id)
        self.assertFalse(self.menu.dishes.filter(pk=self.tea.pk).exists())
        self.act(self.burger, action="availability", value="always")
        self.assertEqual(Dish.objects.get(pk=self.burger.pk).availability, "always")
        response = self.act(self.burger, action="duplicate")
        copy = Dish.objects.latest("id")
        self.assertRedirects(response, reverse("admin_dish", args=[copy.id]))
        self.assertEqual((copy.title, copy.availability, copy.menu.count()), ("Burger (copy)", "off", 0))
        self.assertEqual(copy.dishcomponent_set.count(), 3)
        self.assertEqual(copy.extras.count(), 2)
        response = self.act(self.burger, action="archive")
        self.assertRedirects(response, reverse("admin_dishes"))
        burger = Dish.objects.get(pk=self.burger.pk)
        self.assertTrue(burger.archived)
        self.assertFalse(burger.available)
        self.assertFalse(burger.menu.exists())
        self.assertNotIn(burger, [r["dish"] for s in self.client.get(reverse("admin_dishes")).context["l"]["sections"] for r in s["rows"]])
        self.act(self.burger, action="restore")
        self.assertFalse(Dish.objects.get(pk=self.burger.pk).archived)

    def test_new_dish_and_editor_render(self):
        response = self.client.post(reverse("admin_dish_new"))
        dish = Dish.objects.latest("id")
        self.assertRedirects(response, reverse("admin_dish", args=[dish.id]))
        self.assertEqual(dish.availability, "off")
        for target in (dish, self.burger, self.coffee):
            self.assertEqual(self.client.get(reverse("admin_dish", args=[target.id])).status_code, 200)

    def test_last_week_counts_combos_in_sold_only(self):
        now = timezone.now()
        self.place([MenuFixture.dish(self.coffee, qty=2)], now)
        self.place([self.lunch(self.coffee)], now)
        week = self.client.get(reverse("admin_dish", args=[self.coffee.id])).context["e"]["week"]
        self.assertEqual(week["sold"], 3)
        self.assertEqual(week["sales"], Decimal("5.00"))


class UnitTests(TestCase):
    def test_big_numbers_show_in_the_bigger_unit(self):
        self.assertEqual(fmt_qty(2500, "g"), "2.5 kg")
        self.assertEqual(fmt_qty(400, "ml"), "400 ml")
        self.assertEqual(fmt_qty(12000, "ml"), "12 L")
        self.assertEqual(fmt_qty(12, "ea"), "12 ea")
