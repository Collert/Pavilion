"""
Tests for order creation, pricing, stock, station status transitions and the KDS.

Run with: venv\\Scripts\\python manage.py test
Square is never called: payments.square functions are patched where they'd be used.
"""
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from gift_cards.models import GiftCard, GiftCardAuthorization
from online_store.models import RejectedOrder
from payments.models import PaymentAuthorization, Transaction
from .models import Component, Dish, DishComponent, Menu, Order, OrderStation, Station
from .orders import OrderError, create_order, price_cart


class MenuFixture:
    """A tiny menu: perogies (kitchen), coffee/tea (bar), breakfast with a free drink, and a pay-per-pick drink."""

    @classmethod
    def build_menu(cls):
        cls.kitchen = Station.objects.get(code="kitchen")
        cls.bar = Station.objects.get(code="bar")

        def component(title, inventory, option="buy"):
            return Component.objects.create(title=title, unit_of_measurement="ea", type="food",
                                            crafting_option=option, inventory=inventory)

        def dish(title, price, station, parts=()):
            obj = Dish.objects.create(title=title, price=Decimal(price), station=station)
            for comp, qty in parts:
                DishComponent.objects.create(dish=obj, component=comp, quantity=qty)
            return obj

        cls.perogy = component("Perogy", 100)
        cls.coffee_shot = component("Coffee shot", 0, option="auto")
        cls.perogies = dish("Perogies", "9.50", cls.kitchen, [(cls.perogy, 8)])
        cls.coffee = dish("Coffee", "2.50", cls.bar, [(cls.coffee_shot, 1)])
        cls.tea = dish("Tea", "2.00", cls.bar)

        drink = component("Breakfast drink", 0)
        drink.child_dishes.set([cls.coffee, cls.tea])
        cls.breakfast = dish("Breakfast", "12.00", cls.kitchen, [(drink, 1)])
        # Breakfast also needs perogies, so it isn't an "only choices" dish
        DishComponent.objects.create(dish=cls.breakfast, component=cls.perogy, quantity=2)

        pick = component("Hot drink choice", 0)
        pick.child_dishes.set([cls.coffee, cls.tea])
        cls.hot_drink = dish("Hot drink", "0.00", cls.bar, [(pick, 1)])

        cls.menu = Menu.objects.create(title="Test menu", is_active=True)
        cls.menu.dishes.set([cls.perogies, cls.coffee, cls.tea, cls.breakfast, cls.hot_drink])

    @staticmethod
    def item(dish, parent=None):
        return {"pk": dish.id, **({"parent": parent.id} if parent else {})}


class PricingTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def test_plain_dishes_are_priced_from_the_database(self):
        counts, total = price_cart([self.item(self.perogies), self.item(self.perogies), self.item(self.coffee)])
        self.assertEqual(total, Decimal("21.50"))
        self.assertEqual(counts[self.perogies], 2)

    def test_choice_child_is_free_with_its_parent(self):
        counts, total = price_cart([self.item(self.breakfast), self.item(self.tea, parent=self.breakfast)])
        self.assertEqual(total, Decimal("12.00"))
        self.assertEqual(counts[self.tea], 1)

    def test_only_choice_dish_charges_the_pick_and_is_not_prepared(self):
        counts, total = price_cart([self.item(self.hot_drink), self.item(self.coffee, parent=self.hot_drink)])
        self.assertEqual(total, Decimal("2.50"))
        self.assertNotIn(self.hot_drink, counts)

    def test_free_picks_are_limited(self):
        with self.assertRaises(OrderError):
            price_cart([self.item(self.breakfast), self.item(self.tea, parent=self.breakfast),
                        self.item(self.coffee, parent=self.breakfast)])

    def test_free_pick_needs_its_parent_in_the_cart(self):
        with self.assertRaises(OrderError):
            price_cart([self.item(self.tea, parent=self.breakfast)])

    def test_child_must_be_an_option_of_the_parent(self):
        with self.assertRaises(OrderError):
            price_cart([self.item(self.breakfast), self.item(self.perogies, parent=self.breakfast)])

    def test_out_of_stock_dish_is_rejected(self):
        Dish.objects.filter(pk=self.tea.pk).update(in_stock=False)
        with self.assertRaises(OrderError):
            price_cart([self.item(self.tea)])

    def test_unknown_dish_is_rejected(self):
        with self.assertRaises(OrderError):
            price_cart([{"pk": 999999}])


class CreateOrderTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def test_pos_order_creates_rows_and_statuses(self):
        order = create_order(items=[self.item(self.perogies), self.item(self.perogies), self.item(self.coffee)],
                             channel="store", initial_status=OrderStation.STATUS_APPROVED, name="Olena")
        self.assertEqual(order.total, Decimal("21.50"))
        self.assertFalse(order.picked_up)
        self.assertEqual(order.station_status_map(), {"kitchen": 1, "bar": 1})
        self.assertEqual(order.orderdish_set.get(dish=self.perogies).quantity, 2)

    def test_stock_is_taken_per_quantity(self):
        create_order(items=[self.item(self.perogies)] * 3, channel="store", initial_status=OrderStation.STATUS_APPROVED)
        self.perogy.refresh_from_db()
        self.assertEqual(self.perogy.inventory, 100 - 3 * 8)

    def test_dish_goes_out_of_stock_when_component_runs_low(self):
        create_order(items=[self.item(self.perogies)] * 12, channel="store", initial_status=OrderStation.STATUS_APPROVED)
        self.perogies.refresh_from_db()
        self.assertFalse(self.perogies.in_stock)  # 4 perogies left, the dish needs 8

    def test_self_crafting_component_never_blocks_its_dish(self):
        create_order(items=[self.item(self.coffee)] * 5, channel="store", initial_status=OrderStation.STATUS_APPROVED)
        self.coffee.refresh_from_db()
        self.assertTrue(self.coffee.in_stock)

    def test_discount(self):
        order = create_order(items=[self.item(self.perogies)], channel="store", initial_status=1,
                             discount_amount="1.50", discount_percent="10")
        self.assertEqual(order.total, Decimal("7.05"))  # 9.50 - 1.50 - 0.95
        self.assertEqual(order.discount, Decimal("2.45"))

    def test_gift_card_partial_payment_with_cents(self):
        card = GiftCard.objects.create(available_balance=Decimal("10.00"))
        order = create_order(items=[self.item(self.perogies)], channel="store", initial_status=1,
                             gift_card_payments=[(card.number, 3.5)])
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("6.50"))
        self.assertEqual(GiftCardAuthorization.objects.get(order=order).charged_balance, Decimal("3.50"))

    def test_failed_order_rolls_everything_back(self):
        card = GiftCard.objects.create(available_balance=Decimal("1.00"))
        with self.assertRaises(OrderError):
            create_order(items=[self.item(self.perogies)], channel="store", initial_status=1,
                         gift_card_payments=[(card.number, "5.00")])
        self.assertFalse(Order.objects.exists())
        self.perogy.refresh_from_db()
        self.assertEqual(self.perogy.inventory, 100)

    def test_payment_check_can_block_the_order(self):
        def unpaid(remaining):
            raise OrderError("unpaid")
        with self.assertRaises(OrderError):
            create_order(items=[self.item(self.tea)], channel="web", initial_status=0, verify_payment=unpaid)
        self.assertFalse(Order.objects.exists())

    def test_prep_time_set_when_last_station_finishes(self):
        order = create_order(items=[self.item(self.perogies), self.item(self.tea)], channel="store", initial_status=1)
        order.set_station_status(["kitchen"], OrderStation.STATUS_DONE)
        self.assertIsNone(Order.objects.get(pk=order.pk).prep_time)
        order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        self.assertIsNotNone(Order.objects.get(pk=order.pk).prep_time)

    def test_order_ready_signal_fires_once(self):
        from .signals import order_ready
        received = []
        handler = lambda sender, order, **kw: received.append(order.pk)
        order_ready.connect(handler)
        try:
            order = create_order(items=[self.item(self.perogies), self.item(self.tea)], channel="store", initial_status=1)
            order.set_station_status(["kitchen"], OrderStation.STATUS_DONE)
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        finally:
            order_ready.disconnect(handler)
        self.assertEqual(received, [order.pk])


class MenuTests(TestCase):
    def test_only_one_menu_is_active(self):
        first = Menu.objects.create(title="A", is_active=True)
        second = Menu.objects.create(title="B", is_active=True)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertEqual(Menu.active(), second)


@override_settings(ENFORCE_DEVICE_CHECK=False)
class KitchenDisplayTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.staff = User.objects.create_user("cook", password="x", is_staff=True)

    def setUp(self):
        self.client.force_login(self.staff)
        self.url = reverse("order_marking")

    def online_order(self, **fields):
        return create_order(items=[self.item(self.perogies), self.item(self.tea)], channel="web",
                            initial_status=OrderStation.STATUS_PENDING, **fields)

    def send(self, method, body):
        return getattr(self.client, method)(self.url, json.dumps(body), content_type="application/json")

    def test_screen_lists_orders_for_its_stations(self):
        self.online_order()
        response = self.client.get(self.url, {"filter": "bar"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["orders"]), 1)
        response = self.client.get(self.url, {"filter": "gng"})
        self.assertEqual(len(response.context["orders"]), 0)

    def test_approve_done_pickup(self):
        order = self.online_order()
        self.send("post", {"orderId": order.id, "action": "approve", "filters": ["kitchen"]})
        self.assertEqual(Order.objects.get(pk=order.pk).station_status_map(), {"kitchen": 1, "bar": 0})
        self.send("post", {"orderId": order.id, "action": "approve", "filters": ["bar"]})
        self.send("put", {"orderId": order.id, "filters": ["kitchen", "bar"]})
        self.assertEqual(Order.objects.get(pk=order.pk).station_status_map(), {"kitchen": 2, "bar": 2})
        self.send("delete", {"orderId": order.id})
        self.assertTrue(Order.objects.get(pk=order.pk).picked_up)

    def test_done_does_not_skip_approval(self):
        order = self.online_order()
        self.send("put", {"orderId": order.id, "filters": ["kitchen"]})
        self.assertEqual(Order.objects.get(pk=order.pk).station_status_map()["kitchen"], 0)

    @mock.patch("payments.square.capture_authorization")
    def test_payment_captured_once_every_station_approves(self, capture):
        transaction = Transaction.objects.create(amount=Decimal("11.50"), successful=True)
        auth = PaymentAuthorization.objects.create(payment_id="p1", amount=1150, currency="CAD", transaction=transaction)
        order = self.online_order(authorization=auth)
        self.send("post", {"orderId": order.id, "action": "approve", "filters": ["kitchen"]})
        capture.assert_not_called()
        self.send("post", {"orderId": order.id, "action": "approve", "filters": ["bar"]})
        capture.assert_called_once_with(auth)

    @mock.patch("payments.square.void_authorization")
    def test_reject_refunds_and_deletes(self, void):
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        transaction = Transaction.objects.create(amount=Decimal("5.00"), successful=True)
        auth = PaymentAuthorization.objects.create(payment_id="p2", amount=500, currency="CAD", transaction=transaction)
        order = self.online_order(authorization=auth, gift_card_payments=[(card.number, "6.50")])
        response = self.send("post", {"orderId": order.id, "action": "delete", "filters": ["kitchen"],
                                      "rejection": {"reasons": ["out-of-stock"], "reasonExtra": ""}})
        self.assertEqual(response.status_code, 200)
        void.assert_called_once_with(auth)
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        self.assertTrue(RejectedOrder.objects.filter(order_id=order.id).exists())
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("20.00"))

    @mock.patch("payments.square.void_authorization", side_effect=Exception("Square down"))
    def test_reject_keeps_order_if_refund_fails(self, void):
        transaction = Transaction.objects.create(amount=Decimal("11.50"), successful=True)
        auth = PaymentAuthorization.objects.create(payment_id="p3", amount=1150, currency="CAD", transaction=transaction)
        order = self.online_order(authorization=auth)
        response = self.send("post", {"orderId": order.id, "action": "delete", "filters": ["kitchen"], "rejection": {"reasons": ["no-containers"]}})
        self.assertEqual(response.status_code, 502)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())


@override_settings(ENFORCE_DEVICE_CHECK=False)
class PosRegisterTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.staff = User.objects.create_user("cashier", password="x", is_staff=True)

    def test_register_creates_an_approved_order(self):
        self.client.force_login(self.staff)
        cart = {"items": [self.item(self.breakfast), self.item(self.coffee, parent=self.breakfast)],
                "partialPayments": [], "discounts": {"amount": 0, "percent": 0}}
        response = self.client.post(reverse("pos"), json.dumps({"cart": cart, "instructions": "", "toGo": False, "name": "Taras"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get()
        self.assertEqual(order.total, Decimal("12.00"))
        self.assertEqual(order.station_status_map(), {"kitchen": 1, "bar": 1})

    def test_register_reports_order_errors(self):
        self.client.force_login(self.staff)
        cart = {"items": [{"pk": 424242}], "partialPayments": []}
        response = self.client.post(reverse("pos"), json.dumps({"cart": cart, "name": ""}), content_type="application/json")
        self.assertEqual(response.status_code, 400)


class ActiveOrdersFeedTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def test_anonymous_feed_is_redacted(self):
        create_order(items=[self.item(self.tea)], channel="web", initial_status=0, phone=6045550100, name="Iryna")
        data = self.client.get(reverse("active_orders")).json()
        self.assertEqual(data[0]["name"], "Iryna")
        self.assertNotIn("phone", data[0])
        self.assertNotIn("payment_id", data[0])
        self.assertEqual(data[0]["stations"], {"bar": 0})


@override_settings(ENFORCE_DEVICE_CHECK=False)
class AccessTests(MenuFixture, TestCase):
    """Customers can register accounts, so staff pages must check is_staff, not just login."""

    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.customer = User.objects.create_user("customer", password="x")

    def test_customer_cannot_reach_staff_pages(self):
        self.client.force_login(self.customer)
        for name, kwargs in [("pos", {}), ("order_marking", {}), ("pos_output", {}), ("create_menu", {}),
                             ("component_availability", {}), ("crafting", {}), ("utils_get_image_colors", {}),
                             ("admin-menus-list", {}), ("admin-menu-instance", {"id": self.menu.id}),
                             ("admin-dashboard-history", {})]:
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertIn(response.status_code, (302, 403), name)

    def test_customer_cannot_use_staff_apis(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("check_inventory")).status_code, 403)
        response = self.client.post(reverse("check_su"), json.dumps({"username": "a", "password": "b"}), content_type="application/json")
        self.assertEqual(response.status_code, 403)


class SeedDemoTests(TestCase):
    """seed_demo must keep working on a fresh database (CLAUDE.md: update it with every model change)."""

    def test_seed_demo_runs(self):
        from django.core.management import call_command
        from io import StringIO
        with self.settings(MEDIA_ROOT=__import__("tempfile").mkdtemp()):
            call_command("seed_demo", allow_production=True, stdout=StringIO())
        self.assertTrue(Order.objects.filter(channel="delivery").exists())
        self.assertEqual(Menu.active().title, "Sunday brunch")
