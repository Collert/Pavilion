from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

# Create your models here.

class Staff(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    minutes_worked = models.PositiveIntegerField(null=True, blank=True, default=0)
    last_worked = models.DateTimeField(default=timezone.now)
    allowance = models.FloatField(default=30)

class Customer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    profile_picture = models.ImageField(null=True, blank=True)