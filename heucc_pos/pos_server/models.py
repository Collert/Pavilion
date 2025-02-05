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
        """Checks whether the dish only consists of components that point to other dishes"""
        if not self.components.all():
            return False
        for component in self.components.all():
            if not component.child_dishes.all():
                return False
        return True

    def serialize_with_options(self):
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
    order = models.ForeignKey(Order, on_delete = models.CASCADE)
    dish = models.ForeignKey(Dish, on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)

    def __str__(self) -> str:
        return f"{self.quantity} X {self.dish.title} in order {self.order.id}"
    
class Component(models.Model):
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

    def __str__(self) -> str:
        qty = self.inventory
        if qty is None:
            inventory = "Inventory not tracked"
        else:
            inventory = f"{qty} X in inventory"
        return f"{self.title} ({self.unit_of_measurement.title()})"
    
class ComponentIngredient(models.Model):
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    ingredient = models.ForeignKey('Ingredient', on_delete=models.CASCADE)
    quantity = models.FloatField()

    def __str__(self):
        return f"{self.quantity} x {self.ingredient.title} in {self.component.title}"
    
class EligibleDevice(models.Model):
    token = models.UUIDField(default=uuid.uuid4)
    name = models.CharField(max_length=140)

    def __str__(self) -> str:
        return self.name

def update_active_orders_cache():
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
