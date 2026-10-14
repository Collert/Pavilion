"""
Fill an empty database with demo data for local development and testing.

    python manage.py seed_demo            # refuses if there are already orders or dishes
    python manage.py seed_demo --force    # seed anyway (adds to what's there)

Creates users (admin/admin, cashier/cashier, courier/courier, customer/customer; register PINs: Alex 0000,
Dana 1234, Priya 2468), stations, ingredients, components with options, dishes with adjustable parts, add-ons
and condiments, combos, an active and an inactive menu, offers and coupons, today's business-day event, promo
banners, gift card presets and cards, a registered device, past cash drawers, and orders in every state across
the last few days.

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
from django.utils import timezone

from deliveries.models import CourierShift, Delivery
from events.models import Event
from gift_cards.models import CardPreset, GiftCard
from inventory.models import Packaging, Recipe
from online_store.models import PromoContent, RejectedOrder
from pos_server.models import (CashDrawerSession, CashMovement, Combo, ComboSlot, ComboSlotOption, Component,
                               ComponentIngredient, Discount, Dish, DishComponent, EligibleDevice, Extra, Ingredient,
                               Menu, Option, OptionGroup, Order, OrderStation, Station)
from pos_server.orders import create_order
from pos_server.pricing import price_cart
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
            self.seed_discounts()
            self.seed_events()
            self.seed_store()
            self.seed_gift_cards()
            self.device, _ = EligibleDevice.objects.get_or_create(token=DEMO_DEVICE_TOKEN, defaults={"name": "Register 1"})
            Packaging.objects.get_or_create(name="Takeout container", defaults={"quantity": 200, "cost": Decimal("0.35")})
            self.seed_orders()
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
            # code, name, icon, menu heading
            ("kitchen", "Grill", "outdoor_grill", "Grill"),
            ("bar", "Bar", "local_cafe", "Bar"),
            ("bakery", "Bakery", "bakery_dining", "Bakery"),
        ]
        self.stations = {}
        for position, (code, name, icon, heading) in enumerate(specs):
            station, _ = Station.objects.update_or_create(code=code, defaults={
                "name": name, "icon": icon, "menu_heading": heading, "position": position})
            self.stations[code] = station
        Station.objects.filter(code="gng").update(position=len(specs))

    def seed_inventory(self):
        ingredient_specs = [
            # title, unit, inventory, allergen, unlimited, cost
            ("Ground beef", "g", 40000, None, False, "0.015"),
            ("Flour", "g", 100000, "Wheat and triticale", False, "0.002"),
            ("Milk", "ml", 60000, "Milk", False, "0.002"),
            ("Oats", "g", 20000, None, False, "0.004"),
            ("Almonds", "g", 10000, "Tree Nuts", False, "0.02"),
            ("Cheddar", "g", 8000, "Milk", False, "0.02"),
            ("Eggs", "ea", 600, "Eggs", False, "0.30"),
            ("Potatoes", "g", 150000, None, False, "0.003"),
            ("Chicken", "g", 30000, None, False, "0.012"),
            ("Pork shoulder", "g", 20000, None, False, "0.01"),
            ("Vegetables", "g", 60000, None, False, "0.004"),
            ("Coffee beans", "g", 20000, None, False, "0.03"),
            ("Lemons", "ea", 300, None, False, "0.40"),
            ("Sugar", "g", 20000, None, False, "0.002"),
            ("Butter", "g", 10000, "Milk", False, "0.01"),
            ("Sesame seeds", "g", 2000, "Sesame seeds", False, "0.01"),
            ("Water", "ml", 0, None, True, None),
        ]
        self.ingredients = {}
        for title, unit, inventory, allergen, unlimited, cost in ingredient_specs:
            self.ingredients[title], _ = Ingredient.objects.get_or_create(title=title, defaults={
                "unit_of_measurement": unit, "inventory": inventory, "allergen": allergen,
                "unlimited": unlimited, "cost": Decimal(cost) if cost else None})

        recipe, _ = Recipe.objects.get_or_create(
            markdown_text="|||\n|-|-|\n|Step 1:|Roll 120 g of beef into a ball.|\n|Step 2:|Smash it on a hot griddle, season, flip after 2 minutes.|\n",
            defaults={"original_yield": 1})

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
            ("Smash sauce", "ml", "food", "craft", 3000, "0", [("Eggs", 0.01)]),
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
                "extra_price": Decimal(extra_price), "recipe": recipe if title == "Beef patty" else None})
            if created:
                for ingredient, qty in parts:
                    ComponentIngredient.objects.create(component=component, ingredient=self.ingredients[ingredient], quantity=qty)
            self.components[title] = component

        c = self.components

        def group(title, options, component=None, dish=None, note="", default=None):
            """options: "Name", ("Name", "price") or ("Name", "price", {other Option fields}). The first is standard."""
            obj = OptionGroup.objects.create(title=title, component=component, dish=dish, note=note)
            specs = [(o, "0", {}) if isinstance(o, str) else (o[0], o[1], o[2] if len(o) > 2 else {}) for o in options]
            default = default or specs[0][0]
            for position, (name, price, more) in enumerate(specs):
                Option.objects.create(group=obj, title=name, price=Decimal(price), position=position,
                                      is_default=name == default, **more)
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
            key = "dish" if isinstance(owner, Dish) else "combo"
            for position, (name, price) in enumerate(addons):
                component = c[name]
                Extra.objects.create(**{key: owner}, component=component, kind=Extra.ADDON, price=Decimal(price), position=position)
            for position, label in enumerate(condiments):
                Extra.objects.create(**{key: owner}, component=c[self.side_component[label]], kind=Extra.CONDIMENT,
                                     title=label, position=position)

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

        # Combos
        def combo(title, price, slots, description=""):
            obj, created = Combo.objects.get_or_create(title=title, defaults={"price": Decimal(price), "description": description})
            if created:
                obj.image.save(f"{title.lower().replace(' ', '_')}.png", placeholder_image(title, "#14261f"), save=True)
                for position, (slot_title, options, settings_) in enumerate(slots):
                    slot = ComboSlot.objects.create(combo=obj, title=slot_title, position=position, **settings_)
                    for option_position, (option_dish, delta) in enumerate(options):
                        ComboSlotOption.objects.create(slot=slot, dish=option_dish, price=Decimal(delta),
                                                       is_default=option_position == 0, position=option_position)
            return obj

        lunch = combo("Lunch combo", "18.95", [
            ("Main", [(smash, "0"), (chicken, "0"), (melt, "0"), (bowl, "0.50"), (pork, "2.00")], {}),
            ("Side", [(corn, "0"), (fries, "0"), (salad, "0")], {"allow_none": True, "none_price": Decimal("-1.50")}),
            ("Drink", [(latte, "0"), (flat_white, "0"), (lemonade, "0"), (spritz, "0.50"), (matcha, "1.00"), (cold_brew, "0")],
             {"ask_on_add": True, "allow_none": True, "none_price": Decimal("-1.50")}),
        ], "A main, a side and a drink.")
        kids = combo("Kids meal", "9.95", [
            ("Main", [(kids_burger, "0"), (bites, "0")], {}),
            ("Side", [(corn, "0"), (fries, "0"), (salad, "0")],
             {"ask_on_add": True, "allow_none": True, "none_price": Decimal("-1.00")}),
            ("Drink", [(lemonade, "0"), (spritz, "0.50"), (latte, "1.00")], {"allow_none": True, "none_price": Decimal("-1.50")}),
        ], "A small main, a side and a drink.")
        extras(kids, condiments=["Extra napkins"])

        self.dishes = {d.title: d for d in Dish.objects.all()}
        self.combos = {"Lunch combo": lunch, "Kids meal": kids}

        def menu(title, active, dishes, combos, color):
            obj, created = Menu.objects.get_or_create(title=title, defaults={
                "is_active": active, "background_color": color, "accent_1": "#e8ede9", "accent_2": "#f4a93b", "accent_3": "#14261f"})
            if created:
                obj.header_image.save("header.png", placeholder_image(title, color, (1200, 300)), save=False)
                obj.footer_image.save("footer.png", placeholder_image("Thank you!", color, (1200, 200)), save=True)
                obj.dishes.set(dishes)
                obj.combos.set(combos)
            return obj

        visible = [smash, chicken, melt, pork, bowl, fries, corn, latte, flat_white, lemonade, spritz, matcha, cold_brew,
                   bun, croissant, brownie, tart, cookie, loaf]
        menu("Lunch", True, visible, [lunch, kids], "#1f4a3a")
        menu("Breakfast", False, [latte, flat_white, matcha, bun, croissant, cookie], [], "#b5791f")

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
        for title, tagline, color in [("Lunch combos", "A main, a side and a drink from $18.95", "#1f4a3a"),
                                      ("Gift cards", "Give someone lunch", "#14261f")]:
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
        d, combos = self.dishes, self.combos
        names = ["Olena", "Sam", "Mark", "Iris", "Peter", "Sofia", "Andre", None, None]
        now = timezone.now()

        def dish(title, qty=1, cust=None):
            return {"type": "dish", "id": d[title].id, "qty": qty, "cust": cust or {}}

        def lunch(drink="Iced latte", qty=1, **slots):
            combo = combos["Lunch combo"]
            chosen = {str(s.id): {"dish": d[drink].id} for s in combo.slots.all() if s.title == "Drink"}
            chosen.update(slots)
            return {"type": "combo", "id": combo.id, "qty": qty, "slots": chosen}

        def kids(side="Charred corn"):
            combo = combos["Kids meal"]
            return {"type": "combo", "id": combo.id, "qty": 1,
                    "slots": {str(s.id): {"dish": d[side].id} for s in combo.slots.all() if s.title == "Side"}}

        def option(dish_title, group_title, option_title):
            """{"<group key>": option id} for a dish's own option group."""
            group = OptionGroup.objects.get(dish=d[dish_title], title=group_title)
            return {str(group.id): group.options.get(title=option_title).id}

        burger_parts = {dc.component.title: dc.id for dc in d["Smash burger"].dishcomponent_set.select_related("component")}
        patty_group = OptionGroup.objects.get(component__title="Beef patty")
        well_done = {f"{patty_group.id}.{burger_parts['Beef patty']}": patty_group.options.get(title="Well done").id}

        def order(lines, channel, status, minutes_ago, method="card", drawer=None, order_discount=None, **fields):
            payments = ()
            if channel == "store":
                total = price_cart(lines, order_discount=order_discount, can_manual=True).total
                payments = [{"method": method, "amount": str(total), "tendered": str(total)}]
            created = create_order(
                lines=lines, channel=channel, initial_status=status, payments=payments, drawer=drawer,
                order_discount=order_discount, can_manual_discount=True,
                name=fields.pop("name", random.choice(names)), **fields)
            when = now - datetime.timedelta(minutes=minutes_ago)
            Order.objects.filter(pk=created.pk).update(timestamp=when, start_time=when)
            created.payments.update(timestamp=when)
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
                Order.objects.filter(pk=past.pk).update(picked_up=True, prep_time=datetime.timedelta(minutes=random.randint(3, 15)))
            CashMovement.objects.create(drawer=drawer, amount=Decimal("-12.50"), reason="Bought ice", user=self.cashier,
                                        timestamp=opened + datetime.timedelta(hours=1))
            cash = sum((p.amount for p in drawer.payments.filter(method="cash")), Decimal("0"))
            expected = drawer.opening_amount + cash - Decimal("12.50")
            drawer.expected_amount = expected
            drawer.counted_amount = expected if days_ago != 2 else expected - Decimal("4.25")
            drawer.closed_at = opened + datetime.timedelta(hours=6)
            drawer.closed_by = self.cashier
            drawer.save()

        # Today, on the screens
        order([dish("Smash burger", cust={"parts": {str(burger_parts["Onion"]): 0}, "options": well_done,
                                          "addons": [Extra.objects.get(dish=d["Smash burger"], component__title="Avocado").id]}),
               dish("Fries", cust={"options": option("Fries", "Size", "Large")}) if d["Fries"].available else dish("Charred corn")],
              "store", OrderStation.STATUS_APPROVED, 6, name="Olena", cashier=self.cashier)
        order([lunch("Flat white")], "store", OrderStation.STATUS_APPROVED, 4, name="Sam", special_instructions="Birthday",
              order_discount={"offer": Discount.objects.get(name="Student").id}, cashier=self.cashier)
        order([dish("Iced latte"), dish("Almond croissant")], "store", OrderStation.STATUS_APPROVED, 2, to_go_order=True,
              cashier=self.cashier)
        ready = order([dish("Halloumi grain bowl"), dish("Fresh lemonade")], "store", OrderStation.STATUS_APPROVED, 9, name="Mark")
        ready.set_station_status(["kitchen", "bar"], OrderStation.STATUS_DONE)
        order([kids("Green salad")], "web", OrderStation.STATUS_PENDING, 1, name="Iris", phone=5555550123,
              gift_card_payments=[(self.gift_cards[0].number, "5.00")], verify_payment=lambda remaining: None)
        delivery = order([dish("Pulled pork plate"), dish("Charred corn")], "delivery", OrderStation.STATUS_PENDING, 3, name="Sofia",
                         phone=5555550188, to_go_order=True, verify_payment=lambda remaining: None)
        Delivery.objects.create(order=delivery, destination="200 Oak Ave, Springfield", phone=5555550188,
                                instructions="Leave the package at the door.")
        order([dish("Cookie", 3)], "store", OrderStation.STATUS_APPROVED, 1, name="Peter")
        RejectedOrder.objects.create(order_id=Order.objects.latest("id").id + 1000, reason="Demo: out of containers.",
                                     timestamp=now - datetime.timedelta(hours=1))
