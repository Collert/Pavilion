from django.db import models
from pos_server.models import *

# Create your models here.

class StockUpdate(models.Model):
    date = models.DateTimeField(auto_now_add = True)
    receipt = models.FileField(upload_to='files/ingredient_receipts', null=True)

class UpdateIngredient(models.Model):
    update_obj = models.ForeignKey("StockUpdate", on_delete = models.CASCADE)
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)

class Recipe(models.Model):
    markdown_text = models.TextField()
    original_yield = models.PositiveIntegerField(default=1)

    def __str__(self) -> str:
        if self.dish.first():
            return f"Recipe for {self.dish.first().title}"
        elif self.component.first():
            return f"Recipe for {self.component.first().title}"
        return super().__str__()