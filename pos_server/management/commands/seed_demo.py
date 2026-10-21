"""
Fill an empty database with demo data for local development and testing.

    python manage.py seed_demo            # refuses if there are already orders or dishes
    python manage.py seed_demo --force    # seed anyway (adds to what's there)

Creates users (admin/admin, cashier/cashier, courier/courier, customer/customer; register PINs: Alex 0000,
Dana 1234, Priya 2468, recently used on the register so its lock screen lists them), stations, ingredients, components with options, dishes with adjustable parts, add-ons
and condiments, combos, menus (one switched on by hand, some on schedules, a draft), offers and coupons, today's business-day event, promo
banners, gift card presets and cards, a registered device, past cash drawers, orders in every state across
the last few days, and nine weeks of finished orders (closed Tuesdays) for the admin dashboard and reports.

KEEP THIS UP TO DATE: whenever a model is added or changed, update this command so it still runs on a
fresh database and covers the new fields (see CLAUDE.md).
"""
import datetime
import io
import random
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.core.management import call_command
from django.utils import timezone

from admin_portal.models import StoreSettings
from deliveries.models import CourierShift, Delivery
from events.models import Event
from gift_cards.models import CardPreset, GiftCard
from inventory.models import (CookingSession, IngredientReservation, Packaging, Recipe, RecipeIngredient, RecipeStep,
                              Transformation, YieldEntry)
from online_store.models import PromoContent, RejectedOrder
from pos_server.models import (CashDrawerSession, CashMovement, Combo, ComboSlot, ComboSlotOption, Component,
                               ComponentIngredient, CateringPrice, Discount, Dish, DishComponent, EligibleDevice, Extra, Ingredient,
                               Menu, MenuRule, Option, OptionGroup, Order, OrderPayment, OrderStation, ReceiptLog, Refund,
                               RefundItem, RegisterSignIn, Station)
from pos_server import refunds
from pos_server.orders import create_order
from pos_server.pricing import price_cart
from screens.models import Screen
from users.models import Staff

# A fixed token so a dev browser can be registered once: ?device-token=<this> on any POS page
DEMO_DEVICE_TOKEN = "00000000-0000-4000-8000-000000000001"

CONDIMENTS = ["Ketchup", "Mustard", "Mayo", "Hot sauce", "Extra salt packet", "Extra napkins"]


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
        parser.add_argument("--history-days", type=int, default=65, help="Days of finished past orders (the slow part)")

    def handle(self, *args, force=False, allow_production=False, history_days=65, **options):
        self.history_days = history_days
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
            self.seed_cookery()
            call_command("seed_recipes", stdout=io.StringIO())  # the Cookery mockup's recipes too
            self.seed_discounts()
            self.seed_events()
            self.seed_store()
            self.seed_screens()
            self.seed_gift_cards()
            self.device, _ = EligibleDevice.objects.get_or_create(token=DEMO_DEVICE_TOKEN, defaults={"name": "Register 1"})
            # The register's lock screen offers PIN sign-in to whoever used it last
            for username in ("admin", "priya", "cashier"):  # Dana (cashier) most recent
                for device in (None, self.device):
                    RegisterSignIn.record(device, User.objects.get(username=username))
            Packaging.objects.get_or_create(name="Takeout container", defaults={"quantity": 200, "cost": Decimal("0.35")})
            self.seed_orders()
            # Availability overrides, as in the mockup (after the orders, which need these to sell):
            # a component turned off (its dish becomes unavailable) and a sold-out dish kept selling past zero
            for obj, mode in ((Component.objects.get(title="Halloumi"), "off"), (Dish.objects.get(title="Cold brew"), "always")):
                obj.availability = mode
                obj.save()
        self.stdout.write(self.style.SUCCESS(
            "Seeded. Log in as admin/admin, or on the register's lock screen as Dana (PIN 1234). Register this "
            f"browser as a device with ?device-token={DEMO_DEVICE_TOKEN} (only needed when ENFORCE_DEVICE_CHECK is on)."
        ))

    # ---------- people ----------

    def seed_users(self):
        def user(username, first_name, pin=None, **flags):
            obj, created = User.objects.get_or_create(username=username, defaults={
                "first_name": first_name, "email": f"{username}@example.com", **flags})
            if created:
                obj.set_password(username)
                obj.save()
            if flags.get("is_staff"):
                staff, _ = Staff.objects.get_or_create(user=obj)
                if pin:
                    staff.set_pin(pin)
                    staff.save()
            return obj
        user("admin", "Alex", pin="0000", is_staff=True, is_superuser=True)
        self.cashier = user("cashier", "Dana", pin="1234", is_staff=True)
        shift_lead = user("priya", "Priya", pin="2468", is_staff=True)
        # A shift lead can type in manual discounts on the register; cashiers can't
        shift_lead.user_permissions.add(Permission.objects.get(codename="apply_manual_discount"))
        user("courier", "Courier", is_staff=True)
        # The courier is clocked in, so delivery ETAs and the courier app have someone to work with
        CourierShift.objects.get_or_create(user=User.objects.get(username="courier"), defaults={"mode": "bicycling"})
        customer = user("customer", "Customer")
        customer.customer_profile.phone = 6045550100
        customer.customer_profile.home_address = "123 Main St, Springfield"
        customer.customer_profile.save()

    # ---------- menu ----------

    def seed_stations(self):
        specs = [
            # code, name, icon, menu heading, colour
            ("kitchen", "Grill", "outdoor_grill", "Grill", "#12875A"),
            ("bar", "Bar", "local_cafe", "Bar", "#4E79B5"),
            ("bakery", "Bakery", "bakery_dining", "Bakery", "#E87BA4"),
        ]
        self.stations = {}
        for position, (code, name, icon, heading, color) in enumerate(specs):
            station, _ = Station.objects.update_or_create(code=code, defaults={
                "name": name, "icon": icon, "menu_heading": heading, "position": position, "color": color})
            self.stations[code] = station
        Station.objects.filter(code="gng").update(position=len(specs), color="#C9821A")

    def seed_inventory(self):
        ingredient_specs = [
            # title, unit, inventory, allergens, flags (m = meat, f = fish, a = animal byproduct), unlimited, cost
            ("Ground beef", "g", 40000, [], "m", False, "0.015"),
            ("Flour", "g", 100000, ["Gluten"], "", False, "0.002"),
            ("Milk", "ml", 60000, ["Dairy"], "a", False, "0.002"),
            ("Oats", "g", 20000, [], "", False, "0.004"),
            ("Almonds", "g", 10000, ["Tree nuts"], "", False, "0.02"),
            ("Cheddar", "g", 8000, ["Dairy"], "a", False, "0.02"),
            ("Eggs", "ea", 600, ["Egg"], "a", False, "0.30"),
            ("Potatoes", "g", 150000, [], "", False, "0.003"),
            ("Chicken", "g", 30000, [], "m", False, "0.012"),
            ("Pork shoulder", "g", 20000, [], "m", False, "0.01"),
            ("Vegetables", "g", 60000, [], "", False, "0.004"),
            ("Coffee beans", "g", 20000, [], "", False, "0.03"),
            ("Lemons", "ea", 300, [], "", False, "0.40"),
            ("Sugar", "g", 20000, [], "", False, "0.002"),
            ("Butter", "g", 10000, ["Dairy"], "a", False, "0.01"),
            ("Sesame seeds", "g", 2000, ["Sesame"], "", False, "0.01"),
            ("Mustard seed", "g", 1000, ["Mustard"], "", False, "0.02"),
            ("Water", "ml", 0, [], "", True, None),
        ]
        self.ingredients = {}
        for title, unit, inventory, allergens, flags, unlimited, cost in ingredient_specs:
            self.ingredients[title], _ = Ingredient.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "inventory": inventory, "allergens": allergens,
                "meat": "m" in flags, "fish": "f" in flags, "animal": "a" in flags,
                "unlimited": unlimited, "cost": Decimal(cost) if cost else None})

        # title, unit, type, crafting option, inventory, extra price, [(ingredient, qty)]
        component_specs = [
            ("Brioche bun", "ea", "food", "buy", 120, "0", [("Flour", 60), ("Eggs", 0.2)]),
            ("Sesame bun", "ea", "food", "buy", 80, "0", [("Flour", 60), ("Sesame seeds", 2)]),
            ("Gluten-free bun", "ea", "food", "buy", 30, "0", []),
            ("Lettuce wrap", "ea", "food", "buy", 200, "0", [("Vegetables", 40)]),
            ("Beef patty", "ea", "food", "craft", 150, "4.00", [("Ground beef", 120)]),
            ("Cheddar slice", "ea", "food", "buy", 300, "1.00", [("Cheddar", 20)]),
            ("Lettuce", "g", "food", "buy", 5000, "0", [("Vegetables", 1)]),
            ("Tomato", "ea", "food", "buy", 400, "0", [("Vegetables", 30)]),
            ("Pickles", "ea", "food", "buy", 1000, "0", []),
            ("Onion", "g", "food", "buy", 4000, "0", [("Vegetables", 1)]),
            ("Smash sauce", "ml", "food", "craft", 3000, "0", [("Eggs", 0.01), ("Mustard seed", 0.02)]),
            ("Chicken fillet", "ea", "food", "craft", 80, "3.50", [("Chicken", 150)]),
            ("Spicy mayo", "ml", "food", "craft", 2000, "0", [("Eggs", 0.01)]),
            ("Slaw", "g", "food", "craft", 5000, "0", [("Vegetables", 1)]),
            ("Mushrooms", "g", "food", "buy", 4000, "1.50", [("Vegetables", 1)]),
            ("Swiss cheese", "ea", "food", "buy", 200, "1.00", [("Cheddar", 20)]),
            ("Garlic aioli", "ml", "food", "craft", 2000, "0", [("Eggs", 0.01)]),
            ("Pulled pork", "g", "food", "craft", 8000, "5.00", [("Pork shoulder", 1.2)]),
            ("Cornbread", "ea", "food", "craft", 40, "2.00", [("Flour", 50), ("Eggs", 0.25)]),
            ("BBQ sauce", "ml", "food", "buy", 3000, "0", []),
            ("Halloumi", "g", "food", "buy", 4000, "3.50", [("Milk", 2)]),
            ("Grains", "g", "food", "craft", 8000, "0", []),
            ("Greens", "g", "food", "buy", 6000, "0", [("Vegetables", 1)]),
            ("Cucumber", "g", "food", "buy", 3000, "0", [("Vegetables", 1)]),
            ("Pickled onion", "g", "food", "craft", 2000, "0", [("Vegetables", 1)]),
            ("Dressing", "ml", "food", "craft", 3000, "0", []),
            ("Fries portion", "ea", "food", "craft", 0, "0", [("Potatoes", 180)]),  # sold out, like the mockup
            ("Seasoning", "g", "food", "buy", 2000, "0", []),
            ("Corn cob", "ea", "food", "buy", 120, "0", [("Vegetables", 200)]),
            ("Chili lime butter", "g", "food", "craft", 2000, "0", [("Butter", 1)]),
            ("Cotija", "g", "food", "buy", 2000, "1.00", [("Cheddar", 1)]),
            ("Croutons", "g", "food", "craft", 2000, "0", [("Flour", 1)]),
            ("Chicken bites", "ea", "food", "craft", 300, "0", [("Chicken", 25)]),
            ("Bacon", "ea", "food", "buy", 300, "0", [("Pork shoulder", 20)]),
            ("Fried egg", "ea", "food", "auto", 0, "0", [("Eggs", 1)]),
            ("Avocado", "ea", "food", "buy", 60, "0", [("Vegetables", 60)]),
            ("Espresso shot", "ea", "beverage", "auto", 0, "1.00", [("Coffee beans", 9)]),
            ("Whole milk", "ml", "beverage", "buy", 20000, "0", [("Milk", 1)]),
            ("Oat milk", "ml", "beverage", "buy", 8000, "0", [("Oats", 0.1)]),
            ("Almond milk", "ml", "beverage", "buy", 6000, "0", [("Almonds", 0.1)]),
            ("Skim milk", "ml", "beverage", "buy", 8000, "0", [("Milk", 1)]),
            ("Lemonade", "ml", "beverage", "craft", 12000, "0", [("Lemons", 0.004), ("Sugar", 0.05), ("Water", 1)]),
            ("Ginger syrup", "ml", "beverage", "craft", 2000, "0", [("Sugar", 0.5)]),
            ("Sparkling water", "ml", "beverage", "buy", 20000, "0", []),
            ("Matcha powder", "g", "beverage", "buy", 500, "0", []),
            ("Cold brew", "ml", "beverage", "craft", 0, "0", [("Coffee beans", 0.1), ("Water", 1)]),  # sold out
            ("Vanilla syrup", "ml", "beverage", "buy", 2000, "0", [("Sugar", 0.5)]),
            ("Whipped cream", "ml", "beverage", "buy", 2000, "0", [("Milk", 1)]),
            ("Extra ginger", "ml", "beverage", "craft", 1000, "0", []),
            ("Cardamom bun", "ea", "food", "buy", 30, "0", [("Flour", 80), ("Butter", 15)]),
            ("Almond croissant", "ea", "food", "buy", 24, "0", [("Flour", 70), ("Almonds", 20), ("Butter", 30)]),
            ("Brownie", "ea", "food", "buy", 30, "0", [("Flour", 30), ("Eggs", 0.5)]),
            ("Lemon tart", "ea", "food", "buy", 16, "0", [("Flour", 40), ("Lemons", 0.3)]),
            ("Cookie", "ea", "food", "buy", 40, "0", [("Flour", 30), ("Butter", 10)]),
            ("Sourdough loaf", "ea", "food", "buy", 0, "0", [("Flour", 500)]),  # sold out
            ("Butter pat", "ea", "food", "buy", 200, "0", [("Butter", 10)]),
            ("Jam", "ea", "food", "buy", 200, "0", []),
            ("Cream cheese", "ea", "food", "buy", 150, "0", [("Milk", 20)]),
        ]
        side = [("Ketchup", "Ketchup packet"), ("Mustard", "Mustard packet"), ("Mayo", "Mayo packet"),
                ("Hot sauce", "Hot sauce packet"), ("Extra salt packet", "Salt packet"), ("Extra napkins", "Napkins"),
                ("Straw", "Straw"), ("Lid", "Lid"), ("Stirrer", "Stirrer"), ("Fork", "Fork")]
        component_specs += [(component, "ea", "food", "buy", 2000, "0", []) for _label, component in side]
        self.side_component = dict(side)

        self.components = {}
        for title, unit, food_type, option, inventory, extra_price, parts in component_specs:
            component, created = Component.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "type": food_type, "crafting_option": option, "inventory": inventory,
                "extra_price": Decimal(extra_price)})
            if created:
                for ingredient, qty in parts:
                    ComponentIngredient.objects.create(component=component, ingredient=self.ingredients[ingredient], quantity=qty)
            self.components[title] = component

        c = self.components

        def group(title, options, component=None, dish=None, note="", default=None, pick=OptionGroup.ONE, required=True):
            """
            options: "Name", ("Name", "price") or ("Name", "price", {other Option fields}). A required pick-one
            group starts on its first option, so `default` is moved to the front.
            """
            obj = OptionGroup.objects.create(title=title, component=component, dish=dish, note=note, pick=pick, required=required)
            specs = [(o, "0", {}) if isinstance(o, str) else (o[0], o[1], o[2] if len(o) > 2 else {}) for o in options]
            if default:
                specs.sort(key=lambda spec: spec[0] != default)
            for position, (name, price, more) in enumerate(specs):
                Option.objects.create(group=obj, title=name, price=Decimal(price), position=position, **more)
            return obj

        # Options that belong to a component: every dish using it offers them
        group("Bun", [("Brioche bun", "0"), ("Sesame bun", "0", {"component": c["Sesame bun"]}),
                      ("Gluten-free bun", "1.50", {"component": c["Gluten-free bun"]}),
                      ("Lettuce wrap", "0", {"component": c["Lettuce wrap"]})], component=c["Brioche bun"])
        group("Patty temperature", ["Medium rare", "Medium", "Medium well", "Well done"], component=c["Beef patty"],
              note="Cooked to order", default="Medium")
        group("Milk", [("Whole milk", "0"), ("Oat milk", "0.60", {"component": c["Oat milk"]}),
                       ("Almond milk", "0.60", {"component": c["Almond milk"]}), ("Skim milk", "0", {"component": c["Skim milk"]})],
              component=c["Whole milk"])
        self.group = group

    def seed_dish_details(self):
        """Admin details: kitchen ticket names, prep times and catering prices."""
        # title: (ticket name, prep minutes)
        details = {
            "Smash burger": ("SMASH", 7), "Crispy chicken sandwich": ("CHX SAND", 8),
            "Mushroom melt": ("MELT", 7), "Pulled pork plate": ("PORK PLATE", 6),
            "Halloumi grain bowl": ("GRAIN BOWL", 6), "Fries": ("FRIES", 4),
            "Charred corn": ("CORN", 5), "Green salad": ("SALAD", 3),
            "Kids cheeseburger": ("KIDS BURGER", 6), "Kids chicken bites": ("KIDS BITES", 6),
            "Iced latte": ("ICED LATTE", 2), "Flat white": ("FLAT WHITE", 2),
            "Fresh lemonade": ("LEMONADE", 1), "Ginger spritz": ("SPRITZ", 2),
            "Matcha": ("MATCHA", 2), "Cold brew": ("COLD BREW", 1),
            "Cardamom bun": ("CARD BUN", 1), "Almond croissant": ("ALM CROISS", 1),
            "Brownie": ("BROWNIE", 1), "Lemon tart": ("LEMON TART", 1),
            "Cookie": ("COOKIE", 1), "Sourdough loaf": ("SOURDOUGH", 1),
        }
        for title, (ticket, prep) in details.items():
            Dish.objects.filter(title=title, ticket_name="").update(ticket_name=ticket, prep_minutes=prep)
        # Catering: title -> (minimum, [(from quantity, price each)])
        catering = {"Smash burger": (12, [(12, "13.50"), (25, "12.75"), (50, "11.95")]),
                    "Halloumi grain bowl": (10, [(10, "13.95"), (30, "12.95")]),
                    "Fries": (10, [(10, "5.25"), (40, "4.75")]),
                    "Cardamom bun": (12, [(12, "4.00"), (48, "3.50")])}
        for title, (minimum, tiers) in catering.items():
            dish = Dish.objects.filter(title=title, catering=False).first()
            if dish:
                dish.catering, dish.catering_min = True, minimum
                dish.save(update_fields=["catering", "catering_min"])
                for quantity, price in tiers:
                    CateringPrice.objects.create(dish=dish, quantity=quantity, price=Decimal(price))

    def seed_menus(self):
        st = self.stations
        c = self.components
        group = self.group
        colors = {"kitchen": "#8a3b12", "bar": "#1f4a3a", "bakery": "#b5791f"}

        def dish(title, price, station, parts, description="", visible=True):
            obj, created = Dish.objects.get_or_create(title=title, defaults={
                "price": Decimal(price), "station": st[station], "description": description, "visible_in_menu": visible})
            if created:
                obj.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, colors[station]), save=True)
                for position, part in enumerate(parts):
                    name, qty, *flags = part
                    DishComponent.objects.create(dish=obj, component=c[name], quantity=qty, position=position,
                                                 adjustable="adjustable" in flags)
            return obj

        def extras(owner, addons=(), condiments=()):
            """Add-ons become the dish's "Add-ons" pick-any group (each can be added many times); condiments are Extras."""
            key = "dish" if isinstance(owner, Dish) else "combo"
            if addons:
                group("Add-ons", [(name, price, {"component": c[name]}) for name, price in addons], dish=owner,
                      pick=OptionGroup.ANY, required=False)
            for position, label in enumerate(condiments):
                Extra.objects.create(**{key: owner}, component=c[self.side_component[label]], title=label, position=position)

        A = "adjustable"
        burger_addons = [("Bacon", "2.50"), ("Fried egg", "2.00"), ("Avocado", "2.25")]

        smash = dish("Smash burger", "14.50", "kitchen", [("Brioche bun", 1, A), ("Beef patty", 1, A), ("Cheddar slice", 1, A),
                     ("Lettuce", 20, A), ("Tomato", 1, A), ("Pickles", 3, A), ("Onion", 15, A), ("Smash sauce", 20, A)],
                     "Two-minute smashed beef patty, cheddar and smash sauce on brioche.")
        extras(smash, burger_addons, CONDIMENTS)
        chicken = dish("Crispy chicken sandwich", "13.95", "kitchen", [("Brioche bun", 1, A), ("Chicken fillet", 1, A),
                       ("Lettuce", 20, A), ("Pickles", 3, A), ("Slaw", 40, A), ("Spicy mayo", 20, A)], "Buttermilk fried chicken with slaw.")
        group("Spice level", ["Mild", "Medium", "Hot"], dish=chicken, default="Medium")
        extras(chicken, [("Bacon", "2.50"), ("Cheddar slice", "1.00"), ("Avocado", "2.25")], CONDIMENTS)
        melt = dish("Mushroom melt", "13.25", "kitchen", [("Brioche bun", 1, A), ("Mushrooms", 80, A), ("Swiss cheese", 1, A),
                    ("Onion", 30, A), ("Garlic aioli", 20, A)], "Roasted mushrooms and Swiss, no meat.")
        extras(melt, [("Avocado", "2.25"), ("Fried egg", "2.00")], CONDIMENTS)
        pork = dish("Pulled pork plate", "16.75", "kitchen", [("Pulled pork", 200, A), ("Slaw", 80, A), ("Cornbread", 1, A),
                    ("Pickles", 3, A), ("BBQ sauce", 30, A)], "Slow-smoked pork, slaw and cornbread.")
        group("Sauce heat", ["Mild", "Medium", "Hot"], dish=pork, default="Medium")
        extras(pork, condiments=["Hot sauce", "Extra salt packet", "Extra napkins"])
        bowl = dish("Halloumi grain bowl", "14.95", "kitchen", [("Halloumi", 100, A), ("Grains", 150, A), ("Greens", 50, A),
                    ("Cucumber", 40, A), ("Pickled onion", 20, A), ("Dressing", 30)], "Grilled halloumi over grains and greens.")
        group("Dressing", ["Lemon tahini", "Yogurt herb", "Olive oil and lemon"], dish=bowl)
        extras(bowl, [("Avocado", "2.25")], ["Extra salt packet", "Extra napkins"])
        fries = dish("Fries", "5.95", "kitchen", [("Fries portion", 1), ("Seasoning", 2, A)], "Hand-cut, twice-fried.")
        group("Size", [("Small", "-1.00", {"multiplier": 0.7}), ("Regular", "0"), ("Large", "1.50", {"multiplier": 1.5})],
              dish=fries, default="Regular")
        group("Crispness", ["Regular", "Extra crispy"], dish=fries)
        extras(fries, condiments=["Ketchup", "Mayo", "Hot sauce", "Extra salt packet", "Extra napkins"])
        corn = dish("Charred corn", "6.50", "kitchen", [("Corn cob", 1), ("Chili lime butter", 15, A), ("Cotija", 10, A)],
                    "Grilled corn with chili lime butter and cotija.")
        extras(corn, condiments=["Extra salt packet", "Extra napkins"])
        salad = dish("Green salad", "6.00", "kitchen", [("Greens", 80), ("Tomato", 1, A), ("Cucumber", 40, A), ("Croutons", 15, A)],
                     "Side salad.", visible=False)
        group("Dressing", ["Lemon vinaigrette", "Ranch", "On the side"], dish=salad)
        kids_burger = dish("Kids cheeseburger", "7.50", "kitchen", [("Brioche bun", 1, A), ("Beef patty", 0.75, A),
                           ("Cheddar slice", 1, A), ("Pickles", 2, A)], visible=False)
        extras(kids_burger, condiments=["Ketchup", "Mustard", "Extra napkins"])
        bites = dish("Kids chicken bites", "7.50", "kitchen", [("Chicken bites", 5)], visible=False)
        group("Dipping sauce", ["Ketchup", "BBQ", "Ranch"], dish=bites)

        drink_side = ["Straw", "Lid", "Stirrer", "Extra napkins"]

        def drink(title, price, parts, served=None, ice=False, size=False, sweet=False, addons=(), description=""):
            obj = dish(title, price, "bar", parts, description)
            if served:
                group("Served", ["Hot", "Iced"], dish=obj, default=served)
            if ice:
                group("Ice", ["Regular ice", "Light ice", "No ice"], dish=obj)
            if size:
                group("Size", [("Small", "-0.50", {"multiplier": 0.75}), ("Regular", "0"), ("Large", "0.60", {"multiplier": 1.33})],
                      dish=obj, default="Regular")
            if sweet:
                group("Sweetness", ["Regular", "Less sweet", "No sugar"], dish=obj)
            extras(obj, addons, drink_side)
            return obj

        coffee_addons = [("Espresso shot", "1.00"), ("Vanilla syrup", "0.75"), ("Whipped cream", "0.50")]
        latte = drink("Iced latte", "5.95", [("Espresso shot", 2), ("Whole milk", 250)], served="Iced", ice=True, size=True,
                      addons=coffee_addons, description="Double shot over cold milk.")
        flat_white = drink("Flat white", "5.25", [("Espresso shot", 2), ("Whole milk", 150)], served="Hot", size=True,
                           addons=coffee_addons[:2])
        lemonade = drink("Fresh lemonade", "4.95", [("Lemonade", 350)], ice=True, size=True, sweet=True)
        spritz = drink("Ginger spritz", "5.75", [("Ginger syrup", 40), ("Sparkling water", 250)], ice=True, sweet=True,
                       addons=[("Extra ginger", "0.50")])
        matcha = drink("Matcha", "6.25", [("Matcha powder", 4), ("Whole milk", 250)], served="Iced", ice=True, size=True, sweet=True,
                       addons=[("Vanilla syrup", "0.75")])
        cold_brew = drink("Cold brew", "5.50", [("Cold brew", 350)], ice=True, size=True, addons=[("Espresso shot", "1.00")])

        bakery_addons = [("Butter pat", "0.50"), ("Jam", "0.50"), ("Cream cheese", "1.00")]

        def pastry(title, price, component, description=""):
            obj = dish(title, price, "bakery", [(component, 1)], description)
            group("Heat", ["As is", "Warmed"], dish=obj)
            extras(obj, bakery_addons, ["Extra napkins", "Fork"])
            return obj

        bun = pastry("Cardamom bun", "4.50", "Cardamom bun", "Swedish-style, baked every morning.")
        croissant = pastry("Almond croissant", "4.95", "Almond croissant")
        brownie = pastry("Brownie", "4.25", "Brownie")
        tart = pastry("Lemon tart", "5.75", "Lemon tart")
        cookie = pastry("Cookie", "3.50", "Cookie")
        loaf = pastry("Sourdough loaf", "9.00", "Sourdough loaf")

        # Combos: slots are (title, [(dish, upcharge)], {picks, required}); the first dish is the default
        def combo(title, price, slots, description="", **fields):
            obj, created = Combo.objects.get_or_create(title=title, defaults={"price": Decimal(price), "description": description, **fields})
            if created:
                obj.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, "#14261f"), save=True)
                for position, (slot_title, options, settings_) in enumerate(slots):
                    slot = ComboSlot.objects.create(combo=obj, title=slot_title, position=position, **settings_)
                    for option_position, (option_dish, delta) in enumerate(options):
                        ComboSlotOption.objects.create(slot=slot, dish=option_dish, price=Decimal(delta),
                                                       is_default=option_position == 0 and slot.required, position=option_position)
            return obj

        lunch = combo("Lunch combo", "18.95", [
            ("Main", [(smash, "0"), (chicken, "0"), (melt, "0"), (bowl, "1.00"), (pork, "2.50")], {}),
            ("Side", [(fries, "0"), (corn, "0.50"), (salad, "0")], {}),
            ("Drink", [(lemonade, "0"), (cold_brew, "0"), (spritz, "0.75"), (latte, "0.50"), (flat_white, "0"), (matcha, "1.00")], {}),
        ], "Any burger or sandwich with a side and a drink.", ticket_name="LUNCH CMB")
        kids = combo("Kids meal", "9.95", [
            ("Main", [(kids_burger, "0"), (bites, "0")], {}),
            ("Side", [(fries, "0"), (corn, "0"), (salad, "0")], {}),
            ("Drink", [(lemonade, "0"), (spritz, "0.50")], {}),
            ("Treat", [(brownie, "2.00"), (bun, "2.00")], {"required": False}),
        ], "A kids main, a side and a drink, with an optional treat.", ticket_name="KIDS CMB")
        combo("Coffee and pastry", "8.50", [
            ("Coffee", [(flat_white, "0"), (latte, "0.50"), (matcha, "0.75")], {}),
            ("Pastry", [(bun, "0"), (cookie, "0"), (croissant, "0.50")], {}),
        ], "Any coffee with something from the bakery case.", ticket_name="COF+PAST")
        combo("Snack box", "11.00", [
            ("Sides", [(fries, "0"), (corn, "0"), (salad, "0")], {"picks": 2}),
            ("Sweet", [(cookie, "0"), (brownie, "0.50")], {}),
        ], "Two sides and something sweet to share.", ticket_name="SNACK BOX", pricing=Combo.PERCENT,
            percent_off=Decimal("15"), active=False)
        extras(kids, condiments=["Extra napkins"])

        self.dishes = {d.title: d for d in Dish.objects.all()}
        self.seed_dish_details()
        self.combos = {"Lunch combo": lunch, "Kids meal": kids}

        def menu(title, position, dishes, combos, color, rules=(), **fields):
            obj, created = Menu.objects.get_or_create(title=title, defaults={
                "position": position, "background_color": color, "accent_1": "#e8ede9", "accent_2": "#f4a93b",
                "accent_3": "#14261f", **fields})
            if created:
                obj.header_image.save("header.png", placeholder_image(title, color, (1200, 300)), save=False)
                obj.footer_image.save("footer.png", placeholder_image("Thank you!", color, (1200, 200)), save=True)
                obj.dishes.set(dishes)
                obj.combos.set(combos)
                for kind, kind_type, extra in rules:
                    MenuRule.objects.create(menu=obj, kind=kind, type=kind_type, **extra)
            return obj

        def weekly(kind, days, hour):
            return (kind, MenuRule.WEEKLY, {"days": days, "time": datetime.time(hour)})

        visible = [smash, chicken, melt, pork, bowl, fries, corn, latte, flat_white, lemonade, spritz, matcha, cold_brew,
                   bun, croissant, brownie, tart, cookie, loaf]
        # Lunch is switched on by hand, so the register and store always have a menu; the others follow schedules
        menu("Lunch", 0, visible, [lunch, kids], "#1f4a3a", manual_on=True)
        breakfast = menu("Breakfast", 1, [latte, flat_white, matcha, bun, croissant, cookie], [], "#b5791f", auto=True,
                         rules=[weekly("on", "01234", 7), weekly("on", "56", 8), weekly("off", "0123456", 11)])
        menu("Happy hour", 2, [spritz, lemonade, fries, corn], [], "#8e2914", auto=True,
             rules=[weekly("on", "234", 16), weekly("off", "234", 18)])
        year = timezone.localdate().year
        holiday = menu("Holiday specials", 3, [latte, bun, brownie, cookie], [], "#2d5a84", auto=True, draft=True,
                       rules=[("on", MenuRule.DATES, {"start_date": datetime.date(year, 12, 1), "end_date": datetime.date(year, 12, 31)})])
        MenuRule.objects.get_or_create(menu=breakfast, kind="off", type=MenuRule.MENU, other_menu=holiday)

    def seed_cookery(self):
        """Recipes, par levels, frozen versions with their transforms, and a few batches in progress (like the mockup)."""
        c, ing = self.components, self.ingredients
        minutes = lambda m: datetime.timedelta(minutes=m)
        recipes = {
            # component: (makes, [(ingredient, qty)], [(title, text, minutes, scene, [ingredients used])])
            "Pulled pork": (4000, [("Pork shoulder", 5000), ("Sugar", 100), ("Vegetables", 500)], [
                ("Rub the shoulders", "Mix the sugar with salt and spices and rub it all over the pork.", 0, "prep", ["Pork shoulder", "Sugar"]),
                ("Sear", "Brown the shoulders on every side in the big rondeau.", 10, "fry", []),
                ("Braise low", "Add the onions and a little water, cover tight and braise at 150 °C until it falls apart.", 240, "bake", ["Vegetables"]),
                ("Shred", "Pull the meat apart with two forks, mix in the juices and portion into tubs.", 0, "prep", []),
            ]),
            "Cornbread": (24, [("Flour", 1200), ("Eggs", 6), ("Milk", 900), ("Butter", 200), ("Sugar", 150)], [
                ("Mix the batter", "Whisk the wet into the dry with the melted butter until just combined.", 0, "prep", ["Flour", "Eggs", "Milk", "Butter", "Sugar"]),
                ("Rest the batter", "Let it sit so the flour hydrates.", 15, "rest", []),
                ("Bake", "Pour into the buttered half sheet and bake at 200 °C until golden and springy.", 25, "bake", []),
                ("Cool", "Cool on a rack before cutting, or it crumbles.", 10, "chill", []),
                ("Cut", "Cut the sheet into 24 squares and count them onto the tray.", 0, "prep", []),
            ]),
            "Lemonade": (12000, [("Lemons", 60), ("Sugar", 1500), ("Water", 11000)], [
                ("Juice the lemons", "Roll, halve and juice every lemon; strain out the seeds.", 0, "prep", ["Lemons"]),
                ("Make the syrup", "Simmer the sugar with 1.5 L of the water until clear.", 10, "simmer", ["Sugar"]),
                ("Mix and chill", "Stir the juice, syrup and the rest of the water together and chill the jugs.", 60, "chill", ["Water"]),
            ]),
            "Chicken bites": (120, [("Chicken", 3000), ("Flour", 500), ("Eggs", 6)], [
                ("Cut the chicken", "Cut the thighs into 25 g pieces.", 0, "prep", ["Chicken"]),
                ("Bread", "Egg, then seasoned flour, pressing it on well.", 0, "prep", ["Flour", "Eggs"]),
                ("Chill", "Rest the breaded bites in the walk-in so the coating sets.", 20, "chill", []),
                ("Par-fry", "Fry at 170 °C in small batches, then cool on racks.", 6, "fry", []),
            ]),
        }
        for title, (makes, parts, steps) in recipes.items():
            recipe = Recipe.objects.create(yield_quantity=makes)
            rows = {name: RecipeIngredient.objects.create(recipe=recipe, ingredient=ing[name], quantity=qty, position=n)
                    for n, (name, qty) in enumerate(parts)}
            for n, (step_title, text, mins, scene, uses) in enumerate(steps):
                step = RecipeStep.objects.create(recipe=recipe, position=n, title=step_title, text=text, scene=scene,
                                                 duration=minutes(mins) if mins else None)
                step.uses.set([rows[name] for name in uses])
            Component.objects.filter(pk=c[title].pk).update(recipe=recipe)
        for title, par in (("Beef patty", 200), ("Pulled pork", 8000), ("Cornbread", 40), ("Lemonade", 15000),
                           ("Chicken bites", 300), ("Spicy mayo", 2000), ("Slaw", 5000), ("Garlic aioli", 2000)):
            Component.objects.filter(pk=c[title].pk).update(par=par)
        Component.objects.filter(pk=c["Beef patty"].pk).update(inventory=46)  # running low, as in the mockup

        frozen = {}
        for title, unit, inventory, par in (("Beef patty, frozen", "ea", 80, 120), ("Pulled pork, frozen", "g", 6000, 8000)):
            frozen[title] = Component.objects.create(title=title, unit_of_measurement=unit, type="food", inventory=inventory,
                                                     par=par, crafting_option="transform")
        how = {
            "freeze": "Lay them out on parchment-lined trays in the blast freezer, then bag them once they are hard.",
            "thaw": "Move them to the walk-in on a tray, single layer. Never thaw at room temperature.",
        }
        specs = [  # name, from, to, scene, minutes
            ("Freeze patties", c["Beef patty"], frozen["Beef patty, frozen"], "freeze", 90),
            ("Thaw patties", frozen["Beef patty, frozen"], c["Beef patty"], "thaw", 720),
            ("Freeze pulled pork", c["Pulled pork"], frozen["Pulled pork, frozen"], "freeze", 120),
            ("Thaw pulled pork", frozen["Pulled pork, frozen"], c["Pulled pork"], "thaw", 480),
        ]
        transforms = {name: Transformation.objects.create(name=name, from_component=source, to_component=target, scene=scene,
                                                          duration=minutes(mins), instructions=how[scene], position=n)
                      for n, (name, source, target, scene, mins) in enumerate(specs)}

        # In progress: Priya's pork is braising, Dana is counting lemonade, and patties are thawing
        now = timezone.now()
        priya = User.objects.get(username="priya")
        pork = Component.objects.get(pk=c["Pulled pork"].pk)
        braise_end = now + minutes(72)
        braising = CookingSession.objects.create(kind="cook", component=pork, recipe=pork.recipe, started_by=priya, holder=priya,
                                                 started_at=now - minutes(190), step=2,
                                                 timers={"2": {"end": int(braise_end.timestamp() * 1000), "dur": 240 * 60, "paused": None}})
        lemonade = Component.objects.get(pk=c["Lemonade"].pk)
        counting = CookingSession.objects.create(kind="cook", component=lemonade, recipe=lemonade.recipe, started_by=self.cashier,
                                                 holder=self.cashier, started_at=now - minutes(75), phase="yield", step=2)
        for amount, ago in ((4000, 9), (4000, 4)):
            YieldEntry.objects.create(session=counting, amount=amount, at=now - minutes(ago))
        # Batches reserve their ingredients when they start (out of inventory)
        for batch in (braising, counting):
            for ri in batch.recipe.ingredients.select_related("ingredient"):
                if not ri.ingredient.unlimited:
                    IngredientReservation.objects.create(session=batch, ingredient=ri.ingredient, quantity=ri.quantity)
                    Ingredient.objects.filter(pk=ri.ingredient_id).update(inventory=F("inventory") - ri.quantity)
        thaw = transforms["Thaw patties"]
        CookingSession.objects.create(kind="transform", transformation=thaw, component=thaw.from_component, quantity=40,
                                      started_by=self.cashier, holder=self.cashier, started_at=now - minutes(540),
                                      ends_at=now + minutes(180))

    def seed_discounts(self):
        specs = [
            dict(name="Happy hour", note="Weekdays, 2 to 5 pm", kind="pct", value="10", weekdays="12345",
                 start_time=datetime.time(14), end_time=datetime.time(17)),
            dict(name="Student", note="With student ID", kind="pct", value="15"),
            dict(name="Loyalty reward", note="From saved points", kind="amt", value="3.00"),
            dict(name="Family pack", note="Orders of four or more", kind="amt", value="5.00", scope="order", min_items=4),
            dict(name="Welcome", note="10% off a first order", kind="pct", value="10", scope="order", code="WELCOME10",
                 online=True, max_uses=500),
            dict(name="Lunch deal", note="$5 off a lunch combo", kind="amt", value="5.00", scope="item", code="LUNCH5",
                 min_subtotal="15.00"),
        ]
        for position, spec in enumerate(specs):
            spec = {**spec, "value": Decimal(spec["value"]), "position": position}
            if "min_subtotal" in spec:
                spec["min_subtotal"] = Decimal(spec["min_subtotal"])
            discount, created = Discount.objects.get_or_create(name=spec.pop("name"), defaults=spec)
            if created and discount.code == "LUNCH5":
                discount.combos.set([self.combos["Lunch combo"]])

    # ---------- store ----------

    def seed_events(self):
        now = timezone.localtime()
        today_open = now.replace(hour=0, minute=1, second=0, microsecond=0)
        today_close = now.replace(hour=23, minute=59, second=0, microsecond=0)
        Event.objects.get_or_create(title="Open today", start=today_open, defaults={
            "end": today_close, "location_title": "Main location", "location_address": "100 Main St, Springfield",
            "restaurant_open": True, "in_person_open": True, "online_open": True, "delivery_open": True})
        next_week = today_open + datetime.timedelta(days=7)
        Event.objects.get_or_create(title="Patio opening", start=next_week.replace(hour=11), defaults={
            "end": next_week.replace(hour=15), "restaurant_open": True, "in_person_open": True,
            "online_open": True, "delivery_open": False})

    def seed_store(self):
        store = StoreSettings.load()
        store.name = "Corner Kitchen"
        store.save()
        for title, tagline, color in [("Lunch combos", "A main, a side and a drink from $18.95", "#1f4a3a"),
                                      ("Gift cards", "Give someone lunch", "#14261f")]:
            promo, created = PromoContent.objects.get_or_create(title=title, defaults={
                "tagline": tagline, "call_to_action": "Order now", "color": color, "accent": "#ffffff", "link": "/en/menu"})
            if created:
                promo.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, color), save=True)

    def seed_screens(self):
        # The order board comes with migrations (screens/0002); add a portrait one for a vertical TV
        Screen.objects.get_or_create(slug="pickup-portrait", defaults={
            "name": "Pickup (portrait)", "public": True, "width": 1080, "height": 1920, "padding": 32,
            "layout": {"widget": "rows", "params": {"count": 3, "weights": [1, 1.3, "340px"], "gap": 24}, "children": [
                {"widget": "order_list", "params": {"show": "preparing", "columns": 3, "limit": 9}},
                {"widget": "order_list", "params": {
                    "show": "ready", "title": "Ready for pickup", "subtitle": "Please collect at the counter",
                    "icon": "bag", "theme": "dark", "limit": 8}},
                {"widget": "columns", "params": {"count": 2, "weights": ["420px", 1]}, "children": [
                    {"widget": "rows", "params": {"count": 2, "gap": 24}, "children": [{"widget": "clock"}, {"widget": "weather"}]},
                    {"widget": "text_card", "params": {
                        "tag": "New", "title": "Maple oat latte", "footer": "$5.75", "background": "#F6D9C4",
                        "text": "Double shot, steamed oat milk and a little maple syrup."}},
                ]},
            ]},
        })

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
        d, combos = self.dishes, self.combos
        names = ["Olena", "Sam", "Mark", "Iris", "Peter", "Sofia", "Andre", None, None]
        now = timezone.now()

        def dish(title, qty=1, cust=None):
            return {"type": "dish", "id": d[title].id, "qty": qty, "cust": cust or {}}

        def lunch(drink="Iced latte", qty=1, **slots):
            combo = combos["Lunch combo"]
            chosen = {str(s.id): [{"dish": d[drink].id}] for s in combo.slots.all() if s.title == "Drink"}
            chosen.update(slots)
            return {"type": "combo", "id": combo.id, "qty": qty, "slots": chosen}

        def kids(side="Charred corn"):
            combo = combos["Kids meal"]
            return {"type": "combo", "id": combo.id, "qty": 1,
                    "slots": {str(s.id): [{"dish": d[side].id}] for s in combo.slots.all() if s.title == "Side"}}

        def option(dish_title, group_title, option_title):
            """{"<group key>": option id} for a dish's own option group."""
            group = OptionGroup.objects.get(dish=d[dish_title], title=group_title)
            return {str(group.id): group.options.get(title=option_title).id}

        burger_parts = {dc.component.title: dc.id for dc in d["Smash burger"].dishcomponent_set.select_related("component")}
        patty_group = OptionGroup.objects.get(component__title="Beef patty")
        well_done = {f"{patty_group.id}.{burger_parts['Beef patty']}": patty_group.options.get(title="Well done").id}

        shopper = User.objects.get(username="customer")
        cards = [("VISA", "4242"), ("MASTERCARD", "8810"), ("AMERICAN_EXPRESS", "1005"), ("VISA", "7731"), ("INTERAC", "0459")]

        def order(lines, channel, status, minutes_ago, method="card", drawer=None, order_discount=None, **fields):
            payments = ()
            if channel == "store":
                total = price_cart(lines, order_discount=order_discount, can_manual=True).total
                payments = [{"method": method, "amount": str(total), "tendered": str(total)}]
            if channel == "store":
                fields.setdefault("device", self.device)
            elif random.random() < 0.4:  # some online orders come from the signed-in demo customer
                fields.setdefault("customer", shopper)
                fields.setdefault("email", shopper.email)
            created = create_order(
                lines=lines, channel=channel, initial_status=status, payments=payments, drawer=drawer,
                order_discount=order_discount, can_manual_discount=True,
                name=fields.pop("name", random.choice(names)), **fields)
            when = now - datetime.timedelta(minutes=minutes_ago)
            Order.objects.filter(pk=created.pk).update(timestamp=when, start_time=when)
            created.payments.update(timestamp=when)
            for payment in created.payments.filter(method=OrderPayment.CARD):  # what Square would have reported
                brand, last4 = random.choice(cards)
                payment.square_payment_id = f"demo-{created.pk}-{payment.pk}"
                payment.card_brand, payment.card_last4 = brand, last4
                payment.entry_method = payment.entry_method or random.choice(["tap", "tap", "chip"])
                payment.save()
            created.station_statuses.filter(confirmed_at__isnull=False).update(confirmed_at=when)
            created.refresh_from_db()
            return created

        carts = [
            lambda: [dish("Smash burger"), dish("Fresh lemonade")],
            lambda: [lunch()],
            lambda: [lunch("Matcha"), dish("Cookie", 2)],
            lambda: [dish("Iced latte", 2), dish("Cardamom bun", 2)],
            lambda: [kids(), dish("Flat white")],
            lambda: [dish("Halloumi grain bowl"), dish("Ginger spritz")],
            lambda: [dish("Crispy chicken sandwich"), dish("Charred corn")],
        ]

        # Past days: finished and picked up, each with a closed cash drawer
        for days_ago in (1, 2, 7):
            opened = now - datetime.timedelta(days=days_ago, minutes=30)
            drawer = CashDrawerSession.objects.create(opened_by=self.cashier, opened_at=opened, opening_amount=Decimal("200.00"))
            for _ in range(random.randint(6, 12)):
                minutes = days_ago * 24 * 60 - random.randint(10, 300)  # while the drawer was open
                past = order(carts[random.randrange(len(carts))](), "store", OrderStation.STATUS_APPROVED, minutes,
                             method=random.choice(["cash", "card", "card"]), drawer=drawer, cashier=self.cashier)
                past.set_station_status([s.code for s in past.stations.all()], OrderStation.STATUS_DONE)
                prep = datetime.timedelta(minutes=random.randint(3, 15))
                Order.objects.filter(pk=past.pk).update(picked_up=True, prep_time=prep)
                past.station_statuses.update(ready_at=past.timestamp + prep)
            CashMovement.objects.create(drawer=drawer, amount=Decimal("-12.50"), reason="Bought ice", user=self.cashier,
                                        timestamp=opened + datetime.timedelta(hours=1))
            cash = sum((p.amount for p in drawer.payments.filter(method="cash")), Decimal("0"))
            expected = drawer.opening_amount + cash - Decimal("12.50")
            drawer.expected_amount = expected
            drawer.counted_amount = expected if days_ago != 2 else expected - Decimal("4.25")
            drawer.closed_at = opened + datetime.timedelta(hours=6)
            drawer.closed_by = self.cashier
            drawer.save()

        self.seed_history(order, carts, skip_days=(1, 2, 7))
        self.seed_order_extras()

        # Today, on the screens
        addons = OptionGroup.objects.get(dish=d["Smash burger"], title="Add-ons")
        order([dish("Smash burger", cust={"parts": {str(burger_parts["Onion"]): 0},
                                          "options": {**well_done, str(addons.id): {str(addons.options.get(title="Avocado").id): 2}}}),
               dish("Fries", cust={"options": option("Fries", "Size", "Large")}) if d["Fries"].available else dish("Charred corn")],
              "store", OrderStation.STATUS_APPROVED, 6, name="Olena", cashier=self.cashier)
        order([lunch("Flat white")], "store", OrderStation.STATUS_APPROVED, 4, name="Sam", special_instructions="Birthday",
              order_discount={"offer": Discount.objects.get(name="Student").id}, cashier=self.cashier)
        order([dish("Iced latte"), dish("Almond croissant")], "store", OrderStation.STATUS_APPROVED, 2, to_go_order=True,
              cashier=self.cashier)
        ready = order([dish("Halloumi grain bowl"), dish("Fresh lemonade")], "store", OrderStation.STATUS_APPROVED, 9, name="Mark")
        ready.set_station_status(["kitchen", "bar"], OrderStation.STATUS_DONE)
        ready.station_statuses.update(ready_at=now - datetime.timedelta(minutes=3))
        order([kids("Green salad")], "web", OrderStation.STATUS_PENDING, 1, name="Iris", phone=5555550123,
              gift_card_payments=[(self.gift_cards[0].number, "5.00")], verify_payment=lambda remaining: None)
        # Deliveries: two looking for a courier (off the KDS until one accepts), and one delivered yesterday. Travel
        # times are filled in so the courier app shows ETAs without a Google key.
        courier = User.objects.get(username="courier")
        for name, phone, address, unit, note, minutes, metres, travel, cart in (
            ("Sofia R.", 5555550188, "200 Oak Ave, Springfield", "", "Leave the package at the door. Side door, buzz 204",
             3, 1200, {"walking": 900, "bicycling": 300, "driving": 240}, [dish("Pulled pork plate"), dish("Charred corn")]),
            ("Leo K.", 5555550149, "588 Elm St, Springfield", "Unit 2", "Meet the customer at the door.",
             1, 2600, {"walking": 1920, "bicycling": 660, "driving": 420}, [dish("Crispy chicken sandwich"), dish("Cookie", 2), dish("Fresh lemonade")]),
        ):
            placed = order(cart, "delivery", OrderStation.STATUS_PENDING, minutes, name=name, phone=phone, to_go_order=True,
                           verify_payment=lambda remaining: None)
            Delivery.objects.create(order=placed, destination=address, address_2=unit, phone=phone, instructions=note,
                                    distance_m=metres, travel_seconds=travel)
        yesterday = order([dish("Smash burger"), dish("Iced latte")], "delivery", OrderStation.STATUS_APPROVED, 24 * 60 + 40,
                          name="Hannah W.", phone=5555550150, to_go_order=True, verify_payment=lambda remaining: None)
        yesterday.set_station_status([s.code for s in yesterday.stations.all()], OrderStation.STATUS_DONE)
        Order.objects.filter(pk=yesterday.pk).update(picked_up=True, prep_time=datetime.timedelta(minutes=11))
        yesterday.refresh_from_db()
        Delivery.objects.create(order=yesterday, destination="1622 Charles St, Springfield", phone=5555550150,
                                instructions="Blue door, knock please", courier=courier, handoff=Delivery.HAND,
                                accepted_at=yesterday.timestamp + datetime.timedelta(minutes=1),
                                picked_up_at=yesterday.timestamp + datetime.timedelta(minutes=14),
                                arrived_at=yesterday.timestamp + datetime.timedelta(minutes=22),
                                delivered_at=yesterday.timestamp + datetime.timedelta(minutes=23),
                                distance_m=600, travel_seconds={"walking": 480, "bicycling": 180, "driving": 180})
        order([dish("Cookie", 3)], "store", OrderStation.STATUS_APPROVED, 1, name="Peter")
        RejectedOrder.objects.create(order_id=Order.objects.latest("id").id + 1000, reason="Demo: out of containers.",
                                     timestamp=now - datetime.timedelta(hours=1))

    def seed_history(self, order, carts, skip_days=()):
        """
        Finished, picked-up orders for the last nine weeks (closed Tuesdays), busier at lunch, dinner and on
        weekends, so the admin dashboard and reports have something to show. Stock is put back each day and at the
        end, so the history never sells out and today's demo orders still have what they need.
        """
        stock = dict(Component.objects.values_list("id", "inventory"))

        def restock():
            for component in Component.objects.all():
                if component.inventory != stock.get(component.id):
                    component.inventory = stock.get(component.id)
                    component.save()

        now = timezone.localtime()
        weights = [(8, 2), (9, 3), (10, 2), (11, 5), (12, 8), (13, 6), (14, 3), (15, 2), (16, 2), (17, 5), (18, 7), (19, 5), (20, 2)]
        hours = [h for h, w in weights for _ in range(w)]
        for days_ago in range(3, self.history_days + 1):
            day = (now - datetime.timedelta(days=days_ago)).date()
            if days_ago in skip_days or day.weekday() == 1:
                continue
            restock()  # as if someone restocked overnight
            count = random.randint(6, 11) + (4 if day.weekday() >= 5 else 0)
            opened = timezone.make_aware(datetime.datetime.combine(day, datetime.time(7, 30)))
            drawer = CashDrawerSession.objects.create(device=self.device, opened_by=self.cashier, opened_at=opened,
                                                      opening_amount=Decimal("200.00"))
            for _ in range(count):
                when = datetime.datetime.combine(day, datetime.time(random.choice(hours), random.randint(0, 59)))
                minutes_ago = int((now - timezone.make_aware(when)).total_seconds() // 60)
                channel = random.choices(["store", "web", "delivery"], weights=[60, 25, 15])[0]
                extra = {"cashier": self.cashier, "drawer": drawer} if channel == "store" else {"verify_payment": lambda remaining: None}
                if channel != "store":
                    extra.update(phone=5555550100 + random.randint(0, 99), to_go_order=True)
                past = order(carts[random.randrange(len(carts))](), channel, OrderStation.STATUS_APPROVED, minutes_ago,
                             method=random.choice(["cash", "card", "card", "card"]), **extra)
                past.set_station_status([s.code for s in past.stations.all()], OrderStation.STATUS_DONE)
                prep = datetime.timedelta(minutes=random.randint(3, 14), seconds=random.randint(0, 59))
                Order.objects.filter(pk=past.pk).update(picked_up=True, prep_time=prep)
                past.station_statuses.update(ready_at=past.timestamp + prep)
            cash = sum((p.amount for p in drawer.payments.filter(method="cash")), Decimal("0"))
            drawer.counted_amount = drawer.expected_amount = drawer.opening_amount + cash
            drawer.closed_at = opened + datetime.timedelta(hours=14)
            drawer.closed_by = self.cashier
            drawer.save()
        restock()

    def seed_order_extras(self):
        """Receipts, a staff note and a couple of refunds on past orders, for the admin's Retail orders page."""
        admin = User.objects.get(username="admin")
        past = list(Order.objects.filter(channel="store").order_by("-timestamp")[:40])
        for order in past[::3]:
            ReceiptLog.objects.create(order=order, channel=ReceiptLog.PRINT, by=self.cashier, at=order.timestamp + datetime.timedelta(seconds=30))
        email = past[4]
        ReceiptLog.objects.create(order=email, channel=ReceiptLog.EMAIL, to="sam.p@example.com", by=admin,
                                  at=email.timestamp + datetime.timedelta(minutes=2))
        past[0].staff_note = "Regular. Likes burgers medium-well."
        past[0].save(update_fields=["staff_note"])
        # Refunds are recorded as the page would, without calling Square
        for order, whole, reason in ((past[6], True, "Wrong order"), (past[9], False, "Quality issue")):
            item = order.items.filter(parent__isnull=True).first()
            amount, lines = refunds.quote(order, {item.id: 1}, whole)
            when = order.timestamp + datetime.timedelta(minutes=15)
            refund = Refund.objects.create(order=order, amount=amount, reason=reason, to=Refund.ORIGINAL, whole=whole,
                                           by=admin, created_at=when)
            RefundItem.objects.bulk_create(RefundItem(refund=refund, item=i, quantity=q) for i, q in lines)
            payment = order.payments.exclude(method=OrderPayment.CASH).first()
            refund.parts = ([{"method": payment.method, "amount": str(amount), "payment": payment.id, "label": payment.card_label}]
                            if payment else [{"method": "cash", "amount": str(amount)}])
            refund.to = Refund.ORIGINAL if payment else Refund.CASH
            refund.save()
