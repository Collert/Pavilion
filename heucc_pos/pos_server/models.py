from django.db import models

# Create your models here.
    
class Menu(models.Model):
    title = models.CharField(max_length=140)

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
    prep_time = models.DurationField()
    dishes = models.ManyToManyField(Dish, through="OrderDish")
    table = models.IntegerField(null=True)
    kitchen_done = models.BooleanField(default=False)
    bar_done = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Order {self.id}"

class OrderDish(models.Model):
    order = models.ForeignKey(Order, on_delete = models.CASCADE)
    dish = models.ForeignKey(Dish, on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)

    def __str__(self) -> str:
        return f"{self.quantity} X {self.dish.title} in order {self.order.id}"
    
class Component(models.Model):
    title = models.CharField(max_length=140)
    ingredients = models.ManyToManyField("Ingredient", through='ComponentIngredient')
    inventory = models.PositiveIntegerField(default = 0, null = True)

    def __str__(self) -> str:
        qty = self.inventory
        if qty is None:
            inventory = "Inventory not tracked"
        else:
            inventory = f"{qty} X in inventory"
        return f"Component: {self.title}. {inventory}"
    
class DishComponent(models.Model):
    dish = models.ForeignKey('Dish', on_delete=models.CASCADE)
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} x {self.component.name} in {self.dish.name}"

class Ingredient(models.Model):
    title = models.CharField(max_length=140)
    inventory = models.PositiveIntegerField(default = 0, null = True)

    def __str__(self) -> str:
        qty = self.inventory
        if qty is None:
            inventory = "Inventory not tracked"
        else:
            inventory = f"{qty} X in inventory"
        return f"Ingredient: {self.title}. {inventory}"
    
class ComponentIngredient(models.Model):
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    ingredient = models.ForeignKey('Ingredient', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} x {self.ingredient.name} in {self.component.name}"