from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

# Create your models here.

class Staff(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    minutes_worked = models.PositiveIntegerField(null=True, blank=True, default=0)
    last_worked = models.DateTimeField(default=timezone.now)
    allowance = models.FloatField(default=30)

class Customer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    profile_picture = models.ImageField(null=True, blank=True, upload_to='files/pfps')
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    home_address = models.TextField(null=True, blank=True)

@receiver(post_save, sender=User)
def create_customer_profile(sender, instance, created, **kwargs):
    if created:
        Customer.objects.create(user=instance)