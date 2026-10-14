import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from pos_server.models import Component, Dish, DishComponent, Extra, Menu, Station
from pos_server.orders import OrderError, create_order


@override_settings(ENFORCE_DEVICE_CHECK=False)
class AvailabilityTests(TestCase):
    """Off / Auto / Always on dishes and components, and the Availability panel's endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("cook", password="x", is_staff=True)
        kitchen = Station.objects.get(code="kitchen")

        def component(title, inventory):
            return Component.objects.create(title=title, unit_of_measurement="ea", type="food", crafting_option="buy", inventory=inventory)

        cls.patty = component("Patty", 10)
        cls.bun = component("Bun", 0)
        cls.ketchup = component("Ketchup", 50)
        cls.burger = Dish.objects.create(title="Burger", price=Decimal("10.00"), station=kitchen)
        DishComponent.objects.create(dish=cls.burger, component=cls.patty, quantity=1)
        DishComponent.objects.create(dish=cls.burger, component=cls.bun, quantity=1)
        cls.salad = Dish.objects.create(title="Salad", price=Decimal("8.00"), station=kitchen)
        Extra.objects.create(dish=cls.salad, component=cls.ketchup, kind=Extra.CONDIMENT)
        Menu.objects.create(title="Menu", is_active=True).dishes.set([cls.burger, cls.salad])

    def setUp(self):
        self.client.force_login(self.staff)

    def fresh(self, obj):
        return type(obj).objects.get(pk=obj.pk)

    def set_mode(self, obj, mode):
        obj = self.fresh(obj)
        obj.availability = mode
        obj.save()
        return obj

    def test_auto_follows_inventory(self):
        self.assertFalse(self.fresh(self.burger).available)  # no buns
        self.set_mode(self.bun, "auto")
        bun = self.fresh(self.bun)
        bun.inventory = 5
        bun.save()
        self.assertTrue(self.fresh(self.burger).available)

    def test_always_sells_past_zero(self):
        self.set_mode(self.bun, "always")  # the component keeps going...
        self.assertTrue(self.fresh(self.burger).available)
        self.set_mode(self.bun, "auto")
        self.set_mode(self.burger, "always")  # ...or the dish does
        self.assertTrue(self.fresh(self.burger).available)
        create_order(lines=[{"type": "dish", "id": self.burger.id, "qty": 1}], channel="store", initial_status=1)
        self.assertEqual(self.fresh(self.bun).inventory, -1)

    def test_off_stops_selling_and_blocks_dishes(self):
        self.set_mode(self.salad, "off")
        self.assertFalse(self.fresh(self.salad).available)
        with self.assertRaises(OrderError):
            create_order(lines=[{"type": "dish", "id": self.salad.id, "qty": 1}], channel="store", initial_status=1)
        # A component turned off blocks its dishes, even ones set to Always, and its stock doesn't bring it back
        self.set_mode(self.burger, "always")
        self.set_mode(self.patty, "off")
        burger = self.fresh(self.burger)
        self.assertTrue(burger.blocked)
        self.assertFalse(burger.available)
        patty = self.fresh(self.patty)
        patty.inventory = 100
        patty.save()
        self.assertFalse(self.fresh(self.patty).in_stock)
        self.set_mode(self.patty, "auto")
        self.assertTrue(self.fresh(self.burger).available)

    def test_catalog(self):
        self.set_mode(self.patty, "off")
        data = self.client.get(reverse("availability_data")).json()["tabs"]
        burger = data["dishes"][0]["items"][0]
        self.assertEqual((burger["name"], burger["mode"], burger["blocked_by"], burger["stock"]), ("Burger", "auto", ["Patty"], 0))
        self.assertEqual([i["name"] for g in data["parts"] for i in g["items"]], ["Bun", "Patty"])
        self.assertEqual([i["name"] for g in data["condiments"] for i in g["items"]], ["Ketchup"])

    def test_changes_and_undo(self):
        url = reverse("availability_data")
        body = {"changes": [{"kind": "component", "id": self.patty.id, "mode": "off"}, {"kind": "dish", "id": self.salad.id, "mode": "always"}]}
        response = self.client.post(url, json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.fresh(self.patty).availability, self.fresh(self.salad).availability), ("off", "always"))
        undo = {"changes": [{"kind": "component", "id": self.patty.id, "mode": "auto"}, {"kind": "dish", "id": self.salad.id, "mode": "auto"}]}
        self.client.post(url, json.dumps(undo), content_type="application/json")
        self.assertEqual((self.fresh(self.patty).availability, self.fresh(self.salad).availability), ("auto", "auto"))

    def test_bad_changes_change_nothing(self):
        body = {"changes": [{"kind": "dish", "id": self.salad.id, "mode": "off"}, {"kind": "dish", "id": self.burger.id, "mode": "sometimes"}]}
        response = self.client.post(reverse("availability_data"), json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fresh(self.salad).availability, "auto")

    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("availability_data")).status_code, 401)
        self.assertEqual(self.client.get(reverse("component_availability")).status_code, 302)

    def test_panel_on_its_page_and_the_kitchen_display(self):
        for name in ("component_availability", "order_marking"):
            self.assertContains(self.client.get(reverse(name)), 'data-avail data-url="%s"' % reverse("availability_data"))
