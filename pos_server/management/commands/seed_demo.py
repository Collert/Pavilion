"""
Fill an empty database with demo data for local development and testing.

    python manage.py seed_demo            # refuses if there are already orders or dishes
    python manage.py seed_demo --force    # seed anyway (adds to what's there)

Creates users (admin/admin, cashier/cashier, courier/courier, customer/customer; register PINs: Alex 0000,
Dana 1234, Priya 2468, recently used on the register so its lock screen lists them), stations, ingredients,
components with options, dishes with adjustable parts, add-ons and condiments, combos, menus (one switched on by
hand, some on schedules, a draft), offers and coupons, today's business-day event, promo banners, services (staff,
rentals, venue...) with bookings, gift card presets and cards, a registered device, past cash drawers, orders in
every state across the last few days, and nine weeks of finished orders (closed Tuesdays) for the admin dashboard
and reports.

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
from django.db.models import F
from django.core.management import call_command
from django.utils import timezone

from admin_portal.models import StoreSettings, StoreTheme
from deliveries.models import CourierShift, Delivery
from events.models import Event
from gift_cards.models import CardPreset, GiftCard, StaffCard, StaffCardType, StaffCardUse
from inventory.models import (Restock, RestockLine, CookingSession, IngredientReservation, Packaging, Recipe, RecipeIngredient, RecipeStep,
                              Transformation, YieldEntry)
from online_store.models import PromoContent, RejectedOrder
from pos_server.models import (CashDrawerSession, CashMovement, Combo, ComboSlot, ComboSlotOption, Component,
                               ComponentIngredient, CateringPrice, Condiment, Discount, Dish, Disposable, DishComponent, EligibleDevice, Extra, Ingredient, PartChoice, StorageLocation, Supplier,
                               Menu, MenuRule, Option, OptionGroup, Order, OrderPayment, OrderStation, ReceiptLog, Refund,
                               RefundItem, RegisterSignIn, Station)
from pos_server import refunds
from pos_server.orders import create_order
from pos_server.pricing import price_cart
from screens.models import Screen, ScreenSession
from services.models import Booking, Service
from users.models import Staff

# A fixed token so a dev browser can be registered once: ?device-token=<this> on any POS page
DEMO_DEVICE_TOKEN = "00000000-0000-4000-8000-000000000001"

CONDIMENTS = ["Ketchup", "Mustard", "Mayo", "Hot sauce", "Extra salt packet"]


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
            self.seed_substitutes()
            self.seed_cookery()
            call_command("seed_recipes", stdout=io.StringIO())  # the Cookery mockup's recipes too
            self.seed_component_details()
            self.seed_ingredient_details()
            self.seed_condiments()
            self.seed_disposables()
            self.seed_restocks()
            self.seed_discounts()
            self.seed_events()
            self.seed_store()
            self.seed_services()
            self.seed_catering()
            self.seed_audio()
            self.seed_team()
            self.seed_time_clock()
            self.seed_screens()
            self.seed_gift_cards()
            self.device, _ = EligibleDevice.objects.get_or_create(token=DEMO_DEVICE_TOKEN, defaults={"name": "Register 1"})
            # The register's lock screen offers PIN sign-in to whoever used it last
            for username in ("admin", "priya", "cashier"):  # Dana (cashier) most recent
                for device in (None, self.device):
                    RegisterSignIn.record(device, User.objects.get(username=username))
            Packaging.objects.get_or_create(name="Takeout container", defaults={"quantity": 200, "cost": Decimal("0.35")})
            self.seed_orders()
            self.seed_staff_cards()
            self.seed_account()
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
        user("priya", "Priya", pin="2468", is_staff=True)
        # Priya, the shift lead, is in Managers below, which gives "Give discounts" (typed-in discounts on the
        # register); Dana, a cashier in Front of house, can't
        user("courier", "Courier", is_staff=True)
        # The courier is clocked in, so delivery ETAs and the courier app have someone to work with
        CourierShift.objects.get_or_create(user=User.objects.get(username="courier"), defaults={"mode": "bicycling"})
        # The team: last names, phones, emergency contacts, roles and groups (users/migrations/0004 made the usual
        # groups and roles); the admin is the owner
        from django.contrib.auth.models import Group
        from users.models import Role
        team = [("admin", "Rivera", "(604) 555-0142", "Sam Rivera", "(604) 555-0188", ["Manager"], ["Owners"]),
                ("cashier", "Kim", "(604) 555-0170", "Min Kim", "(604) 555-0129", ["Cashier", "Barista"], ["Front of house"]),
                ("priya", "Shah", "(604) 555-0119", "Raj Shah", "(604) 555-0173", ["Manager", "Cashier"], ["Managers", "Front of house"]),
                ("courier", "Ramos", "(778) 555-0137", "", "", ["Delivery"], ["Delivery"])]
        for username, last, phone, em_name, em_phone, roles, groups in team:
            person = User.objects.get(username=username)
            person.last_name = last
            person.save(update_fields=["last_name"])
            profile = person.staff_profile
            profile.phone, profile.emergency_name, profile.emergency_phone = phone, em_name, em_phone
            profile.save()
            profile.roles.set(Role.objects.filter(name__in=roles))
            person.groups.set(Group.objects.filter(name__in=groups))
        customer = user("customer", "Customer")
        customer.customer_profile.phone = 6045550100
        customer.customer_profile.home_address = "123 Main St, Springfield"
        customer.customer_profile.save()

    # ---------- menu ----------

    def seed_stations(self):
        specs = [
            # code, name, ticket code, icon, menu heading, colour, target ticket minutes
            ("kitchen", "Grill", "GRL", "flame", "Grill", "#12875A", 9),
            ("bar", "Bar", "BAR", "cup", "Bar", "#4E79B5", 4),
            ("bakery", "Bakery", "BAK", "croissant", "Bakery", "#E87BA4", 3),
        ]
        self.stations = {}
        for position, (code, name, tag, icon, heading, color, target) in enumerate(specs):
            station, _ = Station.objects.update_or_create(code=code, defaults={
                "name": name, "ticket_code": tag, "icon": icon, "menu_heading": heading, "position": position, "color": color,
                "target_minutes": target})
            self.stations[code] = station
        Station.objects.filter(code="gng").update(position=len(specs), color="#C9821A", icon="snow", ticket_code="GNG", target_minutes=3)

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

        # title, unit, food/beverage (drinks get the drink category), crafting option, inventory, extra price, [(ingredient, qty)]
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
                ("Hot sauce", "Hot sauce packet"), ("Extra salt packet", "Salt packet")]
        component_specs += [(component, "ea", "food", "buy", 2000, "0", []) for _label, component in side]
        self.side_component = dict(side)

        self.components = {}
        for title, unit, food_type, option, inventory, extra_price, parts in component_specs:
            component, created = Component.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "category": "drink" if food_type == "beverage" else "other", "crafting_option": option, "inventory": inventory,
                "extra_price": Decimal(extra_price)})
            if created:
                for ingredient, qty in parts:
                    ComponentIngredient.objects.create(component=component, ingredient=self.ingredients[ingredient], quantity=qty)
            self.components[title] = component

        c = self.components

        def group(title, options, component=None, dish=None, note="", default=None, pick=OptionGroup.ONE, required=True, addons=False):
            """
            options: "Name", ("Name", "price") or ("Name", "price", {other Option fields}). A required pick-one
            group starts on its first option, so `default` is moved to the front.
            """
            obj = OptionGroup.objects.create(title=title, component=component, dish=dish, note=note, pick=pick, required=required,
                                             addons=addons)
            specs = [(o, "0", {}) if isinstance(o, str) else (o[0], o[1], o[2] if len(o) > 2 else {}) for o in options]
            if default:
                specs.sort(key=lambda spec: spec[0] != default)
            for position, (name, price, more) in enumerate(specs):
                Option.objects.create(group=obj, title=name, price=Decimal(price), position=position, **more)
            return obj

        # Options that belong to a component: every dish using it offers them (substitutes are on each dish's part)
        group("Patty temperature", ["Medium rare", "Medium", "Medium well", "Well done"], component=c["Beef patty"],
              note="Cooked to order", default="Medium")
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
                      pick=OptionGroup.ANY, required=False, addons=True)
            for position, label in enumerate(x for x in condiments if x in self.side_component):  # napkins etc. are disposables
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
        lunch_menu = menu("Lunch", 0, visible, [lunch, kids], "#1f4a3a", manual_on=True)
        breakfast = menu("Breakfast", 1, [latte, flat_white, matcha, bun, croissant, cookie], [], "#b5791f", auto=True,
                         rules=[weekly("on", "01234", 7), weekly("on", "56", 8), weekly("off", "0123456", 11)])
        menu("Happy hour", 2, [spritz, lemonade, fries, corn], [], "#8e2914", auto=True,
             rules=[weekly("on", "234", 16), weekly("off", "234", 18)])
        year = timezone.localdate().year
        holiday = menu("Holiday specials", 3, [latte, bun, brownie, cookie], [], "#2d5a84", auto=True, draft=True,
                       rules=[("on", MenuRule.DATES, {"start_date": datetime.date(year, 12, 1), "end_date": datetime.date(year, 12, 31)})])
        MenuRule.objects.get_or_create(menu=breakfast, kind="off", type=MenuRule.MENU, other_menu=holiday)

        # Menu boards (the Display tab): Lunch turns over two pages, Breakfast has one
        kitchen, bar, bakery = (f"s:{st[code].id}" for code in ("kitchen", "bar", "bakery"))
        timing = {"sold": "show", "page_fx": "fade", "page_sec": 30, "group_fx": "slide", "group_sec": 10}
        if not lunch_menu.display:
            lunch_menu.display = {"template": "menu_showcase", "look": {"badge": "Kitchen open till 8"}, **timing, "pages": [
                {"featured": f"d:{bowl.id}", "slots": [kitchen, bar, bakery]},
                {"featured": f"c:{lunch.id}", "slots": ["combos", bar, bakery]}]}
            lunch_menu.save(update_fields=["display"])
        if not breakfast.display:
            breakfast.display = {"template": "menu_showcase", "look": {"badge": "Fresh from the oven", "accent": "#F2B45A"}, **timing,
                                 "pages": [{"featured": f"d:{bun.id}", "slots": [bar, bakery, ""]}]}
            breakfast.save(update_fields=["display"])
        self.lunch_menu = lunch_menu

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
            recipe = Recipe.objects.create(title=title, yield_quantity=makes, total_minutes=sum(step[2] for step in steps) + 15)
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
            frozen[title] = Component.objects.create(title=title, unit_of_measurement=unit, inventory=inventory,
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

        # Recipes for a transformation (component in, steps), a dish build, and one not attached yet (admin Recipes)
        def recipe(title, makes, total, parts, steps):
            obj = Recipe.objects.create(title=title, yield_quantity=makes, total_minutes=total)
            for n, (item, qty) in enumerate(parts):
                key = "component" if isinstance(item, Component) else "ingredient"
                RecipeIngredient.objects.create(recipe=obj, quantity=qty, position=n, **{key: item})
            for n, (step_title, text, mins, scene) in enumerate(steps):
                RecipeStep.objects.create(recipe=obj, position=n, title=step_title, text=text, scene=scene, duration=minutes(mins) if mins else None)
            return obj
        thaw = recipe("Thaw beef patties", 40, 740, [(frozen["Beef patty, frozen"], 40)], [
            ("Move to the walk-in", "Move frozen patties to a lined tray on the bottom shelf of the walk-in.", 0, "prep"),
            ("Thaw overnight", "Leave covered in the walk-in. Never thaw at room temperature.", 720, "thaw"),
            ("Check and date", "Check they're soft all the way through and label with today's date. Use within 2 days.", 0, "prep")])
        Transformation.objects.filter(pk=transforms["Thaw patties"].pk).update(recipe=thaw)
        build = recipe("Smash burger build", 1, 6, [(c["Brioche bun"], 1), (c["Beef patty"], 1), (c["Cheddar slice"], 1), (c["Smash sauce"], 20)], [
            ("Toast the bun", "Butter and toast the bun cut side down.", 1, "fry"),
            ("Smash the patty", "Smash the ball hard on the hot plate, season, flip after 90 seconds and add cheese.", 2, "fry"),
            ("Build", "Sauce, patty, pickles and onion, top bun. Wrap halfway in paper.", 0, "prep")])
        Dish.objects.filter(title="Smash burger").update(recipe=build)
        recipe("Brioche buns", 24, 660, [(ing["Flour"], 1400), (ing["Butter"], 350), (ing["Eggs"], 6), (ing["Milk"], 400), (ing["Sugar"], 120)], [
            ("Mix", "Mix the dough and work in the butter a little at a time.", 0, "prep"),
            ("Cold rise", "Rise overnight in the fridge.", 600, "chill")])

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

    def storage_locations(self):
        """{key: StorageLocation} for the places the seeds put things."""
        names = {"freezer": "Freezer", "fridge": "Walk-in fridge", "line": "Line fridge", "hot": "Hot holding", "dry": "Dry store",
                 "counter": "Display case", "bar": "Bar fridge"}
        return {key: StorageLocation.named(name) for key, name in names.items()}

    def seed_substitutes(self):
        """Parts customers can swap: another bun on every burger, another milk in every coffee."""
        c = self.components
        swaps = {  # component: (what the register calls it, [(substitute, upcharge)])
            "Brioche bun": ("Bun", [("Sesame bun", "0"), ("Gluten-free bun", "1.50"), ("Lettuce wrap", "0")]),
            "Whole milk": ("Milk", [("Oat milk", "0.60"), ("Almond milk", "0.60"), ("Skim milk", "0")]),
        }
        for title, (label, choices) in swaps.items():
            for dc in DishComponent.objects.filter(component=c[title]):
                DishComponent.objects.filter(pk=dc.pk).update(choice_title=label)
                for position, (name, price) in enumerate(choices):
                    PartChoice.objects.create(part=dc, component=c[name], price=Decimal(price), position=position)

    def seed_component_details(self):
        """Categories, where things are kept and for how long, and extra labels (the Components page)."""
        category = {
            "protein": ["Beef patty", "Beef patty, frozen", "Chicken fillet", "Pulled pork", "Pulled pork, frozen", "Chicken bites",
                        "Bacon", "Fried egg", "Halloumi", "Pierogi, potato & cheese", "Pierogi, frozen"],
            "bread": ["Brioche bun", "Sesame bun", "Gluten-free bun", "Cornbread", "Sourdough loaf", "Croutons", "Brioche buns, house-made"],
            "dairy": ["Cheddar slice", "Swiss cheese", "Cotija", "Butter pat", "Cream cheese", "Whipped cream"],
            "sauce": ["Smash sauce", "Spicy mayo", "Garlic aioli", "BBQ sauce", "Dressing", "Pickles", "Pickled onion",
                      "Chili lime butter", "Jam", "Seasoning", "Beef stock"],
            "veg": ["Lettuce wrap", "Lettuce", "Tomato", "Onion", "Slaw", "Mushrooms", "Grains", "Greens", "Cucumber",
                    "Fries portion", "Corn cob", "Avocado", "Caramelized onion"],
            "bakery": ["Cardamom bun", "Almond croissant", "Brownie", "Lemon tart", "Cookie"],
        }
        storage = {  # title: (kept in, keeps for days)
            "Beef patty": ("line", 2), "Beef patty, frozen": ("freezer", 90), "Pulled pork": ("hot", 0.25),
            "Pulled pork, frozen": ("freezer", 60), "Chicken fillet": ("fridge", 2), "Chicken bites": ("fridge", 2),
            "Bacon": ("line", 2), "Fried egg": ("line", 0.1), "Halloumi": ("line", 3), "Fries portion": ("fridge", 2),
            "Slaw": ("line", 2), "Smash sauce": ("line", 5), "Spicy mayo": ("line", 5), "Garlic aioli": ("line", 5),
            "Pickled onion": ("fridge", 14), "Cornbread": ("counter", 2), "Grains": ("fridge", 4),
            "Espresso shot": ("dry", 1), "Pierogi, potato & cheese": ("fridge", 1), "Pierogi, frozen": ("freezer", 90), "Lemonade": ("line", 3), "Cold brew": ("fridge", 7),
        }
        for key, titles in category.items():
            Component.objects.filter(title__in=titles).update(category=key)
        places = self.storage_locations()
        Component.objects.update(storage=places["fridge"])
        for title, (place, days) in storage.items():
            Component.objects.filter(title=title).update(storage=places[place], keeps_days=days)
        Component.objects.filter(category__in=["bread", "bakery"]).exclude(title__in=storage).update(storage=places["dry"], keeps_days=2)
        Component.objects.filter(category="drink", storage=places["fridge"]).update(keeps_days=7)
        labels = {"Beef patty": "Extra patty", "Cheddar slice": "Extra cheese", "Espresso shot": "Extra shot",
                  "Pulled pork": "Extra pulled pork", "Halloumi": "Extra halloumi", "Mushrooms": "Add mushrooms",
                  "Swiss cheese": "Extra Swiss", "Chicken fillet": "Extra chicken", "Cornbread": "Add cornbread", "Cotija": "Extra cotija"}
        for title, label in labels.items():
            Component.objects.filter(title=title).update(extra_label=label)
        Component.objects.create(title="Seasonal soup base", unit_of_measurement="ml", category="veg", crafting_option="craft",
                                 inventory=0, archived=True)

    def seed_ingredient_details(self):
        """How each ingredient is bought (supplier, pack size and price, which sets its cost), stored and counted."""
        suppliers = {name: Supplier.objects.get_or_create(name=name)[0] for name in (
            "Valley Meats", "Dairyland", "Sun Valley Farms", "Corner Bakehouse", "Ridgeline Coffee Roasters", "Pacific Foodservice")}
        meat, dairy, farm, bakery, coffee, food = suppliers.values()
        places = self.storage_locations()
        # title: (category, supplier, pack size, pack price, kept in, par, % the price went up at the last delivery)
        specs = {
            "Ground beef": ("meat", meat, 5000, "75.00", "fridge", 8000, 3.8),
            "Chicken": ("meat", meat, 10000, "120.00", "fridge", 10000, None),
            "Pork shoulder": ("meat", meat, 10000, "100.00", "fridge", 12000, None),
            "Beef bones": ("meat", meat, 5000, "15.00", "freezer", None, None),
            "Milk": ("dairy", dairy, 4000, "8.00", "fridge", 16000, None),
            "Cheddar": ("dairy", dairy, 2500, "50.00", "fridge", 5000, 4.2),
            "Eggs": ("dairy", dairy, 180, "54.00", "fridge", 120, None),
            "Butter": ("dairy", dairy, 454, "4.54", "fridge", 2000, None),
            "Farmer's cheese": ("dairy", dairy, 2000, "19.00", "fridge", 2000, None),
            "Potatoes": ("produce", farm, 22700, "68.10", "dry", 45000, None),
            "Vegetables": ("produce", farm, 10000, "40.00", "fridge", 20000, -2.5),
            "Lemons": ("produce", farm, 115, "46.00", "fridge", 80, None),
            "Yellow onions": ("produce", farm, 10000, "25.00", "dry", 5000, None),
            "Carrots": ("produce", farm, 10000, "19.00", "fridge", 4000, None),
            "Celery": ("produce", farm, 5000, "12.50", "fridge", None, None),
            "Flour": ("bakery", food, 20000, "40.00", "dry", 10000, None),
            "Instant yeast": ("bakery", food, 454, "9.08", "fridge", 200, None),
            "Oats": ("dry", food, 5000, "20.00", "dry", 5000, None),
            "Almonds": ("dry", food, 1000, "20.00", "dry", 2000, None),
            "Sugar": ("dry", food, 20000, "40.00", "dry", 8000, None),
            "Sesame seeds": ("dry", food, 1000, "10.00", "dry", 500, None),
            "Mustard seed": ("dry", food, 500, "10.00", "dry", 300, None),
            "Kosher salt": ("dry", food, 3000, "6.00", "dry", 2000, None),
            "Black pepper": ("dry", food, 500, "12.50", "dry", 200, None),
            "Bay leaves": ("dry", food, 100, "5.00", "dry", None, None),
            "Smoked paprika": ("dry", food, 500, "14.00", "dry", None, None),
            "Mayonnaise": ("pantry", food, 3780, "39.70", "fridge", 3780, None),
            "Dill pickles": ("pantry", food, 2000, "20.00", "fridge", 1000, None),
            "Yellow mustard": ("pantry", food, 1000, "9.00", "fridge", 500, None),
            "Coffee beans": ("drinks", coffee, 2270, "68.10", "dry", 9000, 6.5),
            "Water": ("other", None, 1, None, "dry", None, None),
        }
        # Bought-in components with no ingredients get the ingredient they're bought as:
        # component: (ingredient, unit, category, supplier, pack size, pack price, allergens, animal byproduct, amount per unit)
        bought = {
            "Gluten-free bun": ("Gluten-free buns", "ea", "bakery", bakery, 12, "10.20", ["Egg"], True, 1),
            "BBQ sauce": ("BBQ sauce", "ml", "pantry", food, 4000, "24.00", ["Mustard"], False, 1),
            "Pickles": ("Pickle chips", "ea", "pantry", food, 500, "25.00", ["Sulphites"], False, 1),
            "Grains": ("Farro", "g", "dry", food, 5000, "30.00", ["Gluten"], False, 1),
            "Dressing": ("Vinaigrette", "ml", "pantry", food, 4000, "32.00", ["Mustard"], False, 1),
            "Seasoning": ("Seasoning blend", "g", "dry", food, 1000, "20.00", [], False, 1),
            "Sparkling water": ("Sparkling water", "ml", "drinks", food, 12000, "24.00", [], False, 1),
            "Matcha powder": ("Matcha powder", "g", "drinks", coffee, 100, "40.00", [], False, 1),
            "Jam": ("Jam portions", "ea", "pantry", food, 200, "50.00", [], False, 1),
            "Extra ginger": ("Ginger", "g", "produce", farm, 1000, "10.00", [], False, 0.2),
        }
        sauces = {"Ketchup": [], "Mustard": ["Mustard"], "Mayo": ["Egg"], "Hot sauce": []}
        for label, component_title in self.side_component.items():
            bought.setdefault(component_title, (component_title, "ea", "pantry" if label in sauces else "other", food, 1000,
                                                "30.00" if label in sauces else "20.00", sauces.get(label, []), label == "Mayo", 1))
        for component_title, (title, unit, category, supplier, pack, price, allergens, animal, amount) in bought.items():
            component = Component.objects.filter(title=component_title).first()
            if component is None or component.componentingredient_set.exists() or component.recipe_id:
                continue
            ingredient, _created = Ingredient.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "inventory": pack * 2, "allergens": allergens, "animal": animal})
            specs.setdefault(title, (category, supplier, pack, price, "dry" if category in ("dry", "other") else "fridge", pack, None))
            ComponentIngredient.objects.create(component=component, ingredient=ingredient, quantity=amount)
        for title, (category, supplier, pack, price, place, par, change) in specs.items():
            ingredient = Ingredient.objects.filter(title=title).first()
            if ingredient is None:
                continue
            ingredient.category, ingredient.supplier, ingredient.pack_size, ingredient.par = category, supplier, pack, par
            ingredient.storage = places[place]
            ingredient.pack_price = Decimal(price) if price else None
            ingredient.save()
            if change:
                Ingredient.objects.filter(pk=ingredient.pk).update(previous_cost=(ingredient.cost / Decimal(str(1 + change / 100))).quantize(Decimal("0.000001")))
        Ingredient.objects.filter(title="Mayonnaise").update(allergens=["Egg"], animal=True)
        Ingredient.objects.create(title="Saffron", unit_of_measurement="g", category="dry", supplier=food, pack_size=10,
                                  pack_price=Decimal("28.00"), archived=True)

    def seed_condiments(self):
        """Condiments: the packets dishes already offer get a profile; three house-made ones come from prep recipes."""
        places = self.storage_locations()
        bought = {  # component: (served as, serving, free per order, price each, dine-in, ask on takeout)
            "Ketchup packet": ("packet", "9 g packet", 3, "0.10", "station", True),
            "Mustard packet": ("packet", "5 g packet", 2, "0.10", "station", True),
            "Mayo packet": ("packet", "12 g packet", 2, "0.10", "station", True),
            "Hot sauce packet": ("packet", "7 g packet", 2, "0.25", "ask", True),
            "Salt packet": ("packet", "1 g packet", Condiment.NO_LIMIT, "0", "table", False),
        }
        for title, (served, serving, free, price, dine, ask) in bought.items():
            component = Component.objects.filter(title=title).first()
            if component:
                Component.objects.filter(pk=component.pk).update(category="sauce", storage=places["dry"], keeps_days=365, par=500)
                Condiment.objects.create(component=component, served_as=served, serving=serving, free_per_order=free,
                                         price=Decimal(price), dine_in=dine, ask_on_takeout=ask)
        i = {ingredient.title: ingredient for ingredient in Ingredient.objects.all()}
        made = [  # title, recipe: (yield in servings, [(ingredient, amount)]), stock, par, free, price, dishes
            ("Chipotle mayo", (50, [("Mayonnaise", 1500), ("Smoked paprika", 30), ("Kosher salt", 10)]), 44, 40, 1, "0.75",
             ["Fries", "Crispy chicken sandwich", "Kids chicken bites"]),
            ("Buttermilk ranch", (45, [("Mayonnaise", 500), ("Milk", 500), ("Black pepper", 10)]), 12, 30, 1, "0.75",
             ["Fries", "Kids chicken bites"]),
            ("Maple butter", (60, [("Butter", 1000), ("Sugar", 250)]), 58, 40, 0, "1.25", ["Cardamom bun"]),
        ]
        for title, (makes, parts), stock, par, free, price, dishes in made:
            recipe = Recipe.objects.create(title=title, yield_quantity=makes, total_minutes=15)
            for position, (name, amount) in enumerate(parts):
                RecipeIngredient.objects.create(recipe=recipe, ingredient=i[name], quantity=amount, position=position)
            RecipeStep.objects.create(recipe=recipe, title="Mix", text="Whisk everything together, then portion into 2 oz cups.", scene="prep")
            component = Component.objects.create(title=title, unit_of_measurement="ea", category="sauce", crafting_option="craft",
                                                  recipe=recipe, inventory=stock, par=par, storage=places["line"], keeps_days=5)
            Condiment.objects.create(component=component, served_as="cup", serving="2 oz cup", free_per_order=free,
                                     price=Decimal(price), dine_in="ask", ask_on_takeout=True)
            for position, dish in enumerate(Dish.objects.filter(title__in=dishes)):
                Extra.objects.create(dish=dish, component=component, quantity=1, max_count=10, position=40 + position)

    def seed_disposables(self):
        """Packaging: each is an ingredient (case price, stock) with how orders use it; a paper bag fee on pickup."""
        places = self.storage_locations()
        front, bar_shelf = StorageLocation.named("Front counter"), places["bar"]
        eco, paper = Supplier.objects.get_or_create(name="EcoPack Supply")[0], Supplier.objects.get_or_create(name="Corner Paper Co.")[0]
        food = Supplier.objects.get(name="Pacific Foodservice")
        dishes = {d.title: d for d in Dish.objects.all()}
        kitchen = [t for t in dishes if dishes[t].station.code == "kitchen"]
        bar = [t for t in dishes if dishes[t].station.code == "bar"]
        hot = [t for t in bar if t in ("Flat white", "Matcha", "Americano", "Chai latte", "Latte")]
        cold = [t for t in bar if t not in hot]
        burgers = [t for t in kitchen if "burger" in t.lower() or "sandwich" in t.lower() or "melt" in t.lower()]
        sides = [t for t in kitchen if t in ("Fries", "Charred corn", "Kids chicken bites")]
        plates = [t for t in kitchen if t not in burgers and t not in sides]
        # title, size, category, material, bin, supplier, case, case price, on hand, par, stored, counted, types, dishes or count, fee
        T, P = ["pickup", "delivery"], ["pickup", "delivery", "catering"]
        specs = [
            ("Burger clamshell", "6 in", "containers", "bagasse", "compost", eco, 500, "62.50", 620, 500, None, "dish", T, burgers, None),
            ("Fry boat", "Medium", "containers", "paper", "compost", eco, 1000, "48.00", 380, 600, None, "dish", T, sides, None),
            ("Meal box with lid", "32 oz", "containers", "kraft", "compost", eco, 200, "46.00", 260, 200, None, "dish", P, plates, None),
            ("Hot cup", "12 oz", "cups", "paper", "compost", eco, 1000, "112.00", 1450, 1000, bar_shelf, "dish", T, hot, None),
            ("Cold cup", "16 oz", "cups", "pet", "recycle", food, 1000, "96.00", 1100, 800, bar_shelf, "dish", T, cold, None),
            ("Sip lid", "16 oz", "cups", "pet", "recycle", food, 1000, "44.00", 1200, 800, bar_shelf, "dish", T, cold, None),
            ("Paper straw", "", "cups", "paper", "compost", eco, 2500, "40.00", 2100, 1000, bar_shelf, "request", ["dine_in", "pickup", "delivery"], [], None),
            ("Cutlery kit", "Fork, knife, napkin", "cutlery", "wood", "compost", eco, 500, "65.00", 140, 300, front, "request", P, [], None),
            ("Napkins", "", "cutlery", "paper", "compost", paper, 6000, "54.00", 7800, 6000, front, "order", ["dine_in", "pickup", "delivery"], 2, None),
            ("Paper bag", "Large", "bags", "kraft", "recycle", paper, 250, "55.00", 300, 500, front, "order", ["pickup"], 1, ("Paper bag", "0.25")),
            ("Tamper-proof seal", "", "bags", "paper", "landfill", paper, 1000, "28.00", 860, 500, front, "order", ["delivery"], 1, None),
            ("Plate", "10 in", "plates", "bagasse", "compost", eco, 500, "45.00", 450, 300, None, "guest", ["catering"], 1, None),
        ]
        for title, size, category, material, bin_, supplier, case, price, stock, par, place, counted, types, use, fee in specs:
            ingredient = Ingredient.objects.create(title=title, unit_of_measurement="ea", category="other", supplier=supplier, pack_size=case,
                                                   pack_price=Decimal(price), inventory=stock, par=par, storage=place or places["dry"])
            disposable = Disposable.objects.create(ingredient=ingredient, size=size, category=category, material=material, bin=bin_,
                                                   order_types=types, counted=counted, count=use if isinstance(use, int) else 1,
                                                   fee=bool(fee), fee_label=fee[0] if fee else "", fee_price=Decimal(fee[1]) if fee else Decimal("0"))
            if isinstance(use, list):
                disposable.dishes.set([dishes[t] for t in use])
        Ingredient.objects.filter(title="Hot cup").update(previous_cost=Decimal("0.104"))  # went up at the last delivery
        retired = Ingredient.objects.create(title="Plastic straw", unit_of_measurement="ea", category="other", pack_size=5000,
                                            pack_price=Decimal("25.00"), archived=True)
        Disposable.objects.create(ingredient=retired, category="cups", material="pp", bin="recycle", counted="request", order_types=["pickup"])

    def seed_restocks(self):
        """Purchase orders: deliveries over the last weeks (some prices changed, most with a receipt), two on the way and a draft."""
        today = timezone.localdate()
        day = lambda n: today + datetime.timedelta(days=n)  # noqa: E731
        priya, admin = User.objects.get(username="priya"), User.objects.get(username="admin")
        i = {ingredient.title: ingredient for ingredient in Ingredient.objects.all()}
        supplier = {x.name: x for x in Supplier.objects.all()}
        # supplier, status, created, placed, expected, delivered, [(ingredient, cases, case price, last paid)], fee, tax, receipt, by
        specs = [
            ("Pacific Foodservice", "delivered", -20, -20, -17, -17, [("Flour", 2, None, "38.00"), ("Sugar", 1, None, None), ("Mayonnaise", 1, None, None)], "0", "4.80", "pdf", admin),
            ("Corner Bakehouse", "delivered", -14, -14, -13, -13, [("Gluten-free buns", 6, None, "9.60")], "0", "0", None, priya),
            ("Ridgeline Coffee Roasters", "delivered", -13, -13, -10, -10, [("Coffee beans", 3, None, "63.95")], "0", "0", "pdf", admin),
            ("Corner Paper Co.", "delivered", -11, -11, -7, -7, [("Paper bag", 4, None, "50.00"), ("Napkins", 1, None, None)], "0", "5.45", "pdf", admin),
            ("Sun Valley Farms", "delivered", -8, -8, -5, -5, [("Potatoes", 2, None, "62.00"), ("Lemons", 1, None, None), ("Vegetables", 2, None, "41.00")], "10.00", "0", "img", priya),
            ("Dairyland", "delivered", -6, -6, -3, -3, [("Eggs", 1, None, "50.80"), ("Butter", 6, None, None), ("Milk", 4, None, None), ("Cheddar", 1, None, "48.00")], "0", "0", "pdf", admin),
            ("EcoPack Supply", "placed", -2, -2, 3, None, [("Fry boat", 1, None, None), ("Cutlery kit", 1, "65.00", None), ("Hot cup", 1, "118.00", "112.00")], "0", "0", None, priya),
            ("Valley Meats", "placed", -1, -1, 2, None, [("Ground beef", 3, "78.00", "75.00"), ("Chicken", 1, None, None)], "15.00", "0", None, admin),
            ("Pacific Foodservice", "draft", 0, None, 3, None, [("BBQ sauce", 2, None, None), ("Pickle chips", 1, None, None), ("Flour", 1, None, None)], "0", "0", None, priya),
        ]
        for name, status, created, placed, expected, delivered, lines, fee, tax, receipt, by in specs:
            restock = Restock.objects.create(supplier=supplier[name], status=status, created_on=day(created),
                                             placed_on=day(placed) if placed is not None else None, expected_on=day(expected) if expected is not None else None,
                                             delivered_on=day(delivered) if delivered is not None else None, fee=Decimal(fee), tax=Decimal(tax), created_by=by,
                                             received_at=timezone.now() + datetime.timedelta(days=delivered) if delivered is not None else None,
                                             notes="Back door, before 9 AM please." if name == "Valley Meats" else "")
            for position, (title, cases, price, was) in enumerate(lines):
                ingredient = i[title]
                case_price = Decimal(price) if price else ingredient.pack_price
                RestockLine.objects.create(restock=restock, ingredient=ingredient, cases=cases, case_price=case_price,
                                           was_price=Decimal(was) if was else case_price, pack_size=ingredient.pack_size, position=position)
            if receipt:
                content = placeholder_image(f"{restock.number} {name}", "#e8ede9", size=(600, 800)) if receipt == "img" else \
                    ContentFile(b"%PDF-1.4\n% demo invoice\n")
                restock.receipt.save(f"{name.split()[0]}-{restock.id}." + ("png" if receipt == "img" else "pdf"), content, save=True)

    def seed_catering(self):
        """Catering: settings, more dishes on for catering, and orders in every state (new, one asking for less
        notice, waiting on the customer, confirmed with deposits, completed, rejected), made through catering.flow."""
        from catering import flow as cflow, pricing as cpricing
        from catering.models import CateringLine, CateringMessage, CateringOrder, CateringSettings

        cfg = CateringSettings.load()
        cfg.policy = ("Free cancellation up to 7 days before your event. Within 7 days, 50% of the food total is non-refundable. "
                      "Services can be cancelled for free up to 48 hours before.")
        cfg.auto_reply = "Thanks! We’ll confirm your catering order within one business day."
        cfg.save()
        # More dishes for catering: a little off retail, more off for big orders
        for title in ("Crispy chicken sandwich", "Pulled pork plate", "Charred corn", "Kids cheeseburger", "Fresh lemonade",
                      "Flat white", "Iced latte", "Almond croissant", "Brownie"):
            dish = Dish.objects.filter(title=title, catering=False).first()
            if dish:
                dish.catering, dish.catering_min = True, 10
                dish.save(update_fields=["catering", "catering_min"])
                for quantity, off in ((10, Decimal("0.95")), (40, Decimal("0.88"))):
                    price = (dish.price * off * 20).quantize(Decimal("1")) / 20
                    CateringPrice.objects.create(dish=dish, quantity=quantity, price=price.quantize(Decimal("0.01")))
        # Venues and classes are booked on their own, not added to catering; the grill and chef need a chat first
        Service.objects.filter(title__in=["Full restaurant buyout", "Private back room", "Pasta making class", "Chef’s tasting menu",
                                          "Mobile flat-top grill", "Chef on site"]).update(catering=False)

        dish = {d.title: d for d in Dish.objects.filter(catering=True)}
        service = {x.title: x for x in Service.objects.all()}
        condiment = {c.component.title: c for c in Condiment.objects.select_related("component")}
        disposable = {d.ingredient.title: d for d in Disposable.objects.select_related("ingredient")}
        priya = User.objects.get(username="priya")
        now, today = timezone.now(), timezone.localdate()

        def at(days, hour, minute=0):
            day = today + datetime.timedelta(days=days)
            return min(timezone.make_aware(datetime.datetime(day.year, day.month, day.day, hour, minute)), now - datetime.timedelta(minutes=20))

        def stamp(order, when):
            """Date the steps a flow call just added (they were stamped now)."""
            CateringMessage.objects.filter(order=order, at__gte=now).update(at=when)

        def order(name, title, days, time, fulfilment, placed, card, lines, org="", address="", note="", phone="604-555-0100"):
            brand, last4 = card.split()
            o = CateringOrder.objects.create(
                name=name, organization=org, email=name.lower().replace(" ", ".") + "@example.com", phone=phone, title=title,
                event_date=today + datetime.timedelta(days=days), event_time=datetime.time(*time), fulfilment=fulfilment,
                address=address, note=note, placed_at=placed, square_customer_id=f"demo:cust-{last4}",
                square_card_id=f"demo:card-{last4}", card_brand=brand, card_last4=last4)
            for position, (kind, ref, quantity, *more) in enumerate(lines):
                item = {"dish": dish, "service": service, "condiment": condiment, "disposable": disposable}[kind][ref]
                amount = more[0] if more else (1 if kind == "service" and item.pricing in (Service.HOUR, Service.DAY) else 0)
                price = Decimal(more[1]) if len(more) > 1 else cpricing.catalog_price(kind, item, quantity)
                CateringLine.objects.create(order=o, kind=kind, position=position, quantity=quantity, amount=amount, price=price,
                                            **{CateringLine.FIELDS[kind]: item})
            cflow.place(o, quiet=True)
            CateringMessage.objects.filter(order=o).update(at=placed)
            return o

        D, S = "dish", "service"
        order("Ben Carter", "Office lunch", 10, (12, 0), CateringOrder.DELIVERY, at(-1, 15, 42), "VISA 4242",
              [(D, "Smash burger", 20), (D, "Halloumi grain bowl", 20), (D, "Fries", 40), (D, "Fresh lemonade", 40), (D, "Brownie", 40),
               (S, "Catering delivery", 1), (S, "Setup and teardown", 1, 2), (S, "Chafing dish set", 4)],
              org="Hartley & Co.", address="1100 Melville St, 14th floor", phone="604-555-0142",
              note="Three people are gluten-free, please label their bowls. Use the loading bay elevator.")
        order("Anjali Rao", "Rao–Mehta wedding brunch", 25, (10, 30), CateringOrder.DELIVERY, at(0, 9, 12), "MASTERCARD 8810",
              [(D, "Cardamom bun", 120), (D, "Almond croissant", 120), (D, "Halloumi grain bowl", 60), (D, "Pulled pork plate", 60),
               (D, "Flat white", 120), (D, "Fresh lemonade", 120), (S, "Catering delivery", 1), (S, "Event server", 4, 5), (S, "Bartender", 1, 5),
               (S, "Coffee urn, 100 cups", 3), (S, "Chafing dish set", 8), (S, "Folding tables and chairs", 12),
               (S, "Custom celebration cake", 1), (S, "Printed event menus", 120)],
              address="Cedar Hall, 2150 W 4th Ave", phone="778-555-0198",
              note="Two tiers for the cake if possible, ivory and gold. The hall opens at 8:30.")
        order("Noor Haddad", "Leo’s 7th birthday", 4, (14, 0), CateringOrder.PICKUP, at(0, 8, 40), "VISA 0019",
              [(D, "Kids cheeseburger", 12), (D, "Fries", 25), (D, "Fresh lemonade", 25), (D, "Brownie", 25), (S, "Custom celebration cake", 1)],
              phone="604-555-0171", note="Sorry for the short notice, our other plan fell through. Could the cake be dinosaur themed?")

        # Waiting: corn is off for winter, so fries instead, and clamshells for 60 delivered meals
        lumen = order("Sofia Moreau", "Holiday party", 53, (18, 0), CateringOrder.DELIVERY, at(-5, 11, 20), "AMEX 1007",
                      [(D, "Crispy chicken sandwich", 30), (D, "Smash burger", 30), (D, "Charred corn", 60), (D, "Fresh lemonade", 60),
                       (D, "Brownie", 60), (S, "Catering delivery", 1), (S, "Chafing dish set", 6)],
                      org="Lumen Studio", address="355 Railway St, unit 200", phone="604-555-0110",
                      note="Can we do the charred corn? Everyone loved it at our summer party.")
        draft = cpricing.lines_of(lumen)
        corn = next(l for l in draft if l["ref"] == dish["Charred corn"].id)
        corn["removed"] = True
        fries = dish["Fries"]
        draft.insert(2, {"id": "", "kind": D, "ref": fries.id, "quantity": 60, "amount": 0, "price": str(fries.catering_price(60)), "removed": False})
        clam = disposable["Burger clamshell"]
        draft.append({"id": "", "kind": "disposable", "ref": clam.id, "quantity": 60, "amount": 0, "price": "0.75", "removed": False})
        cflow.propose(lumen, priya, draft, "Hi Sofia! Charred corn is off the menu for winter, so I swapped in fries. For 60 delivered "
                      "meals we pack everything in compostable clamshells, which adds $45. Let me know if this works!", quiet=True)
        stamp(lumen, at(-3, 10, 5))
        CateringOrder.objects.filter(pk=lumen.pk).update(proposed_at=at(-3, 10, 5))
        CateringMessage.objects.create(order=lumen, who=CateringMessage.CUSTOMER, at=at(-2, 16, 31),
                                       text="Thanks! Checking with my boss, I’ll confirm this week.")

        # Confirmed, deposit hold still to come
        kenji = order("Kenji Watanabe", "Watanabe family reunion", 5, (12, 0), CateringOrder.PICKUP, at(-12, 19, 2), "VISA 5521",
                      [(D, "Pulled pork plate", 35), (D, "Charred corn", 35), (D, "Halloumi grain bowl", 10), (D, "Fresh lemonade", 35),
                       (D, "Cardamom bun", 35), (S, "Chafing dish set", 3), (S, "Coffee urn, 100 cups", 1)], phone="604-555-0133")
        cflow.accept(kenji, priya, "See you on the 25th, Kenji! Everything will be packed and labelled by 11:45.", quiet=True)
        stamp(kenji, at(-11, 10, 15))
        CateringOrder.objects.filter(pk=kenji.pk).update(accepted_at=at(-11, 10, 15))
        # Confirmed and over: the deposit is held, waiting for the equipment to come back
        grace = order("Grace Lin", "Graduation backyard party", -1, (17, 0), CateringOrder.DELIVERY, at(-16, 13, 5), "VISA 6604",
                      [(D, "Smash burger", 25), (D, "Fries", 25), (D, "Fresh lemonade", 25), (S, "Catering delivery", 1), (S, "Chafing dish set", 2)],
                      address="4120 Fraser St, back gate", phone="604-555-0166")
        cflow.accept(grace, priya, "", quiet=True)
        stamp(grace, at(-15, 9, 30))
        CateringOrder.objects.filter(pk=grace.pk).update(accepted_at=at(-15, 9, 30))
        # Completed: both urns came back
        jordan = order("Jordan Lee", "Post-race brunch", -10, (9, 0), CateringOrder.DELIVERY, at(-26, 8, 15), "VISA 3310",
                       [(D, "Almond croissant", 30), (D, "Cardamom bun", 30), (D, "Flat white", 30), (S, "Coffee urn, 100 cups", 2),
                        (S, "Catering delivery", 1)], org="Northside Run Club", address="Trout Lake Park, north lot", phone="604-555-0187")
        cflow.accept(jordan, priya, "", quiet=True)
        stamp(jordan, at(-26, 12, 40))
        jordan.refresh_from_db()
        cflow.finish(jordan, priya)
        stamp(jordan, at(-8, 9, 0))
        CateringOrder.objects.filter(pk=jordan.pk).update(accepted_at=at(-26, 12, 40), closed_at=at(-8, 9, 0))
        # Rejected
        maya = order("Maya Singh", "Aid station snacks", 12, (6, 0), CateringOrder.PICKUP, at(-8, 13, 30), "VISA 7781",
                     [(D, "Cardamom bun", 400), (D, "Fresh lemonade", 400)], org="Seawall Half Marathon", phone="604-555-0155")
        cflow.reject(maya, priya, "Too large for us", "Thanks for thinking of us, Maya! 400 portions by 6 AM is more than our kitchen "
                     "can do on a race weekend. We’d love to help with something smaller next year.", quiet=True)
        stamp(maya, at(-7, 9, 20))
        CateringOrder.objects.filter(pk=maya.pk).update(closed_at=at(-7, 9, 20))

    def seed_account(self):
        """The customer login's account: a name, preferences, a test card, two addresses and the holiday party request."""
        from catering.models import CateringOrder
        from users.models import Customer, SavedAddress, SavedCard
        user = User.objects.get(username="customer")
        user.first_name, user.last_name = "Sofia", "Moreau"
        user.save(update_fields=["first_name", "last_name"])
        profile, _created = Customer.objects.get_or_create(user=user)
        profile.phone, profile.organization, profile.newsletter = 6045550110, "Lumen Studio", True
        profile.square_customer_id = "demo:cust-sofia"
        profile.save()
        SavedCard.objects.create(user=user, square_customer_id="demo:cust-sofia", square_card_id="demo:card-sofia-amex",
                                 brand="AMEX", last4="1007", exp_month=3, exp_year=2029, is_default=True)
        SavedAddress.objects.create(user=user, label="Work", line="355 Railway St", unit="unit 200", city="Vancouver",
                                    postal="V6A 1A4", note="Loading bay off the lane")
        SavedAddress.objects.create(user=user, label="Home", line="455 E 8th Ave", unit="apt 3", city="Vancouver",
                                    postal="V5T 1S2", note="Buzz 03")
        CateringOrder.objects.filter(name="Sofia Moreau").update(customer=user)

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
        """Store hours: weekdays 7 to 7 (Friday with a mid-afternoon break and open late), Saturday till 1 AM, a short
        Sunday. Events on top: today stays open till midnight so the demo store takes online orders whenever you try it,
        openings for a market brunch, trivia and the patio, the holidays coming up, a training day, closing early on
        Halloween and an afternoon without delivery."""
        from events.models import StoreHours
        cfg = StoreHours.load()
        cfg.week = [{"open": True, "r": [[420, 1140]]}, {"open": True, "r": [[420, 1140]]}, {"open": True, "r": [[420, 1140]]},
                    {"open": True, "r": [[420, 1140]]}, {"open": True, "r": [[420, 840], [900, 1320]]},
                    {"open": True, "r": [[480, 1500]]}, {"open": True, "r": [[540, 900]]}]
        cfg.pickup_after, cfg.pickup_before, cfg.delivery_after, cfg.delivery_before, cfg.last_call = 15, 20, 30, 45, 15
        cfg.save()
        now = timezone.localtime()
        today = now.date()
        opens = min(420, now.hour * 60 + now.minute) // 15 * 15
        everything = {"dine_in": True, "pickup": True, "delivery": True}
        Event.objects.update_or_create(title="Open late tonight", defaults={
            "kind": Event.OPEN, "start_date": today, "end_date": today, "opens": opens, "closes": 1440, "banner": False, **everything})

        def upcoming(month, day):
            date = datetime.date(today.year, month, day)
            return date if date > today else date.replace(year=today.year + 1)

        for title, date, (a, b), all_day, desc in (
                ("Remembrance Day", upcoming(11, 11), (0, 1440), True, "Closed for Remembrance Day."),
                ("Christmas Eve, open until 3 PM", upcoming(12, 24), (900, 1680), False, "Closing at 3 PM. Merry Christmas!"),
                ("Christmas Day", upcoming(12, 25), (0, 1440), True, "Closed for Christmas. Merry everything!"),
                ("Boxing Day", upcoming(12, 26), (0, 1440), True, "Closed for Boxing Day.")):
            Event.objects.update_or_create(title=title, defaults={"kind": Event.CLOSED, "start_date": date, "end_date": date, "opens": a,
                                                                   "closes": b, "all_day": all_day, "description": desc, "banner_days": 14,
                                                                   **everything})

        def weekday(w, ahead):
            """The first `w` (0 = Monday) at least `ahead` days from today."""
            day = today + datetime.timedelta(days=ahead)
            return day + datetime.timedelta(days=(w - day.weekday()) % 7)

        halloween = datetime.date(today.year, 10, 31)
        if halloween < today:
            halloween = halloween.replace(year=today.year + 1)
        O, C = Event.OPEN, Event.CLOSED
        for title, kind, day, (a, b), extra, desc in (
                ("Harvest market brunch", O, weekday(6, 5), (480, 540), {"dine_in": True, "pickup": True},
                 "We’re opening early on Sunday for the Main Street harvest market. Local cider and live fiddle."),
                ("Trivia night", O, weekday(1, 8), (1140, 1320), {"dine_in": True, "pickup": False, "banner_days": 3},
                 "Teams of up to six. Winners eat free."),
                ("Patio opening", O, weekday(6, 7), (900, 1200), {"dine_in": True, "pickup": True}, "First warm Sunday on the patio. Live music from 3."),
                ("Staff training day", C, weekday(0, 10), (0, 1440), {"all_day": True, **everything},
                 "We’re closed for a day of training. See you Tuesday!"),
                ("Halloween, closing early", C, halloween, (1080, 1500), {"banner_days": 5, **everything},
                 "Closing at 6 PM so the team can go trick-or-treating."),
                ("Short-staffed, no delivery", C, weekday(3, 3), (840, 1140), {"dine_in": False, "pickup": False, "delivery": True, "banner_days": 1},
                 "Pickup and dine-in as usual. Delivery is off for the afternoon.")):
            Event.objects.update_or_create(title=title, defaults={"kind": kind, "start_date": day, "end_date": day, "opens": a, "closes": b,
                                                                   "description": desc, **extra})

    def seed_store(self):
        store = StoreSettings.load()
        store.name = "Corner Kitchen"
        store.address, store.city, store.phone = "1200 Main St", "Vancouver, BC V6A 2T9", "604-555-0100"
        store.draft = store.details()
        store.save()
        # The online store's theme: its defaults, drawing the dishes (the demo's photos are placeholders)
        StoreTheme.objects.update_or_create(name="neighbourhood", defaults={"live": {"pictures": "drawings"}, "draft": {"pictures": "drawings"}})
        for title, tagline, color in [("Lunch combos", "A main, a side and a drink from $18.95", "#1f4a3a"),
                                      ("Gift cards", "Give someone lunch", "#14261f")]:
            promo, created = PromoContent.objects.get_or_create(title=title, defaults={
                "tagline": tagline, "call_to_action": "Order now", "color": color, "accent": "#ffffff", "link": "/en/menu"})
            if created:
                promo.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, color), save=True)

    def seed_services(self):
        """Services (staff, rentals, the venue, delivery, classes, extras) and a few weeks of bookings for them."""
        S = Service

        def service(title, category, pricing, price, description, minimum=0, booked=0, **fields):
            obj = S.objects.create(title=title, category=category, pricing=pricing, price=Decimal(str(price)),
                                   description=description, minimum=minimum, details={**S.DETAILS[category], **fields.pop("details", {})}, **fields)
            obj.booked = booked
            return obj

        services = [
            service("Event server", S.STAFF, S.HOUR, 38, "A uniformed server for passed canapés, plated service or buffet upkeep.",
                    minimum=4, details={"staff": 6, "gratuity": 15}, booked=9),
            service("Bartender", S.STAFF, S.HOUR, 45, "Certified bartender. Bar tools included.", minimum=4,
                    details={"staff": 3, "gratuity": 15}, booked=5),
            service("Chef on site", S.STAFF, S.HOUR, 85, "One of our chefs cooks live at your event: grill station or carving board.",
                    minimum=3, details={"staff": 2}, needs_approval=True, booked=2),
            service("Chafing dish set", S.EQUIPMENT, S.DAY, 18, "Full-size stainless chafer with fuel for 4 hours. Keeps hot food hot on a buffet.",
                    minimum=1, details={"owned": 12}, deposit=True, deposit_amount=Decimal("40"), booked=14),
            service("Coffee urn, 100 cups", S.EQUIPMENT, S.DAY, 45, "Commercial percolator urn with cups, lids and stir sticks for 100.",
                    minimum=1, details={"owned": 3}, deposit=True, deposit_amount=Decimal("75"), booked=4),
            service("Mobile flat-top grill", S.EQUIPMENT, S.DAY, 220, "Propane flat-top for outdoor events. Tank and safety kit included.",
                    minimum=1, details={"owned": 1}, deposit=True, deposit_amount=Decimal("500"), deposit_per=S.PER_BOOKING,
                    needs_approval=True, lead_days=7, cancellation=S.STRICT, booked=1),
            service("Folding tables and chairs", S.EQUIPMENT, S.DAY, 24, "Six-foot table with six chairs. Linens available as an extra.",
                    minimum=2, details={"owned": 20}, booked=6),
            service("Full restaurant buyout", S.VENUE, S.FLAT, 1800, "The whole dining room and patio for your private event, evenings only.",
                    details={"seated": 60, "standing": 90, "min_spend": 3500, "hours": 4},
                    includes=["Tables and chairs", "House sound system", "Dedicated manager", "Cleanup"],
                    needs_approval=True, lead_days=21, cancellation=S.STRICT, at_counter=False, booked=2),
            service("Private back room", S.VENUE, S.HOUR, 75, "Seats 18 at one long table. Ideal for birthdays and team lunches.",
                    minimum=2, details={"seated": 18, "standing": 25, "min_spend": 600}, includes=["Tables and chairs", "TV for slides"],
                    lead_days=3, booked=7),
            service("Catering delivery", S.DELIVERY, S.FLAT, 35, "Delivered hot within 10 km, set out on your table, ready to serve.",
                    details={"radius": 10, "per_km": 2}, booked=22),
            service("Setup and teardown", S.DELIVERY, S.HOUR, 55, "Our crew sets up the buffet, then packs everything away after.",
                    minimum=2, details={"radius": 25}, booked=8),
            service("Pasta making class", S.EXPERIENCE, S.PERSON, 75, "Two hours in our kitchen learning dough, fillings and shaping. Take home a dozen.",
                    minimum=6, details={"capacity": 12, "minutes": 120}, lead_days=5, booked=3),
            service("Chef’s tasting menu", S.EXPERIENCE, S.PERSON, 95, "Seven courses at the chef’s counter with optional drink pairing.",
                    minimum=2, details={"capacity": 8, "minutes": 150}, needs_approval=True, lead_days=3, active=False, booked=4),
            service("Custom celebration cake", S.EXTRA, S.UNIT, 65, "Two-layer cake with a message. Choose chocolate, lemon or carrot.",
                    minimum=1, lead_days=4, booked=5),
            service("Printed event menus", S.EXTRA, S.UNIT, 2.5, "Personalised menus printed on card for each place setting.",
                    minimum=10, lead_days=3, at_counter=False, booked=2),
        ]
        S.objects.create(title="Photo booth", category=S.EXTRA, pricing=S.FLAT, price=Decimal("150"), active=False, archived=True,
                         description="Retired: the booth went back to the rental company.")

        who = ["Riverside Tech meetup", "Sarah and Dev’s wedding", "Grandview Elementary PAC", "Hollis Design team lunch",
               "Ortiz family reunion", "Maple Street Book Club", "Kim retirement party", "Northside Running Club"]
        today = timezone.localdate()
        now = timezone.now()
        for obj in services:
            # The last 30 days' bookings (the list's "booked" count), some still to come, and a couple from before
            for k in range(obj.booked + 2):
                recent = k < obj.booked
                created = now - datetime.timedelta(days=random.randint(1, 29) if recent else random.randint(35, 80))
                date = today + datetime.timedelta(days=3 + k * 5 + len(obj.title) % 4) if k < 3 and recent else (created + datetime.timedelta(days=random.randint(2, 20))).date()
                amount = 1 if obj.pricing == S.FLAT else max(obj.minimum, 1) + (2 * k if obj.pricing == S.HOUR else k % 3)
                count = (k % 3) + (2 if obj.category == S.EQUIPMENT else 1) if obj.counts_units else 1
                quote = obj.quote(amount, count)
                status = Booking.PENDING if obj.needs_approval and k == 0 and date >= today else Booking.CONFIRMED
                Booking.objects.create(service=obj, name=who[(k + len(obj.title)) % len(who)], date=date, amount=amount, count=count,
                                       status=status, channel=random.choice([Booking.CATERING, Booking.CATERING, Booking.ONLINE, Booking.COUNTER]),
                                       total=quote["total"], deposit=quote["deposit"], created_at=created,
                                       notes="Evening event" if obj.pricing == S.FLAT else "")
        # One that was declined, so the counts skip it
        Booking.objects.create(service=services[0], name="Late request", date=today + datetime.timedelta(days=1), amount=4,
                               status=Booking.DECLINED, total=Decimal("152.00"), created_at=now - datetime.timedelta(days=2))

    def seed_team(self):
        """The schedule: weekly pars (and a holiday-rush seasonal par), this week's shifts (mostly published, a couple
        of changes waiting, open shifts to fill), Dana's availability and Priya's approved day off."""
        from team.models import Availability, ParSet, Shift, TimeOff
        from team.schedule import snapshot, weekly
        from users.models import Role
        roles = {r.name: str(r.id) for r in Role.objects.all()}
        T = lambda h, m=0: h * 60 + m  # noqa: E731
        usual = {"Manager": [[T(8), T(16, 30), 1]], "Cook": [[T(7), T(21), 1], [T(17), T(19), 2]], "Barista": [[T(7), T(14, 30), 1]],
                 "Cashier": [[T(15), T(21), 1]], "Server": [[T(11), T(14), 1]], "Dishwasher": [[T(17), T(21, 30), 1]],
                 "Delivery": [[T(11), T(14), 1], [T(17), T(21), 1]]}
        busy = {**usual, "Cook": [[T(7), T(21), 1], [T(11), T(14), 2], [T(17), T(20), 2]], "Server": [[T(11), T(14), 1], [T(17), T(21), 2]]}
        pars = weekly()
        pars.pars = {str(d): {roles[name]: wins for name, wins in (busy if d >= 4 else usual).items() if name in roles} for d in range(7)}
        pars.save()
        year = timezone.localdate().year
        ParSet.objects.get_or_create(name="Holiday rush", defaults={
            "kind": ParSet.SEASON, "mode": ParSet.ADD, "start": datetime.date(year, 12, 1), "end": datetime.date(year, 12, 31), "days": [4, 5, 6],
            "pars": {str(d): {roles["Cook"]: [[T(17), T(20), 1]], roles["Cashier"]: [[T(11), T(15), 1]]} for d in (4, 5, 6)}})
        people = {u.username: u for u in User.objects.filter(username__in=["admin", "cashier", "priya", "courier"])}
        role = {name: Role.objects.get(name=name) for name in roles}
        today = timezone.localdate()
        monday = today - datetime.timedelta(days=today.weekday())
        plan = [("admin", "Manager", T(8), T(16, 30)), ("cashier", "Barista", T(6, 30), T(14, 30)), ("priya", "Cashier", T(15), T(21)),
                ("courier", "Delivery", T(11), T(14)), ("courier", "Delivery", T(17), T(21)), (None, "Cook", T(7), T(15))]
        off = {"admin": [5, 6], "cashier": [2, 3], "priya": [0], "courier": [1]}
        for d in range(7):
            day = monday + datetime.timedelta(days=d)
            for who, rname, a, b in plan:
                if who and d in off.get(who, []):
                    continue
                shift = Shift(user=people.get(who), role=role[rname], date=day, start=a, end=b, break_minutes=30 if b - a >= 360 else 0)
                shift.published, shift.published_as = True, snapshot(shift)
                shift.save()
            if d == 4:
                Shift.objects.create(user=None, role=role["Server"], date=day, start=T(17), end=T(21))
        tuesday = monday + datetime.timedelta(days=1)
        Shift.objects.create(user=people["cashier"], role=role["Cashier"], date=tuesday, start=T(15), end=T(17),
                             note="Covering the register until the evening cashier is in")  # not published yet
        Availability.objects.create(user=people["cashier"], status=Availability.APPROVED, effective=monday - datetime.timedelta(days=30),
                                    reason="Classes in the evenings.", decided_at=timezone.now(), decided_by=people["admin"],
                                    windows={str(d): [[T(6), T(16)]] for d in range(7)})
        saturday = monday + datetime.timedelta(days=5)
        TimeOff.objects.create(user=people["priya"], first_day=saturday, last_day=saturday, category="Personal", status=TimeOff.APPROVED,
                               reason="Family birthday.", decided_at=timezone.now(), decided_by=people["admin"])
        # Requests waiting on the manager (Availability requests): time off over shifts Dana already has, an evening
        # appointment, the courier dropping weekends; and one declined last week
        ago = lambda days: timezone.now() - datetime.timedelta(days=days)  # noqa: E731
        next_week = monday + datetime.timedelta(days=7)
        TimeOff.objects.create(user=people["cashier"], first_day=next_week + datetime.timedelta(days=4), last_day=next_week + datetime.timedelta(days=6),
                               category="Vacation", reason="My sister’s wedding in Calgary. Flying out Friday morning.", submitted_at=ago(2))
        for d in range(4, 7):
            day = next_week + datetime.timedelta(days=d)
            shift = Shift(user=people["cashier"], role=role["Barista"], date=day, start=T(6, 30), end=T(14, 30), break_minutes=30)
            shift.published, shift.published_as = True, snapshot(shift)
            shift.save()
        TimeOff.objects.create(user=people["priya"], first_day=today + datetime.timedelta(days=1), last_day=today + datetime.timedelta(days=1),
                               all_day=False, start=T(16), end=T(21, 30), category="Appointment",
                               reason="Appointment at 5, should be back for close if it runs short.", submitted_at=ago(1))
        Availability.objects.create(user=people["courier"], effective=next_week,
                                    reason="Starting a weekend course, so I can only do weekdays for a while.", submitted_at=ago(3),
                                    windows={str(d): ([[T(10), T(22)]] if d < 5 else []) for d in range(7)})
        TimeOff.objects.create(user=people["admin"], first_day=saturday, last_day=saturday, category="Personal", status=TimeOff.DECLINED,
                               reason="Would love the Saturday off.", submitted_at=ago(9), decided_at=ago(8), decided_by=people["priya"],
                               message="Sorry, it’s our busiest Saturday. Happy to give you Sunday instead.")

    def seed_audio(self):
        """Audio channels for the screens, and a small library to play on them: short generated tones standing in for
        songs (real ones are uploaded in the admin's Library), with their tags, in two playlists."""
        import math
        import struct
        import wave

        from audio import library
        from audio.models import AudioChannel, Track
        for name, kind, notes in (("Order calls", "voice", "Calls out names when orders are ready."),
                                  ("Lobby music", "music", "Ceiling speakers in the dining room."),
                                  ("Patio music", "music", ""),
                                  ("Announcements", "voice", "Closing time and event messages.")):
            AudioChannel.objects.get_or_create(name=name, defaults={"kind": kind, "notes": notes})
        if Track.objects.exists():
            return

        def tone(freq, seconds=20, rate=8000):
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * freq * i / rate) * min(1, (seconds * rate - i) / 4000)))
                                       for i in range(seconds * rate)))
            return buffer.getvalue()

        songs = [("Lighthouse Keeper", "Lantern Coast", "Harbor Lights", 2023, "Indie pop", 392),
                 ("Sea Glass", "Lantern Coast", "Harbor Lights", 2023, "Indie pop", 440),
                 ("Velvet Hour", "Juno Hale", "Paper Moons", 2024, "Soul", 330),
                 ("Café Crème", "Odile Brun", "Rue Basse", 2020, "Jazz", 294),
                 ("Steam Buns", "Saltwater Radio", "Night Market", 2024, "Lo-fi", 523),
                 ("track_07", "", "", None, "", 262)]
        made = {}
        for title, artist, album, year, genre, freq in songs:
            upload = ContentFile(tone(freq), name=f"{title}.wav")
            made[title] = library.add_track(upload, {"title": title, "artist": artist, "album": album, "year": year or "",
                                                     "genre": genre, "duration": 20, "bitrate": 128})
        lunch = library.new_playlist([made[t].id for t in ("Steam Buns", "Sea Glass", "Lighthouse Keeper")])
        lunch.name, lunch.description = "Lunch rush", "Upbeat, keeps the line moving"
        lunch.save()
        evenings = library.new_playlist([made[t].id for t in ("Velvet Hour", "Café Crème")])
        evenings.name, evenings.description = "Slow evenings", "Low and warm after 6 PM"
        evenings.save()
        # The lobby plays Lunch rush; the patio has Slow evenings queued, paused
        from audio import music
        music.control(AudioChannel.objects.get(name="Lobby music"), "playnow", ids=[t.id for t in lunch.ordered_tracks()], source=lunch.name)
        patio = AudioChannel.objects.get(name="Patio music")
        music.control(patio, "playnow", ids=[t.id for t in evenings.ordered_tracks()], source=evenings.name)
        music.control(patio, "pause")

    def seed_screens(self):
        # The order board comes with migrations (screens/0002): give it pop-ups (an order-ready call-out, staff
        # announcements, a reminder before events, menu changes) and audio (order calls over the lobby music)
        from audio.models import AudioChannel
        calls, lobby = AudioChannel.objects.get(name="Order calls"), AudioChannel.objects.get(name="Lobby music")
        Screen.objects.filter(slug="order-board").update(popups=[
            {"widget": "order_ready", "params": {"channel": "calls"}}, {"widget": "announcement", "params": {"channel": "calls"}},
            {"widget": "event_reminder", "params": {"before": 30}}, {"widget": "menu_change", "params": {}}], audio=[
            {"id": "calls", "widget": "voice_channel", "params": {"channel": calls.id}},
            {"id": "lobby", "widget": "music_channel", "params": {"channel": lobby.id, "keep": 20}}])
        # and a portrait one for a vertical TV
        Screen.objects.get_or_create(slug="pickup-portrait", defaults={
            "name": "Pickup (portrait)", "public": True, "width": 1080, "height": 1920, "padding": 32,
            "popups": [{"widget": "order_ready", "params": {"style": "banner"}}],
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

        # A menu board for a vertical TV beside the register: the Lunch menu on the Showcase template, with sessions so
        # each register's display shows its own order summary while it takes payment
        board, _ = Screen.objects.get_or_create(slug="menu-board", defaults={
            "name": "Menu board", "public": True, "width": 1080, "height": 1920, "padding": 0, "background": "#16110E",
            "layout": {"widget": "menu_board", "params": {"menu": self.lunch_menu.id}}, "sessions": True,
            "popups": [{"widget": "order_summary", "params": {}}],
        })
        ScreenSession.objects.get_or_create(screen=board, name="Register 1 display")

    def seed_time_clock(self):
        """Time clock punches for the published shifts so far: a few minutes either side, a half-hour break on long
        ones; whoever's shift is on now is still clocked in."""
        from team.models import Shift, TimeBreak, TimeEntry
        now = timezone.now()
        today = timezone.localdate()
        for shift in Shift.objects.filter(published=True, deleted=False, user__isnull=False, date__lte=today,
                                          date__gte=today - datetime.timedelta(days=6)):
            start = timezone.make_aware(datetime.datetime.combine(shift.date, datetime.time())) + datetime.timedelta(minutes=shift.start - random.randint(1, 6))
            end = timezone.make_aware(datetime.datetime.combine(shift.date, datetime.time())) + datetime.timedelta(minutes=shift.end + random.randint(0, 8))
            if start > now:
                continue
            entry = TimeEntry.objects.create(user=shift.user, shift=shift, clock_in=start, clock_out=end if end <= now else None)
            if shift.end - shift.start >= 360 and start + datetime.timedelta(hours=4) < now:
                lunch = start + datetime.timedelta(hours=4)
                TimeBreak.objects.create(entry=entry, start=lunch, end=lunch + datetime.timedelta(minutes=30) if lunch + datetime.timedelta(minutes=30) < now else None)

    def seed_staff_cards(self):
        """The admin's Staff cards: a daily meal balance, a staff discount and a manager's monthly comp, issued to the
        team, with Dana's lunch paid from her meal card and Priya's coffee at the staff discount."""
        meal = StaffCardType.objects.create(name="Shift meal", color="#12875A", kind=StaffCardType.BALANCE, amount=Decimal("12.00"),
                                            refill="day", leftover="reset", channels=["store", "pickup"], position=0)
        discount = StaffCardType.objects.create(name="Staff discount", color="#4E79B5", kind=StaffCardType.DISCOUNT, discount_kind="pct",
                                                discount_value=Decimal("30"), discount_cap=Decimal("20.00"), uses="any",
                                                channels=["store", "pickup", "delivery"], position=1)
        comp = StaffCardType.objects.create(name="Manager comp", color="#C2453B", kind=StaffCardType.BALANCE, amount=Decimal("75.00"),
                                            refill="month", refill_day="1", leftover="carry", cap=Decimal("150.00"), own_only=False,
                                            no_alcohol=False, channels=["store"], position=2)
        people = {u.username: u for u in User.objects.filter(username__in=["admin", "cashier", "priya", "courier"])}
        admin = people["admin"]
        cards = {}
        for t, usernames in ((meal, ["cashier", "priya", "courier"]), (discount, ["cashier", "priya", "courier"]), (comp, ["admin", "priya"])):
            for username in usernames:
                cards[(t.name, username)] = StaffCard.objects.create(type=t, user=people[username], number=StaffCard.new_number(),
                                                                    balance=t.amount if t.kind == t.BALANCE else 0, issued_by=admin)
        d = {dish.title: dish for dish in Dish.objects.filter(title__in=["Iced latte", "Almond croissant", "Flat white"])}

        def line(title):
            return {"type": "dish", "id": d[title].id, "qty": 1, "cust": {}}
        # Dana's lunch from her meal card (it covers the lot), and Priya's coffee at the staff discount
        create_order(lines=[line("Iced latte"), line("Almond croissant")], channel="store", initial_status=OrderStation.STATUS_APPROVED,
                     staff_cards=[cards[("Shift meal", "cashier")]], cashier=self.cashier, device=self.device)
        priya = cards[("Staff discount", "priya")]
        total = price_cart([line("Flat white")], staff_cards=[priya]).total
        create_order(lines=[line("Flat white")], channel="store", initial_status=OrderStation.STATUS_APPROVED, staff_cards=[priya],
                     payments=[{"method": "card", "amount": str(total)}], cashier=self.cashier, device=self.device)
        # Priya's comp went on a supplier lunch earlier this month
        spent = cards[("Manager comp", "priya")]
        StaffCardUse.objects.create(card=spent, kind=StaffCardUse.SPEND, amount=Decimal("42.60"), by=people["priya"],
                                    at=timezone.now() - datetime.timedelta(days=3))
        StaffCard.objects.filter(pk=spent.pk).update(balance=spent.balance - Decimal("42.60"), last_used=timezone.now() - datetime.timedelta(days=3))

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
                total = price_cart(lines, order_discount=order_discount, to_go=bool(fields.get("to_go_order")), can_manual=True).total
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

        def on_side(title, counts):
            """A dish's condiments: {component title: count}."""
            extras = Extra.objects.filter(dish=d[title], component__title__in=counts).select_related("component")
            return {"condiments": {str(e.id): counts[e.component.title] for e in extras}}

        carts = [
            lambda: [dish("Smash burger"), dish("Fresh lemonade")],
            lambda: [lunch()],
            lambda: [lunch("Matcha"), dish("Cookie", 2)],
            lambda: [dish("Iced latte", 2), dish("Cardamom bun", 2)],
            lambda: [kids(), dish("Flat white")],
            lambda: [dish("Halloumi grain bowl"), dish("Ginger spritz")],
            lambda: [dish("Crispy chicken sandwich"), dish("Charred corn")],
            lambda: [dish("Smash burger", cust=on_side("Smash burger", {"Ketchup packet": 4})),
                     dish("Crispy chicken sandwich", cust=on_side("Crispy chicken sandwich", {"Chipotle mayo": 2}))],
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
        comped = order([dish("Iced latte"), dish("Almond croissant")], "store", OrderStation.STATUS_APPROVED, 2, to_go_order=True,
                       order_discount={"manual": {"kind": "pct", "value": "10"}}, cashier=self.cashier)
        # Dana can't type in discounts herself (Front of house): Priya signed this one off with her PIN
        comped.discounts.update(approved_by=User.objects.get(username="priya"))
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
