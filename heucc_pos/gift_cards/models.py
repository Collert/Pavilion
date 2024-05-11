from django.db import models
from django.utils.crypto import get_random_string

# Create your models here.

class GiftCard(models.Model):
    number = models.CharField(max_length=16, default=f"{get_random_string(length=16, allowed_chars='1234567890')}", null=False)
    email = models.CharField(max_length=64, blank=True)
    available_balance = models.DecimalField(null = False, default=0, max_digits=5, decimal_places=2)

class CardPresets(models.Model):
    name = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(null = False, max_digits=5, decimal_places=2)