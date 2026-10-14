from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password, make_password

# Create your models here.

class Staff(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    minutes_worked = models.PositiveIntegerField(null=True, blank=True, default=0)
    last_worked = models.DateTimeField(default=timezone.now)
    allowance = models.FloatField(default=30)
    pin = models.CharField(max_length=128, blank=True, help_text="Hashed 4-digit register PIN. Set it with set_pin().")

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.user.first_name or self.user.username

    def set_pin(self, raw_pin: str):
        """Store a 4-digit PIN (hashed like a password). An empty PIN removes it."""
        if raw_pin and not (len(raw_pin) == 4 and raw_pin.isdigit()):
            raise ValueError("A PIN is 4 digits")
        self.pin = make_password(raw_pin) if raw_pin else ""

    def check_pin(self, raw_pin: str) -> bool:
        return bool(self.pin) and check_password(raw_pin, self.pin)

class Customer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    profile_picture = models.ImageField(null=True, blank=True, upload_to='files/pfps')
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    home_address = models.TextField(null=True, blank=True)

@receiver(post_save, sender=User)
def create_customer_profile(sender, instance, created, **kwargs):
    if created:
        Customer.objects.create(user=instance)