from django.db import models

# Create your models here.
    
class Menu(models.Model):
    title = models.CharField(max_length=140)
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
        ("kitchen", "Kitchen")
    )
    title = models.CharField(max_length=140)
    price = models.FloatField()
    components = models.ManyToManyField("Component", through='DishComponent')
    menu = models.ForeignKey("Menu", on_delete = models.CASCADE, related_name = "dishes")
    station = models.CharField(max_length=50, choices=stations)

    def __str__(self) -> str:
        return self.title

class Order(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    prep_time = models.DurationField(null=True)
    dishes = models.ManyToManyField(Dish, through="OrderDish")
    table = models.CharField(null=True, max_length = 140)
    kitchen_done = models.BooleanField(default=False)
    bar_done = models.BooleanField(default=False)
    special_instructions = models.TextField(null=True)

    def __str__(self) -> str:
        return f"Order {self.id}"

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
    title = models.CharField(max_length=140)
    ingredients = models.ManyToManyField("Ingredient", through='ComponentIngredient')
    inventory = models.PositiveIntegerField(default = 0, null = True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)

    def __str__(self) -> str:
        qty = self.inventory
        if qty is None:
            inventory = "Inventory not tracked"
        else:
            inventory = f"{qty} X in inventory"
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
    title = models.CharField(max_length=140)
    inventory = models.PositiveIntegerField(default = 0, null = True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)

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