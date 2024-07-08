from typing import Iterable
from django.db import models
from django.utils import timezone
from pos_server.models import Order
from django.contrib.auth.models import User

# Create your models here.

class Delivery(models.Model):
    timestamp = models.DateTimeField(default=timezone.now)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name = "delivery")
    destination = models.TextField()
    instructions = models.TextField(blank=True, null=True)
    courier = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    completed = models.BooleanField(default=False)
    eta = models.DateTimeField(blank=True, null=True)
    phone = models.PositiveBigIntegerField()
