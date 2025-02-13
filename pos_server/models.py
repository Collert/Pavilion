from typing import Iterable
from django.db import models
from django.utils import timezone
from django.utils.timezone import localtime, now
from inventory.models import Recipe
from gift_cards.models import GiftCard
from . import globals
import json
import uuid
from payments.models import PaymentAuthorization
from django.core.cache import cache

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

class Dish(models.Model):
    """
    Dish model representing a dish in the POS system.
    Attributes:
        stations (tuple): Choices for the station where the dish is prepared.
        title (CharField): The title of the dish.
        price (FloatField): The price of the dish.
        image (ImageField): An optional image of the dish.
        description (TextField): An optional description of the dish.
        components (ManyToManyField): Components that make up the dish, related through DishComponent.
        menu (ManyToManyField): Menus that include the dish.
        station (CharField): The station where the dish is prepared.
        in_stock (BooleanField): Indicates if the dish is in stock.
        force_in_stock (BooleanField): Indicates if the dish is forced to be in stock.
        recipe (ForeignKey): The recipe associated with the dish from the inventory.
        visible_in_menu (BooleanField): Indicates if the dish is visible in the menu.
    Methods:
        __str__(): Returns the title of the dish.
        check_if_only_choice_dish(): Checks whether the dish only consists of components that point to other dishes.
        serialize_with_options(): Serializes the dish with its options and components.
    """
    stations = (
        ("bar", "Bar"),
        ("kitchen", "Kitchen"),
        ("gng", "Grab & Go")
    )
    title = models.CharField(max_length=140)
    price = models.FloatField()
    image = models.ImageField(upload_to='files/dish_images', null=True, blank=True)
    description = models.TextField(blank=True)
    components = models.ManyToManyField("Component", through='DishComponent')
    menu = models.ManyToManyField("Menu", related_name = "dishes")
    station = models.CharField(max_length=50, choices=stations)
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
                "price":self.price,
                "image":self.image.url if self.image else None,
                "description":self.description,
                "menu":self.menu.first().id,
                "station":self.station,
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
    Order model representing an order in the POS system.
    Attributes:
        order_channels (tuple): Choices for the order channel.
        station_statuses (tuple): Choices for the status of different stations.
        timestamp (DateTimeField): The timestamp when the order was created.
        start_time (DateTimeField): The start time of the order.
        prep_time (DurationField): The preparation time for the order.
        dishes (ManyToManyField): The dishes associated with the order.
        table (CharField): The table number for the order.
        kitchen_status (PositiveSmallIntegerField): The status of the kitchen station.
        bar_status (PositiveSmallIntegerField): The status of the bar station.
        gng_status (PositiveSmallIntegerField): The status of the grab-and-go station.
        picked_up (BooleanField): Whether the order has been picked up.
        special_instructions (TextField): Special instructions for the order.
        to_go_order (BooleanField): Whether the order is a to-go order.
        final_revenue (DecimalField): The final revenue for the order.
        channel (CharField): The channel through which the order was placed.
        phone (PositiveBigIntegerField): The phone number associated with the order.
        approved (BooleanField): Whether the order has been approved.
        authorization (ForeignKey): The payment authorization for the order.
        gift_cards (ManyToManyField): The gift cards associated with the order.
    Methods:
        save(*args, **kwargs): Saves the order and updates the active orders cache.
        delete(*args, **kwargs): Deletes the order and updates the active orders cache.
        __str__() -> str: Returns a string representation of the order.
        __eq__(other): Checks if another order is equal to this order.
        __hash__(): Returns the hash of the order.
        progress_status(): Returns the progress status of the order.
    """
    order_channels = (
        ("store", "In-person"),
        ("web", "Online pickup"),
        ("delivery", "Delivery")
    )
    station_statuses = (
        (0, "Pending approval"),
        (1, "Approved"),
        (2, "Completed"),
        (3, "Rejected"),
        (4, "Not required"),
    )
    timestamp = models.DateTimeField(default=timezone.now)
    start_time = models.DateTimeField(default=timezone.now)
    prep_time = models.DurationField(null=True, blank=True)
    dishes = models.ManyToManyField(Dish, through="OrderDish")
    table = models.CharField(null=True, max_length = 140, blank=True)
    kitchen_status = models.PositiveSmallIntegerField(default=4, choices=station_statuses)
    bar_status = models.PositiveSmallIntegerField(default=4, choices=station_statuses)
    gng_status = models.PositiveSmallIntegerField(default=4, choices=station_statuses)
    picked_up = models.BooleanField(default=True)
    special_instructions = models.TextField(null=True, blank=True)
    to_go_order = models.BooleanField(default=False)
    final_revenue = models.DecimalField(max_digits=6, decimal_places=2, default=0.00, null=True)
    channel = models.CharField(max_length=10, choices=order_channels)
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    approved = models.BooleanField(default=True)
    authorization = models.ForeignKey(PaymentAuthorization, on_delete=models.DO_NOTHING, null=True, blank=True)
    gift_cards = models.ManyToManyField(GiftCard, through="gift_cards.GiftCardAuthorization", blank=True)

    def save(self, *args, **kwargs):
        # Calculate prep time if needed
        if (self.bar_status == 2 or self.bar_status == 4) and (self.kitchen_status == 2 or self.kitchen_status == 4) and (self.gng_status == 2 or self.gng_status == 4):
            if not self.prep_time:
                self.prep_time = timezone.now() - self.timestamp

        # Call the original save method
        super().save(*args, **kwargs)

        # Update the active orders cache
        update_active_orders_cache()

    def delete(self, *args, **kwargs):
        # Ensure the cache is updated when an order is deleted
        super().delete(*args, **kwargs)
        update_active_orders_cache()

    def __str__(self) -> str:
        return f"Order {self.id}"
    
    def __eq__(self, other):
        if isinstance(other, Order):
            return self.id == other.id
        return False

    def __hash__(self):
        return hash(self.id)
    
    def progress_status(self):
        """
        Determine the progress status of an order based on various conditions.

        Returns:
            int: The status code representing the progress of the order.
                - 6: Delivery completed.
                - 5: Order picked up.
                - 4: Order ready (kitchen, bar, and gng statuses are 2 or 4).
                - 3: Order in progress (any of kitchen, bar, or gng statuses is 1).
                - 2: Order not started (any of kitchen, bar, or gng statuses is 0 and start time is in the past).
                - 1: Order is scheduled (any of kitchen, bar, or gng statuses is 0 and start time is in the future).
            None: If the channel is neither "delivery" nor "web".
        """
        print(timezone.localtime(self.start_time))
        print(timezone.localtime(timezone.now()))
        print(timezone.localtime(self.start_time) > timezone.localtime(timezone.now()))
        if self.channel == "delivery":
            if self.delivery.first().completed:
                status = 6
            elif self.picked_up:
                status = 5
            elif not self.picked_up and self.kitchen_status in [2, 4] and self.bar_status in [2, 4] and self.gng_status in [2, 4]:
                status = 4
            elif not self.picked_up and (self.kitchen_status == 1 or self.bar_status == 1 or self.gng_status == 1):
                status = 3
            elif (self.kitchen_status == 0 or self.bar_status == 0 or self.gng_status == 0) and self.start_time <= timezone.now():
                status = 2
            else:
                status = 1
        elif self.channel == "web":
            if self.picked_up:
                status = 5
            elif not self.picked_up and self.kitchen_status in [2, 4] and self.bar_status in [2, 4] and self.gng_status in [2, 4]:
                status = 4
            elif not self.picked_up and (self.kitchen_status == 1 or self.bar_status == 1 or self.gng_status == 1):
                status = 3
            elif (self.kitchen_status == 0 or self.bar_status == 0 or self.gng_status == 0) and self.start_time <= timezone.now():
                status = 2
            else:
                status = 1
        else:
            status = None
        return status

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

    def save(self, *args, **kwargs):
        if self.pk is not None:
            try:
                if kwargs['force_update_stock']:
                    pass
            except KeyError:
                if self.inventory <= 0:
                    self.in_stock = False
                else:
                    self.in_stock = True

            for dc in self.dishcomponent_set.all():
                if self.inventory < dc.quantity or not self.in_stock:
                    dc.dish.in_stock = False
                elif self.inventory >= dc.quantity and dc.dish.force_in_stock:
                    dc.dish.in_stock = True
                    dc.dish.force_in_stock = False
                dc.dish.save()
        # Call the original save method
        super().save()

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

def update_active_orders_cache():
    """
    Updates the cache with the list of active orders.

    This function queries the database for orders that are considered active based on their
    start time and various status fields. It then serializes the relevant order information
    and stores it in the cache with a timeout of 60 seconds.

    Active orders are defined as orders that:
    - Have a start time less than or equal to the current time.
    - Have a kitchen status of 0 or 1.
    - Have a bar status of 0 or 1.
    - Have a gng status of 0 or 1.
    - Have not been picked up.

    The cached data is a list of active order IDs.

    Returns:
        None
    """
    # Query for active orders
    active_orders = Order.objects.filter(
        models.Q(start_time__lte=now()) &
        (models.Q(kitchen_status=0) |
        models.Q(kitchen_status=1) |
        models.Q(bar_status=0) |
        models.Q(bar_status=1) |
        models.Q(gng_status=0) |
        models.Q(gng_status=1) |
        models.Q(picked_up=False))
    )
    # Serialize or prepare active orders for caching
    serialized_orders = [order.id for order in active_orders]  # Or serialize with necessary fields
    cache.set('active_orders', serialized_orders, timeout=60)  # Cache the list of active order IDs
