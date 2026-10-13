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
    address_2 = models.TextField(blank=True, null=True)
    instructions = models.TextField(blank=True, null=True)
    courier = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    completed = models.BooleanField(default=False)
    eta = models.DateTimeField(blank=True, null=True)
    phone = models.PositiveBigIntegerField()


class CourierShift(models.Model):
    """A courier who is clocked in and taking deliveries. Deleted when they clock out."""
    modes = (
        ("driving", "Driving"),
        ("bicycling", "Bicycling"),
        ("walking", "Walking"),
        ("transit", "Transit"),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="courier_shift")
    mode = models.CharField(max_length=20, default="driving")
    started_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user} ({self.mode})"
