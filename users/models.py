from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.auth.hashers import PBKDF2PasswordHasher, check_password


class PinHasher(PBKDF2PasswordHasher):
    """
    PBKDF2 with few iterations, for register PINs only (passwords keep Django's default).

    Django's password hashing takes ~1 s per check on purpose. That cost does little for a 4-digit PIN: there are
    only 10,000 of them, so a stolen hash falls quickly either way. What protects PINs is the lock screen's
    rate limit (see pos_server.register.lock), so they're hashed cheaply and unlocking feels instant.
    Not in PASSWORD_HASHERS: it's used directly by Staff, never for passwords.
    """
    algorithm = "pin_pbkdf2_sha256"
    iterations = 10_000


PIN_HASHER = PinHasher()

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
        """Store a 4-digit PIN, hashed with PinHasher. An empty PIN removes it."""
        if raw_pin and not (len(raw_pin) == 4 and raw_pin.isdigit()):
            raise ValueError("A PIN is 4 digits")
        self.pin = PIN_HASHER.encode(raw_pin, PIN_HASHER.salt()) if raw_pin else ""

    def check_pin(self, raw_pin: str) -> bool:
        if not self.pin:
            return False
        if self.pin.startswith(PIN_HASHER.algorithm + "$"):
            return PIN_HASHER.verify(raw_pin, self.pin)
        # Set before PINs had their own hasher (a full password hash): check it the slow way once, then re-hash
        if check_password(raw_pin, self.pin):
            self.set_pin(raw_pin)
            self.save(update_fields=["pin"])
            return True
        return False

class Customer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    profile_picture = models.ImageField(null=True, blank=True, upload_to='files/pfps')
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    home_address = models.TextField(null=True, blank=True)

@receiver(post_save, sender=User)
def create_customer_profile(sender, instance, created, **kwargs):
    if created:
        Customer.objects.create(user=instance)