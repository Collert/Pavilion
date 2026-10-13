"""
Fill an empty database with demo data for local development and testing.

    python manage.py seed_demo            # refuses if there are already orders or dishes
    python manage.py seed_demo --force    # seed anyway (adds to what's there)

Creates users (admin/admin, cashier/cashier, courier/courier, customer/customer), stations, ingredients,
components, dishes (including choice dishes), an active and an inactive menu, today's business-day event,
promo banners, gift card presets and cards, a registered device, and orders in every state across the
last few days.

KEEP THIS UP TO DATE: whenever a model is added or changed, update this command so it still runs on a
fresh database and covers the new fields (see CLAUDE.md).
"""
import datetime
import io
import random
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from deliveries.models import Delivery
from events.models import Event
from gift_cards.models import CardPreset, GiftCard
from inventory.models import Packaging, Recipe
from online_store.models import PromoContent, RejectedOrder
from pos_server.models import (Component, ComponentIngredient, Dish, DishComponent, EligibleDevice, Ingredient,
                               Menu, Order, OrderStation, Station)
from pos_server.orders import create_order
from users.models import Staff

# A fixed token so a dev browser can be registered once: ?device-token=<this> on any POS page
DEMO_DEVICE_TOKEN = "00000000-0000-4000-8000-000000000001"


def placeholder_image(text, color, size=(800, 500)):
    """A solid-colour PNG with a label, so templates that need an image have one."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.text((size[0] // 2, size[1] // 2), text, fill="white", anchor="mm", font_size=min(size) // 8)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return ContentFile(buffer.getvalue())


class Command(BaseCommand):
    help = "Seed the database with demo data (dev only)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Seed even if the database already has data")
        parser.add_argument("--allow-production", action="store_true", help="Allow running with DEBUG off")

    def handle(self, *args, force=False, allow_production=False, **options):
        if not settings.DEBUG and not allow_production:
            raise CommandError("Refusing to seed demo data with DEBUG off. Pass --allow-production if you really mean it.")
        if not force and (Order.objects.exists() or Dish.objects.exists()):
            raise CommandError("The database already has dishes or orders. Use --force to seed anyway.")
        random.seed(42)
        with transaction.atomic():
            self.seed_users()
            self.seed_stations()
            self.seed_inventory()
            self.seed_menus()
            self.seed_events()
            self.seed_store()
            self.seed_gift_cards()
            EligibleDevice.objects.get_or_create(token=DEMO_DEVICE_TOKEN, defaults={"name": "Demo register"})
            Packaging.objects.get_or_create(name="Takeout container", defaults={"quantity": 200, "cost": Decimal("0.35")})
            self.seed_orders()
        self.stdout.write(self.style.SUCCESS(
            "Seeded. Log in as admin/admin. Register this browser as a device with "
            f"?device-token={DEMO_DEVICE_TOKEN} (only needed when ENFORCE_DEVICE_CHECK is on)."
        ))

    # ---------- people ----------

    def seed_users(self):
        def user(username, **flags):
            obj, created = User.objects.get_or_create(username=username, defaults={
                "first_name": username.title(), "email": f"{username}@example.com", **flags})
            if created:
                obj.set_password(username)
                obj.save()
            return obj
        user("admin", is_staff=True, is_superuser=True)
        for name in ("cashier", "courier"):
            Staff.objects.get_or_create(user=user(name, is_staff=True))
        customer = user("customer")
        customer.customer_profile.phone = 6045550100
        customer.customer_profile.home_address = "123 Columbia St, New Westminster, BC"
        customer.customer_profile.save()

    # ---------- menu ----------

    def seed_stations(self):
        defaults = [
            ("kitchen", "Kitchen", "skillet", "Hot food"),
            ("bar", "Bar", "local_cafe", "Drinks and sweets"),
            ("gng", "Grab & Go", "kitchen", "Grab & Go"),
        ]
        self.stations = {}
        for position, (code, name, icon, heading) in enumerate(defaults):
            self.stations[code], _ = Station.objects.get_or_create(code=code, defaults={
                "name": name, "icon": icon, "menu_heading": heading, "position": position})

    def seed_inventory(self):
        ingredient_specs = [
            # title, unit, inventory, allergen, unlimited, cost
            ("Eggs", "ea", 600, "Eggs", False, "0.30"),
            ("Flour", "g", 100000, "Wheat and triticale", False, "0.002"),
            ("Milk", "ml", 50000, "Milk", False, "0.002"),
            ("Potatoes", "g", 150000, None, False, "0.003"),
            ("Cheese", "g", 4000, "Milk", False, "0.02"),
            ("Beets", "g", 60000, None, False, "0.004"),
            ("Coffee beans", "g", 20000, None, False, "0.03"),
            ("Tea bags", "ea", 200, None, False, "0.10"),
            ("Water", "ml", 0, None, True, None),
            ("Sugar", "g", 5000, None, False, "0.002"),
        ]
        self.ingredients = {}
        for title, unit, inventory, allergen, unlimited, cost in ingredient_specs:
            self.ingredients[title], _ = Ingredient.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "inventory": inventory, "allergen": allergen,
                "unlimited": unlimited, "cost": Decimal(cost) if cost else None})

        recipe, _ = Recipe.objects.get_or_create(markdown_text="| Ingredient | Amount |\n|---|---|\n| Potatoes | 500 g |\n| Flour | 300 g |\n| Cheese | 100 g |", defaults={"original_yield": 12})

        component_specs = [
            # title, unit, type, crafting option, inventory, [(ingredient, qty)]
            ("Perogy", "ea", "food", "craft", 1200, [("Potatoes", 40), ("Flour", 25), ("Cheese", 8)]),
            ("Borscht", "ml", "food", "craft", 40000, [("Beets", 1), ("Potatoes", 0.5)]),
            ("Fried egg", "ea", "food", "auto", 0, [("Eggs", 1)]),
            ("Pancake", "ea", "food", "craft", 400, [("Flour", 60), ("Milk", 80), ("Eggs", 0.5)]),
            ("Coffee", "ml", "beverage", "auto", 0, [("Coffee beans", 0.06), ("Water", 1)]),
            ("Tea", "ml", "beverage", "auto", 0, [("Tea bags", 0.004), ("Water", 1)]),
            ("Honey cake slice", "ea", "food", "buy", 60, []),
            ("Frozen perogies bag", "ea", "food", "buy", 40, []),
        ]
        self.components = {}
        for title, unit, food_type, option, inventory, parts in component_specs:
            component, created = Component.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "type": food_type, "crafting_option": option, "inventory": inventory,
                "in_stock": inventory > 0 or option == "auto", "recipe": recipe if title == "Perogy" else None})
            if created:
                for ingredient, qty in parts:
                    ComponentIngredient.objects.create(component=component, ingredient=self.ingredients[ingredient], quantity=qty)
            self.components[title] = component

    def seed_menus(self):
        kitchen, bar, gng = self.stations["kitchen"], self.stations["bar"], self.stations["gng"]

        def dish(title, price, station, parts, description="", color="#8a3b12", visible=True):
            obj, created = Dish.objects.get_or_create(title=title, defaults={
                "price": Decimal(price), "station": station, "description": description, "visible_in_menu": visible})
            if created:
                obj.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, color), save=True)
                for component, qty in parts:
                    DishComponent.objects.create(dish=obj, component=self.components[component], quantity=qty)
            return obj

        coffee = dish("Coffee", "2.50", bar, [("Coffee", 250)], "Freshly brewed drip coffee.", "#4b2e1e")
        tea = dish("Tea", "2.00", bar, [("Tea", 250)], "Black or herbal.", "#6b8e23")
        perogies = dish("Perogies", "9.50", kitchen, [("Perogy", 8)], "Eight potato-and-cheese perogies with sour cream.")
        borscht = dish("Borscht", "7.00", kitchen, [("Borscht", 400)], "Ukrainian beet soup.", "#8b1e3f")
        pancakes = dish("Pancakes", "8.00", kitchen, [("Pancake", 3)], "Three fluffy pancakes.", "#c98b2b")
        cake = dish("Honey cake", "5.50", bar, [("Honey cake slice", 1)], "Medivnyk, layered honey cake.", "#d4a017")
        frozen = dish("Frozen perogies (24)", "18.00", gng, [("Frozen perogies bag", 1)], "Take home and cook yourself.", "#2f4f6f")

        # A "choice" component: the drink that comes with breakfast
        drink_choice, created = Component.objects.get_or_create(title="Breakfast drink", defaults={
            "unit_of_measurement": "ea", "type": "beverage", "crafting_option": "buy", "inventory": 0})
        if created:
            drink_choice.child_dishes.set([coffee, tea])
        breakfast = dish("Breakfast", "12.00", kitchen, [("Fried egg", 2), ("Pancake", 2)], "Two eggs, two pancakes and a drink.", "#e07a1f")
        if not breakfast.dishcomponent_set.filter(component=drink_choice).exists():
            DishComponent.objects.create(dish=breakfast, component=drink_choice, quantity=1)

        # An "only choices" dish: pick a hot drink, pay for what you pick
        hot_drink, created = Component.objects.get_or_create(title="Hot drink", defaults={
            "unit_of_measurement": "ea", "type": "beverage", "crafting_option": "buy", "inventory": 0})
        if created:
            hot_drink.child_dishes.set([coffee, tea])
        any_drink = dish("Hot drink", "0.00", bar, [], "Coffee or tea.", "#5a3d2b")
        if not any_drink.dishcomponent_set.exists():
            DishComponent.objects.create(dish=any_drink, component=hot_drink, quantity=1)

        self.dishes = {d.title: d for d in (coffee, tea, perogies, borscht, pancakes, cake, frozen, breakfast, any_drink)}

        def menu(title, active, dishes, color):
            obj, created = Menu.objects.get_or_create(title=title, defaults={
                "is_active": active, "background_color": color, "accent_1": "#f4e9d8", "accent_2": "#c0392b", "accent_3": "#2c3e50"})
            if created:
                obj.header_image.save("header.png", placeholder_image(title, color, (1200, 300)), save=False)
                obj.footer_image.save("footer.png", placeholder_image("Thank you!", color, (1200, 200)), save=True)
                obj.dishes.set(dishes)
            return obj

        menu("Sunday brunch", True, [coffee, tea, perogies, borscht, pancakes, cake, frozen, breakfast, any_drink], "#7a2e1a")
        menu("Christmas Eve supper", False, [borscht, perogies, cake, tea], "#1d3b2a")

    # ---------- store ----------

    def seed_events(self):
        now = timezone.localtime()
        today_open = now.replace(hour=0, minute=1, second=0, microsecond=0)
        today_close = now.replace(hour=23, minute=59, second=0, microsecond=0)
        Event.objects.get_or_create(title="Sunday café", start=today_open, defaults={
            "end": today_close, "location_title": "Parish hall", "location_address": "501 4th Ave, New Westminster, BC",
            "restaurant_open": True, "in_person_open": True, "online_open": True, "delivery_open": True})
        next_week = today_open + datetime.timedelta(days=7)
        Event.objects.get_or_create(title="Perogy fundraiser", start=next_week.replace(hour=11), defaults={
            "end": next_week.replace(hour=15), "restaurant_open": True, "in_person_open": True,
            "online_open": True, "delivery_open": False})

    def seed_store(self):
        for title, tagline, color in [("Perogy Sunday", "Fresh perogies every Sunday after liturgy", "#7a2e1a"),
                                      ("Gift cards", "Give the gift of borscht", "#1d3b2a")]:
            promo, created = PromoContent.objects.get_or_create(title=title, defaults={
                "tagline": tagline, "call_to_action": "Order now", "color": color, "accent": "#ffffff", "link": "/en/menu"})
            if created:
                promo.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, color), save=True)

    def seed_gift_cards(self):
        for amount, color in [(10, "#2c7a7b"), (25, "#6b46c1"), (50, "#c05621")]:
            preset, created = CardPreset.objects.get_or_create(name=f"${amount} card", defaults={"amount": Decimal(amount)})
            if created:
                preset.image.save(f"{amount}.png", placeholder_image(f"${amount}", color, (856, 540)), save=True)
        image = CardPreset.objects.first().image
        self.gift_cards = [
            GiftCard.objects.create(email="customer@example.com", image=image.name, available_balance=Decimal("50.00")),
            GiftCard.objects.create(email="friend@example.com", image=image.name, available_balance=Decimal("7.25")),
            GiftCard.objects.create(email="empty@example.com", image=image.name, available_balance=Decimal("0.00")),
        ]

    # ---------- orders ----------

    def seed_orders(self):
        d = self.dishes
        names = ["Olena", "Taras", "Mark", "Iryna", "Peter", "Sofia", "Andriy", None, None]
        now = timezone.now()

        def items(*titles, parent=None):
            return [{"pk": d[t].id, **({"parent": d[parent].id} if parent else {})} for t in titles]

        def order(cart, channel, status, minutes_ago, **fields):
            created = create_order(
                items=cart, channel=channel, initial_status=status,
                name=fields.pop("name", random.choice(names)), **fields)
            Order.objects.filter(pk=created.pk).update(timestamp=now - datetime.timedelta(minutes=minutes_ago),
                                                       start_time=now - datetime.timedelta(minutes=minutes_ago))
            created.refresh_from_db()
            return created

        # Past days: finished and picked up, so the history calendar and day stats have data
        for days_ago in (1, 2, 7):
            for _ in range(random.randint(6, 12)):
                cart = random.choice([items("Perogies", "Coffee"), items("Borscht"), items("Breakfast") + items("Tea", parent="Breakfast"),
                                      items("Pancakes", "Pancakes", "Tea"), items("Honey cake", "Coffee"), items("Frozen perogies (24)")])
                past = order(cart, "store", OrderStation.STATUS_APPROVED, days_ago * 24 * 60 + random.randint(0, 300))
                past.set_station_status([s.code for s in past.stations.all()], OrderStation.STATUS_DONE)
                Order.objects.filter(pk=past.pk).update(picked_up=True, prep_time=datetime.timedelta(minutes=random.randint(3, 15)))

        # Today, on the screens
        order(items("Perogies", "Coffee"), "store", OrderStation.STATUS_APPROVED, 6, name="Olena")
        order(items("Breakfast") + items("Coffee", parent="Breakfast"), "store", OrderStation.STATUS_APPROVED, 4, name="Taras",
              special_instructions="Eggs over easy")
        order(items("Hot drink") + items("Tea", parent="Hot drink") + items("Honey cake"), "store", OrderStation.STATUS_APPROVED, 2, to_go_order=True)
        ready = order(items("Borscht", "Tea"), "store", OrderStation.STATUS_APPROVED, 9, name="Mark")
        ready.set_station_status(["kitchen", "bar"], OrderStation.STATUS_DONE)
        order(items("Pancakes", "Coffee"), "web", OrderStation.STATUS_PENDING, 1, name="Iryna", phone=6045550123,
              gift_card_payments=[(self.gift_cards[0].number, "5.00")])
        delivery = order(items("Perogies", "Borscht"), "delivery", OrderStation.STATUS_PENDING, 3, name="Sofia",
                         phone=6045550188, to_go_order=True)
        Delivery.objects.create(order=delivery, destination="610 6th Ave, New Westminster, BC", phone=6045550188,
                                instructions="Leave the package at the door.")
        order(items("Frozen perogies (24)"), "store", OrderStation.STATUS_APPROVED, 1, name="Peter")
        RejectedOrder.objects.create(order_id=Order.objects.latest("id").id + 1000, reason="Demo: out of containers.",
                                     timestamp=now - datetime.timedelta(hours=1))
