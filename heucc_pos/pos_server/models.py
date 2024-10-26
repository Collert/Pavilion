from typing import Iterable
from django.db import models
from django.utils import timezone
from inventory.models import Recipe
from . import globals
import uuid
from payments.models import PaymentAuthorization

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

    def __str__(self) -> str:
        return self.title

class Order(models.Model):
    order_channels = (
        ("store", "In-person"),
        ("web", "Online pickup"),
        ("delivery", "Delivery")
    )
    timestamp = models.DateTimeField(default=timezone.now)
    start_time = models.DateTimeField(default=timezone.now)
    prep_time = models.DurationField(null=True, blank=True)
    dishes = models.ManyToManyField(Dish, through="OrderDish")
    table = models.CharField(null=True, max_length = 140, blank=True)
    kitchen_done = models.BooleanField(default=True)
    kitchen_needed = models.BooleanField(default=False)
    bar_done = models.BooleanField(default=True)
    gng_done = models.BooleanField(default=True) # I will get to implementing this soon
    picked_up = models.BooleanField(default=True)
    special_instructions = models.TextField(null=True, blank=True)
    to_go_order = models.BooleanField(default=False)
    final_revenue = models.DecimalField(max_digits=6, decimal_places=2, default=0.00, null=True)
    channel = models.CharField(max_length=10, choices=order_channels)
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    approved = models.BooleanField(default=True)
    authorization = models.ForeignKey(PaymentAuthorization, on_delete=models.DO_NOTHING, null=True, blank=True)

    def save(self, temp=False):
        if self.bar_done and self.kitchen_done and self.gng_done:
            if not temp:
                if not self.prep_time:
                    self.prep_time = timezone.now() - self.timestamp
                if self.kitchen_done and self.kitchen_done == Order.objects.get(pk=self.id).kitchen_done and self.channel == "store":
                    self.picked_up=True
                try:
                    globals.active_orders.remove(self)
                except KeyError:
                    pass
                print(self.start_time)
                print(timezone.now())
                if not self.picked_up and self.start_time > timezone.now():
                    globals.active_orders.add(self)
        else:
            try:
                globals.active_orders.remove(self)
            except KeyError:
                pass
            globals.active_orders.add(self)
        print(globals.active_orders)
        return super().save()

    def __str__(self) -> str:
        return f"Order {self.id}"
    
    def __eq__(self, other):
        if isinstance(other, Order):
            return self.id == other.id
        return False

    def __hash__(self):
        return hash(self.id)

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
    crafting_option = models.CharField(max_length=10, choices=crafting_options)
    recipe = models.ForeignKey("inventory.Recipe", related_name="component", on_delete=models.DO_NOTHING, null=True, blank=True)

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
    token = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=140)

    def __str__(self) -> str:
        return self.name