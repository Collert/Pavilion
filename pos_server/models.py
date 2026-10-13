from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.timezone import localtime, now
from gift_cards.models import GiftCard
import json
import uuid
from payments.models import PaymentAuthorization
from django.core.cache import cache
from django.conf import settings

# Create your models here.
    
class Menu(models.Model):
    """
    Model representing a Menu.
    Attributes:
        title (CharField): The title of the menu, with a maximum length of 140 characters.
        is_active (BooleanField): A boolean indicating whether the menu is active. Defaults to False.
        header_image (ImageField): An optional image field for the header image, uploaded to 'files/menu_decorations'.
        footer_image (ImageField): An optional image field for the footer image, uploaded to 'files/menu_decorations'.
        background_color (CharField): The background color of the menu, with a default value of "#ffffff".
        accent_1 (CharField): The first accent color of the menu, with a default value of "#ffffff".
        accent_2 (CharField): The second accent color of the menu, with a default value of "#ffffff".
        accent_3 (CharField): The third accent color of the menu, with a default value of "#ffffff".
    Methods:
        __str__(): Returns a string representation of the Menu instance.
    """
    title = models.CharField(max_length=140)
    is_active = models.BooleanField(default=False)
    header_image = models.ImageField(upload_to='files/menu_decorations', null=True)
    footer_image = models.ImageField(upload_to='files/menu_decorations', null=True)
    background_color = models.CharField(max_length=7, default="#ffffff")
    accent_1 = models.CharField(max_length=7, default="#ffffff")
    accent_2 = models.CharField(max_length=7, default="#ffffff")
    accent_3 = models.CharField(max_length=7, default="#ffffff")

    def __str__(self) -> str:
        return f"Menu: {self.title}"

    def save(self, *args, **kwargs):
        # Exactly one menu is meant to be active: activating this one deactivates the rest
        super().save(*args, **kwargs)
        if self.is_active:
            Menu.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def active(cls):
        """The active menu, or None if no menu is active."""
        return cls.objects.filter(is_active=True).first()

class Dish(models.Model):
    """
    Dish model representing a dish in the POS system.
    Attributes:
        title (CharField): The title of the dish.
        price (DecimalField): The price of the dish.
        image (ImageField): An optional image of the dish.
        description (TextField): An optional description of the dish.
        components (ManyToManyField): Components that make up the dish, related through DishComponent.
        menu (ManyToManyField): Menus that include the dish.
        station (ForeignKey): The Station that prepares the dish.
        in_stock (BooleanField): Indicates if the dish is in stock.
        force_in_stock (BooleanField): Indicates if the dish is forced to be in stock.
        recipe (ForeignKey): The recipe associated with the dish from the inventory.
        visible_in_menu (BooleanField): Indicates if the dish is visible in the menu.
    Methods:
        __str__(): Returns the title of the dish.
        check_if_only_choice_dish(): Checks whether the dish only consists of components that point to other dishes.
        serialize_with_options(): Serializes the dish with its options and components.
    """
    title = models.CharField(max_length=140)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    image = models.ImageField(upload_to='files/dish_images', null=True, blank=True)
    description = models.TextField(blank=True)
    components = models.ManyToManyField("Component", through='DishComponent')
    menu = models.ManyToManyField("Menu", related_name = "dishes")
    station = models.ForeignKey("Station", related_name="dishes", on_delete=models.PROTECT)
    in_stock = models.BooleanField(default=True)
    force_in_stock = models.BooleanField(default=False)
    recipe = models.ForeignKey("inventory.Recipe", related_name="dish", on_delete=models.DO_NOTHING, null=True, blank=True)
    visible_in_menu = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.title
    
    def check_if_only_choice_dish(self):
        """
        Checks whether the dish only consists of components that point to other dishes.

        Returns:
            bool: True if all components of the dish have child dishes, False otherwise.
        """
        if not self.components.all():
            return False
        for component in self.components.all():
            if not component.child_dishes.all():
                return False
        return True

    def serialize_with_options(self):
        """
        Serializes the dish object with its components and options.

        Returns:
            dict: A dictionary containing the serialized data of the dish, including:
                - fields (dict): Contains the following keys:
                    - title (str): The title of the dish.
                    - price (float): The price of the dish.
                    - image (str or None): The URL of the dish's image, or None if no image is available.
                    - description (str): The description of the dish.
                    - menu (int): The ID of the first menu associated with the dish.
                    - station (str): The station associated with the dish.
                    - in_stock (bool): Whether the dish is in stock.
                    - force_in_stock (bool): Whether the dish is forced to be in stock.
                    - choice_components (list): A list of dictionaries representing the components and their child dishes, each containing:
                        - parent (dict): Contains the title and ID of the parent component.
                        - children (list): A list of dictionaries representing the child dishes, each containing:
                            - title (str): The title of the child dish.
                            - id (int): The ID of the child dish.
                            - in_stock (bool): Whether the child dish is in stock.
                            - force_in_stock (bool): Whether the child dish is forced to be in stock.
                    - only_choices (bool): Whether the dish is an only choice dish.
                - model (str): The model name, which is "pos_server.dish".
                - id (int): The ID of the dish.
                - pk (int): The primary key of the dish (same as the ID).
        """
        choice_components = []
        for component in self.components.all():
            if component.child_dishes.all():
                choices = {
                    "parent":{
                        "title":component.title,
                        "id":component.id
                    },
                    "children":[]
                }
                for child in component.child_dishes.all():
                    choices["children"].append({
                        "title":child.title,
                        "id":child.id,
                        "in_stock":child.in_stock,
                        "force_in_stock":child.force_in_stock
                    })
                choice_components.append(choices)
        return {
            "fields":{
                "title":self.title,
                "price":float(self.price),  # JS does arithmetic on it
                "image":self.image.url if self.image else None,
                "description":self.description,
                "menu":self.menu.first().id if self.menu.exists() else None,
                "station":self.station.code,
                "in_stock":self.in_stock,
                "force_in_stock":self.force_in_stock,
                "choice_components":choice_components,
                "only_choices":self.check_if_only_choice_dish()
            },
            "model":"pos_server.dish",
            "id":self.id,
            "pk":self.id
        }

class Order(models.Model):
    """
    An order from the POS (channel "store"), the online store ("web") or for delivery ("delivery").

    Each station that has to make something for the order gets an OrderStation row holding its status
    (see OrderStation.STATUS_*). Stations without a row are not involved ("not required").

    `picked_up` defaults to True and is set False when any station is needed; the order is done once it
    is True. `total` is computed on the server when the order is created (see pos_server.orders).
    """
    order_channels = (
        ("store", "In-person"),
        ("web", "Online pickup"),
        ("delivery", "Delivery")
    )
    timestamp = models.DateTimeField(default=timezone.now)
    start_time = models.DateTimeField(default=timezone.now)
    prep_time = models.DurationField(null=True, blank=True)
    dishes = models.ManyToManyField(Dish, through="OrderDish")
    stations = models.ManyToManyField("Station", through="OrderStation", related_name="orders")
    name = models.CharField(null=True, max_length = 140, blank=True)
    picked_up = models.BooleanField(default=True)
    special_instructions = models.TextField(null=True, blank=True)
    to_go_order = models.BooleanField(default=False)
    total = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="After discount")
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    channel = models.CharField(max_length=10, choices=order_channels)
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    authorization = models.ForeignKey(PaymentAuthorization, on_delete=models.SET_NULL, null=True, blank=True)
    gift_cards = models.ManyToManyField(GiftCard, through="gift_cards.GiftCardAuthorization", blank=True)

    def save(self, *args, **kwargs):
        if self.pk and not self.prep_time and self.station_statuses.exists() and self.all_stations_done():
            self.prep_time = timezone.now() - self.timestamp
        super().save(*args, **kwargs)
        update_active_orders_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        update_active_orders_cache()
        return result

    def __str__(self) -> str:
        return f"Order {self.id}"

    def station_status_map(self) -> dict:
        """{station code: status} for every station involved. Uses prefetched rows when available."""
        return {row.station.code: row.status for row in self.station_statuses.all()}

    def statuses(self):
        return set(self.station_status_map().values())

    def all_stations_done(self) -> bool:
        return all(status in (OrderStation.STATUS_DONE, OrderStation.STATUS_REJECTED) for status in self.statuses())

    def set_station_status(self, station_codes, status, only_from=None) -> int:
        """
        Move the given stations to `status`. `only_from` limits which current statuses may change
        (e.g. only approve stations that are pending). Returns the number of stations changed.
        """
        from .signals import order_ready
        self.refresh_station_statuses()
        was_done = self.all_stations_done()
        rows = self.station_statuses.filter(station__code__in=station_codes)
        if only_from is not None:
            rows = rows.filter(status__in=only_from)
        changed = rows.update(status=status)
        if changed:
            self.refresh_station_statuses()
            self.save()
            if not was_done and self.all_stations_done():
                order_ready.send(sender=Order, order=self)
        return changed

    def refresh_station_statuses(self):
        """Drop prefetched OrderStation rows so the next read hits the database."""
        getattr(self, "_prefetched_objects_cache", {}).pop("station_statuses", None)

    def progress_status(self):
        """
        Customer-facing progress for online/delivery orders:
        1 scheduled, 2 not started, 3 in progress, 4 ready, 5 picked up, 6 delivered. None for POS orders.
        """
        if self.channel not in ("web", "delivery"):
            return None
        statuses = self.statuses()
        if self.channel == "delivery":
            delivery = self.delivery.first()
            if delivery and delivery.completed:
                return 6
        if self.picked_up:
            return 5
        if self.all_stations_done():
            return 4
        if OrderStation.STATUS_APPROVED in statuses:
            return 3
        if self.start_time <= timezone.now():
            return 2
        return 1

class OrderDish(models.Model):
    """
    OrderDish model represents the relationship between an order and a dish, 
    including the quantity of the dish in the order.
    Attributes:
        order (ForeignKey): A reference to the Order model. When the referenced order is deleted, 
                            the related OrderDish instances will also be deleted.
        dish (ForeignKey): A reference to the Dish model. When the referenced dish is deleted, 
                           the related OrderDish instances will also be deleted.
        quantity (IntegerField): The quantity of the dish in the order. Defaults to 1.
    Methods:
        __str__(): Returns a string representation of the OrderDish instance, 
                   showing the quantity of the dish and the order ID.
    """
    order = models.ForeignKey(Order, on_delete = models.CASCADE)
    dish = models.ForeignKey(Dish, on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)

    def __str__(self) -> str:
        return f"{self.quantity} X {self.dish.title} in order {self.order.id}"
    
class Component(models.Model):
    """
    Component model representing an item in the inventory.
    For more info, refer to: https://github.com/Collert/Pavilion/wiki/Inventory-Management-System
    Attributes:
        units (tuple): Choices for unit of measurement.
        food_types (tuple): Choices for type of food.
        crafting_options (tuple): Choices for crafting options.
        title (CharField): The title of the component.
        ingredients (ManyToManyField): Ingredients associated with the component.
        inventory (FloatField): The current inventory level of the component.
        unit_of_measurement (CharField): The unit of measurement for the component.
        type (CharField): The type of food (food or beverage).
        in_stock (BooleanField): Indicates if the component is in stock.
        crafting_option (CharField): The crafting option for the component.
        recipe (ForeignKey): The recipe associated with the component.
        child_dishes (ManyToManyField): Dishes that use this component.
    Methods:
        save(*args, **kwargs): Custom save method to update stock status and related dishes.
        __str__() -> str: String representation of the component.
    """
    units = (
        ("kg", "Kilogram"),
        ("g", "Gram"),
        ("l", "Liter"),
        ("ml", "Milliliter"),
        ("ea", "Each")
    )
    food_types = (
        ("food", "Food"),
        ("beverage", "Beverage")
    )
    crafting_options = (
        ("craft","Craftable"),
        ("buy","Purchased"),
        ("auto","Self-crafting")
    )
    title = models.CharField(max_length=140)
    ingredients = models.ManyToManyField("Ingredient", through='ComponentIngredient')
    inventory = models.FloatField(default = 0, null = True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)
    type = models.CharField(max_length=10, choices=food_types)
    in_stock = models.BooleanField(default=False)
    crafting_option = models.CharField(max_length=10, choices=crafting_options, default="craft")
    recipe = models.ForeignKey("inventory.Recipe", related_name="component", on_delete=models.DO_NOTHING, null=True, blank=True)
    child_dishes = models.ManyToManyField('Dish', symmetrical=False, blank=True, related_name='parent_component')

    def save(self, *args, force_update_stock=False, **kwargs):
        """
        Recalculate `in_stock` (unless force_update_stock=True, which keeps a value set by hand), then
        update the dishes that use this component: they go out of stock when it can't cover them, and come
        back when this component comes back into stock and every other component of the dish is available.
        Self-crafting ("auto") components are made on demand, and choice components are available while
        any of their choices is, so neither is limited by its own inventory.
        """
        was_in_stock = Component.objects.filter(pk=self.pk).values_list("in_stock", flat=True).first() if self.pk else None
        if not force_update_stock:
            if self.pk and self.child_dishes.exists():
                self.in_stock = self.child_dishes.filter(models.Q(in_stock=True) | models.Q(force_in_stock=True)).exists()
            else:
                self.in_stock = self.crafting_option == "auto" or (self.inventory or 0) > 0
        super().save(*args, **kwargs)
        restocked = was_in_stock is False and self.in_stock
        for dc in self.dishcomponent_set.select_related("dish"):
            dish = dc.dish
            if not dc.covers_one():
                if dish.in_stock and not dish.force_in_stock:
                    dish.in_stock = False
                    dish.save(update_fields=["in_stock"])
            elif restocked and not dish.in_stock and all(other.covers_one() for other in dish.dishcomponent_set.select_related("component")):
                dish.in_stock = True
                dish.force_in_stock = False
                dish.save(update_fields=["in_stock", "force_in_stock"])

    def __str__(self) -> str:
        return f"{self.title} ({self.unit_of_measurement.title()})"
    
class DishComponent(models.Model):
    """
    Represents the relationship between a Dish and its Component.
    Attributes:
        dish (ForeignKey): A reference to the Dish model.
        component (ForeignKey): A reference to the Component model.
        quantity (FloatField): The quantity of the component used in the dish.
    Methods:
        __str__(): Returns a string representation of the DishComponent instance.
    """
    dish = models.ForeignKey('Dish', on_delete=models.CASCADE)
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    quantity = models.FloatField()

    def __str__(self):
        return f"{self.quantity} x {self.component.title} in {self.dish.title}"

    def covers_one(self) -> bool:
        """Can the component supply one of this dish right now?"""
        component = self.component
        if not component.in_stock:
            return False
        if component.crafting_option == "auto" or component.child_dishes.exists():
            return True
        return (component.inventory or 0) >= self.quantity

# The next class is a part of dish customization. One day...

# class ModifiedComponent(models.Model):
#     operations = (
#         ("rem","Remove component"),
#         ("add","Add component"),
#         ("pick","Choose component")
#     )
#     order = models.ForeignKey(Order, on_delete = models.CASCADE, related_name="mods")
#     dish = models.ForeignKey(Dish, on_delete = models.CASCADE)
#     parent = models.ForeignKey(Component, on_delete = models.CASCADE)
#     component = models.ForeignKey(Component, on_delete = models.CASCADE, related_name="mods")
#     quantity = models.FloatField()
#     operation = models.CharField(max_length=5, choices=operations)

class Ingredient(models.Model):
    """
    Represents an ingredient in the inventory.
    For more info, refer to: https://github.com/Collert/Pavilion/wiki/Inventory-Management-System
    Attributes:
        units (tuple): Choices for unit of measurement.
        allergens (tuple): Choices for allergens.
        title (str): The name of the ingredient.
        inventory (int): The quantity of the ingredient in inventory.
        unit_of_measurement (str): The unit of measurement for the ingredient.
        allergen (str): The allergen associated with the ingredient.
        unlimited (bool): Indicates if the ingredient has unlimited supply.
        cost (DecimalField): The cost of the ingredient.
        condiment_price (DecimalField): The price of the ingredient if sold as a condiment.
    Methods:
        __str__(): Returns a string representation of the ingredient.
    """
    units = (
        ("kg", "Kilogram"),
        ("g", "Gram"),
        ("l", "Liter"),
        ("ml", "Milliliter"),
        ("ea", "Each")
    )
    allergens = (
        ("Eggs", "Eggs"),
        ("Milk", "Milk"),
        ("Mustard", "Mustard"),
        ("Peanuts", "Peanuts"),
        ("Fish", "Fish"),
        ("Sesame seeds", "Sesame seeds"),
        ("Soy", "Soy"),
        ("Sulphites", "Sulphites"),
        ("Tree Nuts", "Tree Nuts"),
        ("Wheat and triticale", "Wheat and triticale"),
        ("Crustaceans", "Crustaceans")
    )
    title = models.CharField(max_length=140)
    inventory = models.IntegerField(default = 0, null = True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)
    allergen = models.CharField(max_length=20, choices=allergens, blank=True, null=True, default=None)
    unlimited = models.BooleanField(default=False)
    cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True) 
    condiment_price = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True) # Price for this ingredient if sold as a condiment. Leave blank to treat as a free condiment or if not given as a condiment.

    def __str__(self) -> str:
        qty = self.inventory
        if qty is None:
            inventory = "Inventory not tracked"
        else:
            inventory = f"{qty} X in inventory"
        return f"{self.title} ({self.unit_of_measurement.title()})"
    
class ComponentIngredient(models.Model):
    """
    Represents the relationship between a component and an ingredient in the POS system.
    Attributes:
        component (ForeignKey): A reference to the Component model.
        ingredient (ForeignKey): A reference to the Ingredient model.
        quantity (FloatField): The quantity of the ingredient used in the component.
    Methods:
        __str__(): Returns a string representation of the ComponentIngredient instance.
    """
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    ingredient = models.ForeignKey('Ingredient', on_delete=models.CASCADE)
    quantity = models.FloatField()

    def __str__(self):
        return f"{self.quantity} x {self.ingredient.title} in {self.component.title}"
    
class EligibleDevice(models.Model):
    """
    Model representing an eligible device.
    Attributes:
        token (UUIDField): A unique identifier for the device, automatically generated.
        name (CharField): The name of the device, with a maximum length of 140 characters.
    Methods:
        __str__(): Returns the name of the device as its string representation.
    """
    token = models.UUIDField(default=uuid.uuid4)
    name = models.CharField(max_length=140)

    def __str__(self) -> str:
        return self.name
    
class Station(models.Model):
    """
    A place where dishes are prepared (kitchen, bar, ...). Each KDS screen picks the stations it covers
    with ?filter=<code>. Menus are grouped into sections by station, using `menu_heading`.
    """
    name = models.CharField(max_length=50, help_text="Shown on kitchen screens, e.g. 'Kitchen'")
    code = models.SlugField(max_length=20, unique=True)
    icon = models.CharField(max_length=20, choices=settings.AVAILABLE_ICONS)
    menu_heading = models.CharField(max_length=80, blank=True, help_text="Menu section title, e.g. 'Hot food'. Defaults to the name.")
    position = models.PositiveSmallIntegerField(default=0, help_text="Order of menu sections and screen filters")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name

    @property
    def heading(self):
        return self.menu_heading or self.name

    def as_dict(self):
        return {"code": self.code, "name": self.name, "icon": self.icon, "heading": self.heading}

class OrderStation(models.Model):
    """Status of one station's work on one order."""
    STATUS_PENDING = 0    # waiting for approval (online/delivery orders)
    STATUS_APPROVED = 1   # being made
    STATUS_DONE = 2
    STATUS_REJECTED = 3
    statuses = (
        (STATUS_PENDING, "Pending approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DONE, "Completed"),
        (STATUS_REJECTED, "Rejected"),
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="station_statuses")
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="order_statuses")
    status = models.PositiveSmallIntegerField(choices=statuses, default=STATUS_PENDING)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "station"], name="unique_order_station")]

    def __str__(self):
        return f"{self.station} for {self.order}: {self.get_status_display()}"

def active_orders_queryset():
    """Orders that should be on screens: started, and either still being made or not picked up yet."""
    return Order.objects.filter(
        models.Q(start_time__lte=now()) &
        (models.Q(station_statuses__status__in=[OrderStation.STATUS_PENDING, OrderStation.STATUS_APPROVED]) |
         models.Q(picked_up=False))
    ).distinct()

def update_active_orders_cache():
    """Cache the IDs of active orders under 'active_orders' (60 s)."""
    cache.set('active_orders', list(active_orders_queryset().values_list("id", flat=True)), timeout=60)
