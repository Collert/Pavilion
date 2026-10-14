import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from pos_server.models import Component, ComponentIngredient, Dish, DishComponent, Extra, Ingredient, Menu, Station
from pos_server.orders import OrderError, create_order

from .models import CookingSession, Recipe, RecipeIngredient, RecipeStep, Transformation


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


@override_settings(ENFORCE_DEVICE_CHECK=False)
class CookeryTests(TestCase):
    """Cookery: batches from recipes, one-at-a-time making, shared sessions with timers, and transforms."""

    @classmethod
    def setUpTestData(cls):
        cls.cook = User.objects.create_user("cook", password="x", is_staff=True, first_name="Priya")
        cls.other = User.objects.create_user("other", password="x", is_staff=True, first_name="Dana")
        cls.flour = Ingredient.objects.create(title="Flour", unit_of_measurement="g", inventory=1000)
        cls.water = Ingredient.objects.create(title="Water", unit_of_measurement="ml", inventory=0, unlimited=True)
        cls.beef = Ingredient.objects.create(title="Beef", unit_of_measurement="g", inventory=1000)
        cls.recipe = Recipe.objects.create(yield_quantity=60)
        flour = RecipeIngredient.objects.create(recipe=cls.recipe, ingredient=cls.flour, quantity=300)
        RecipeIngredient.objects.create(recipe=cls.recipe, ingredient=cls.water, quantity=500)
        RecipeStep.objects.create(recipe=cls.recipe, position=0, title="Mix", text="Mix it.").uses.set([flour])
        RecipeStep.objects.create(recipe=cls.recipe, position=1, title="Rest", duration=timedelta(minutes=30), scene="rest")
        cls.pierogi = Component.objects.create(title="Pierogi", unit_of_measurement="ea", type="food", crafting_option="craft",
                                               inventory=10, recipe=cls.recipe, par=100)
        cls.patty = Component.objects.create(title="Patty", unit_of_measurement="ea", type="food", crafting_option="craft", inventory=0)
        ComponentIngredient.objects.create(component=cls.patty, ingredient=cls.beef, quantity=120)
        cls.frozen = Component.objects.create(title="Pierogi, frozen", unit_of_measurement="ea", type="food",
                                              crafting_option="transform", inventory=0)
        cls.freeze = Transformation.objects.create(name="Freeze pierogi", from_component=cls.pierogi, to_component=cls.frozen,
                                                   duration=timedelta(hours=2))

    def setUp(self):
        self.client.force_login(self.cook)
        self.url = reverse("cookery_data")

    def act(self, action, **body):
        return self.client.post(self.url, json.dumps({"action": action, **body}), content_type="application/json")

    def fresh(self, obj):
        return type(obj).objects.get(pk=obj.pk)

    def test_snapshot(self):
        data = self.client.get(self.url).json()
        self.assertEqual([c["name"] for c in data["components"] if c["makeable"]], ["Patty", "Pierogi"])
        self.assertIn("Pierogi, frozen", [c["name"] for c in data["components"]])  # it's in a transform
        recipe = data["recipes"][str(self.recipe.id)]
        self.assertEqual([s["title"] for s in recipe["steps"]], ["Mix", "Rest"])
        self.assertEqual(recipe["steps"][1]["dur"], 1800)
        self.assertEqual(len(recipe["steps"][0]["uses"]), 1)

    def test_recipe_batch_takes_ingredients_then_adds_what_was_made(self):
        session = self.act("start", component=self.pierogi.id, batch=2).json()["session"]
        self.assertEqual(self.fresh(self.flour).inventory, 400)  # 2 x 300 g
        self.assertEqual(self.fresh(self.water).inventory, 0)  # unlimited: not counted
        self.act("step", session=session, step=1)
        self.act("timer", session=session, step=1, op="start")
        timers = CookingSession.objects.get(pk=session).timers
        self.assertEqual(timers["1"]["dur"], 1800)
        self.act("timer", session=session, step=1, op="pause")
        self.assertIsNotNone(CookingSession.objects.get(pk=session).timers["1"]["paused"])
        self.act("count", session=session, amount=50)
        self.act("count", session=session, amount=5)
        self.act("uncount", session=session)
        response = self.act("finish", session=session).json()
        self.assertEqual((response["done"]["made"], response["done"]["expected"]), (50, 120))
        self.assertEqual(self.fresh(self.pierogi).inventory, 60)
        self.assertEqual(response["sessions"], [])

    def test_one_at_a_time_takes_ingredients_per_unit_at_the_end(self):
        session = self.act("start", component=self.patty.id).json()["session"]
        self.assertEqual(CookingSession.objects.get(pk=session).phase, "yield")
        self.assertEqual(self.fresh(self.beef).inventory, 1000)
        self.act("count", session=session, amount=3)
        self.act("finish", session=session)
        self.assertEqual((self.fresh(self.patty).inventory, self.fresh(self.beef).inventory), (3, 640))

    def test_anyone_can_pick_up_a_batch(self):
        session = self.act("start", component=self.pierogi.id).json()["session"]
        self.client.force_login(self.other)
        holder = [s["holder"]["name"] for s in self.act("take", session=session).json()["sessions"]]
        self.assertEqual(holder, ["Dana"])

    def test_cannot_finish_empty_or_twice(self):
        session = self.act("start", component=self.patty.id).json()["session"]
        self.assertEqual(self.act("finish", session=session).status_code, 400)
        self.act("count", session=session, amount=1)
        self.act("finish", session=session)
        self.assertEqual(self.act("finish", session=session).status_code, 400)
        self.assertEqual(self.act("count", session=session, amount=-1).status_code, 400)

    def test_transform_moves_stock(self):
        session = self.act("transform", transform=self.freeze.id, qty=4).json()["session"]
        self.assertEqual((self.fresh(self.pierogi).inventory, self.fresh(self.frozen).inventory), (6, 0))
        self.act("transform_done", session=session)
        self.assertEqual(self.fresh(self.frozen).inventory, 4)
        session = self.act("transform", transform=self.freeze.id, qty=2).json()["session"]
        self.act("transform_cancel", session=session)
        self.assertEqual(self.fresh(self.pierogi).inventory, 6)
        self.assertEqual(self.act("transform", transform=self.freeze.id, qty=99).status_code, 400)  # only 6 on hand

    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(reverse("crafting")).status_code, 302)
