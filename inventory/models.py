from django.db import models

# Create your models here.

class StockUpdate(models.Model):
    """
    Model representing a stock update.

    Attributes:
        date (DateTimeField): The date and time when the stock update was created. Automatically set to the current date and time when the object is created.
        receipt (FileField): An optional file field to upload a receipt for the stock update. The file will be uploaded to the 'files/ingredient_receipts' directory.
    """
    date = models.DateTimeField(auto_now_add = True)
    receipt = models.FileField(upload_to='files/ingredient_receipts', null=True)

class UpdateIngredient(models.Model):
    """
    Model representing an update to an ingredient's stock.

    Attributes:
        update_obj (ForeignKey): Reference to the StockUpdate model. When the referenced StockUpdate is deleted, this update will also be deleted.
        ingredient (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this update will also be deleted.
        quantity (IntegerField): The quantity of the ingredient being updated. Defaults to 1.
    """
    update_obj = models.ForeignKey("StockUpdate", on_delete = models.CASCADE)
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)

class Recipe(models.Model):
    """
    Represents a recipe in the inventory system.
    Attributes:
        markdown_text (TextField): The markdown text containing the recipe details.
        original_yield (PositiveIntegerField): The original yield of the recipe, default is 1.
    Methods:
        __str__() -> str: Returns a string representation of the recipe, prioritizing the dish or component title if available.
    """
    markdown_text = models.TextField()
    original_yield = models.PositiveIntegerField(default=1)

    def __str__(self) -> str:
        if self.dish.first():
            return f"Recipe for {self.dish.first().title}"
        elif self.component.first():
            return f"Recipe for {self.component.first().title}"
        return super().__str__()
    
class Packaging(models.Model):
    """
    Represents a packaging in the inventory system.
    Attributes:
        name (CharField): The name of the packaging.
        quantity (PositiveIntegerField): The quantity of the packaging.
    """
    name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    cost = models.DecimalField(max_digits=10, decimal_places=2)

class CondimentUse(models.Model):
    """
    Represents a condiment usage entry. Used to adjust waste reports.
    Attributes:
        date (DateTimeField): The date and time when the condiment was used. Automatically set to the current date and time when the object is created.
        condiment (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this usage entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the condiment used.
    """
    date = models.DateTimeField(auto_now_add = True)
    condiment = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()
    
class IngredientUsageBuffer(models.Model):
    # TODO: Add functionality to mark out ingredients which will add the quantity to the buffer and subtract from the stock.
    # Later, the buffer can be used in crafting and serve as a comparison between the expected and actual usage.
    # At the end of the day, convert the buffer to a waste entry.
    """
    Represents a used unaccounted for ingredient.
    Attributes:
        recipe (ForeignKey): Reference to the Recipe model. When the referenced Recipe is deleted, this buffer will also be deleted.
        ingredient (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this buffer will also be deleted.
        quantity (PositiveIntegerField): The quantity of the ingredient used in the recipe.
    """
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE, related_name="usage_buffer")
    usage = models.PositiveIntegerField()

class WasteIngredient(models.Model):
    """
    Represents a waste entry for ingredients.
    Attributes:
        date (DateTimeField): The date and time when the waste entry was created. Automatically set to the current date and time when the object is created.
        ingredient (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this waste entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the ingredient wasted.
    """
    date = models.DateTimeField(auto_now_add = True)
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()

class WasteComponent(models.Model):
    """
    Represents a waste entry for components.
    Attributes:
        date (DateTimeField): The date and time when the waste entry was created. Automatically set to the current date and time when the object is created.
        component (ForeignKey): Reference to the Component model in the pos_server app. When the referenced Component is deleted, this waste entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the component wasted.
    """
    date = models.DateTimeField(auto_now_add = True)
    component = models.ForeignKey("pos_server.Component", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()

class WastePackaging(models.Model):
    """
    Represents a waste entry for packaging.
    Attributes:
        date (DateTimeField): The date and time when the waste entry was created. Automatically set to the current date and time when the object is created.
        packaging (ForeignKey): Reference to the Packaging model. When the referenced Packaging is deleted, this waste entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the packaging wasted.
    """
    date = models.DateTimeField(auto_now_add = True)
    packaging = models.ForeignKey("Packaging", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()