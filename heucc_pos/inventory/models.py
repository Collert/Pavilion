from django.db import models
from pos_server.models import *

# Create your models here.

class StockUpdate(models.Model):
    date = models.DateTimeField(auto_now_add = True)
    receipt = models.FileField(upload_to='files/ingredient_receipts', null=True)

class UpdateIngredient(models.Model):
    update_obj = models.ForeignKey(StockUpdate, on_delete = models.CASCADE)
    ingredient = models.ForeignKey(Ingredient, on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)