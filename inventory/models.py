from django.db import models
from pos_server.models import *

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