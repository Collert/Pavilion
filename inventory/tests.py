import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from pos_server.models import Component, ComponentIngredient, Dish, DishComponent, Extra, Ingredient, Menu, Station
from pos_server.orders import OrderError, create_order

from .models import (CookingSession, IngredientReservation, Recipe, RecipeIngredient, RecipeStep, Transformation,
                     WasteIngredient)


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
    """Cookery pages: batches from recipes, one-at-a-time making, shared batches with timers, and transforms."""

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

    def fresh(self, obj):
        return type(obj).objects.get(pk=obj.pk)

    def start(self, component, **form):
        response = self.client.post(reverse("cookery_start", args=[component.id]), form)
        session = CookingSession.objects.latest("id")
        self.assertRedirects(response, reverse("cookery_batch", args=[session.id]), fetch_redirect_response=False)
        return session

    def post(self, session, **form):
        return self.client.post(reverse("cookery_batch", args=[session.id]), form)

    def test_pages(self):
        home = self.client.get(reverse("crafting"))
        self.assertContains(home, "Pierogi")
        self.assertContains(home, 'data-region="panel"')
        self.assertNotContains(home, "Pierogi, frozen")  # made by a transform, not here
        self.assertContains(self.client.get(reverse("cookery_transforms")), "Freeze pierogi")
        recipe = self.client.get(reverse("cookery_recipe", args=[self.pierogi.id]), {"batch": 2})
        self.assertContains(recipe, "Mix")
        self.assertContains(recipe, "600 g")  # 2 batches x 300 g flour
        self.assertContains(self.client.get(reverse("cookery_transform", args=[self.freeze.id])), "How many?")

    def test_recipe_batch_reserves_ingredients_and_logs_the_shortfall_as_waste(self):
        session = self.start(self.pierogi, batch=2, phase="guided")
        self.assertEqual(self.fresh(self.flour).inventory, 400)  # 2 x 300 g reserved, out of inventory
        self.assertEqual(self.fresh(self.water).inventory, 0)  # unlimited: not counted or reserved
        self.assertEqual(list(IngredientReservation.objects.values_list("ingredient__title", "quantity")), [("Flour", 600)])
        self.assertContains(self.client.get(reverse("cookery_recipe", args=[self.pierogi.id])), "in batches cooking")
        self.assertContains(self.client.get(reverse("cookery_batch", args=[session.id])), "Step 1 of 2")

        self.post(session, action="step:1")
        self.post(session, action="timer:start", step=1)
        self.assertEqual(self.fresh(session).timers["1"]["dur"], 1800)
        self.assertContains(self.client.get(reverse("cookery_batch", args=[session.id])), "data-ends=")
        self.post(session, action="timer:pause", step=1)
        self.assertIsNotNone(self.fresh(session).timers["1"]["paused"])

        self.post(session, action="phase:yield")
        self.assertContains(self.client.get(reverse("cookery_batch", args=[session.id])), "Recording yield")
        self.post(session, action="count", amount="50")
        self.post(session, action="count", amount="5")
        self.post(session, action="uncount")
        response = self.post(session, action="finish")
        self.assertRedirects(response, reverse("cookery_done", args=[session.id]), fetch_redirect_response=False)
        self.assertEqual(self.fresh(self.pierogi).inventory, 60)
        # 70 of the expected 120 weren't made: that share of the reserved flour is waste
        self.assertEqual(list(WasteIngredient.objects.values_list("ingredient__title", "quantity", "session")), [("Flour", 350, session.id)])
        done = self.client.get(reverse("cookery_done", args=[session.id]))
        self.assertContains(done, "Yay! You made 50 pierogi")
        self.assertContains(done, "350 g flour")

    def test_no_waste_when_the_batch_makes_enough(self):
        session = self.start(self.pierogi, phase="yield")
        self.post(session, action="count", amount="64")
        self.post(session, action="finish")
        self.assertFalse(WasteIngredient.objects.exists())

    def test_one_at_a_time_takes_ingredients_per_unit_at_the_end(self):
        session = self.start(self.patty)
        self.assertEqual(session.phase, "yield")
        self.assertEqual(self.fresh(self.beef).inventory, 1000)
        self.post(session, action="count", amount="3")
        self.post(session, action="finish")
        self.assertEqual((self.fresh(self.patty).inventory, self.fresh(self.beef).inventory), (3, 640))

    def test_amounts_are_typed_in_friendly_units(self):
        sauce = Component.objects.create(title="Sauce", unit_of_measurement="ml", type="food", crafting_option="craft")
        session = self.start(sauce)
        self.post(session, action="count", amount="1.5")  # litres
        self.assertEqual(session.log.get().amount, 1500)

    def test_anyone_can_pick_up_a_batch(self):
        session = self.start(self.pierogi)
        self.client.force_login(self.other)
        response = self.client.post(reverse("cookery_take", args=[session.id]))
        self.assertRedirects(response, reverse("cookery_batch", args=[session.id]), fetch_redirect_response=False)
        self.assertEqual(self.fresh(session).holder, self.other)

    def test_mistakes_become_messages_not_errors(self):
        session = self.start(self.patty)
        response = self.post(session, action="finish")  # nothing counted
        self.assertRedirects(response, reverse("cookery_batch", args=[session.id]), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("cookery_batch", args=[session.id])), "Count what you made first.")
        self.post(session, action="count", amount="nope")
        self.assertFalse(session.log.exists())

    def test_transform_moves_stock(self):
        self.client.post(reverse("cookery_transform", args=[self.freeze.id]), {"qty": "4"})
        session = CookingSession.objects.get(kind="transform")
        self.assertEqual((self.fresh(self.pierogi).inventory, self.fresh(self.frozen).inventory), (6, 0))
        self.assertContains(self.client.get(reverse("cookery_transforming", args=[session.id])), "Time left")
        self.client.post(reverse("cookery_transforming", args=[session.id]), {"action": "done"})
        self.assertEqual(self.fresh(self.frozen).inventory, 4)
        self.client.post(reverse("cookery_transform", args=[self.freeze.id]), {"qty": "2"})
        second = CookingSession.objects.filter(kind="transform").latest("id")
        self.client.post(reverse("cookery_transforming", args=[second.id]), {"action": "cancel"})
        self.assertEqual(self.fresh(self.pierogi).inventory, 6)
        self.client.post(reverse("cookery_transform", args=[self.freeze.id]), {"qty": "99"})  # more than on hand: all 6
        self.assertEqual(self.fresh(self.pierogi).inventory, 0)

    def test_staff_only(self):
        self.client.logout()
        for url in (reverse("crafting"), reverse("cookery_recipe", args=[self.pierogi.id])):
            self.assertEqual(self.client.get(url).status_code, 302)


class SeedRecipesTests(TestCase):
    def test_adds_the_mockup_recipes_once_and_keeps_existing_ones(self):
        from django.core.management import call_command
        from io import StringIO
        kept = Recipe.objects.create(yield_quantity=5)
        Component.objects.create(title="Smash sauce", unit_of_measurement="ml", type="food", crafting_option="craft", recipe=kept)
        call_command("seed_recipes", stdout=StringIO())
        self.assertEqual(Component.objects.get(title="Smash sauce").recipe, kept)  # had one: left alone
        pierogi = Component.objects.get(title="Pierogi, potato & cheese")
        self.assertEqual((pierogi.recipe.yield_quantity, pierogi.recipe.steps.count()), (60, 6))
        self.assertEqual(Transformation.objects.filter(name__in=["Freeze pierogi", "Thaw patties"]).count(), 2)
        counts = (Recipe.objects.count(), Component.objects.count(), Ingredient.objects.count())
        call_command("seed_recipes", stdout=StringIO())
        self.assertEqual((Recipe.objects.count(), Component.objects.count(), Ingredient.objects.count()), counts)


@override_settings(ENFORCE_DEVICE_CHECK=False)
class CookeryInPageTests(TestCase):
    def test_background_posts_get_the_page_back_without_a_redirect(self):
        cook = User.objects.create_user("cook", password="x", is_staff=True)
        patty = Component.objects.create(title="Patty", unit_of_measurement="ea", type="food", crafting_option="craft")
        session = CookingSession.objects.create(kind="cook", component=patty, holder=cook, phase="yield")
        self.client.force_login(cook)
        url = reverse("cookery_batch", args=[session.id])
        response = self.client.post(url, {"action": "count", "amount": "2"}, HTTP_SEC_FETCH_MODE="cors")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="disp yield-total" aria-live="polite">2<')
        navigation = self.client.post(url, {"action": "count", "amount": "1"}, HTTP_SEC_FETCH_MODE="navigate")
        self.assertRedirects(navigation, url, fetch_redirect_response=False)
        finished = self.client.post(url, {"action": "finish"}, HTTP_SEC_FETCH_MODE="cors")  # a new page: still redirects
        self.assertRedirects(finished, reverse("cookery_done", args=[session.id]), fetch_redirect_response=False)
