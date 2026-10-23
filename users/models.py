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

class Role(models.Model):
    """A job people do here (Cook, Cashier...): shown on their profile, what a shift is for, filtered on in the
    admin's Staff page. Roles don't grant anything themselves; their starting groups are ticked for whoever gets
    them (admin's Roles and Staff pages), and groups grant."""
    name = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=160, blank=True)
    color = models.CharField(max_length=7, default="#12875A")
    icon = models.CharField(max_length=20, default="knife", help_text="A key of admin_portal/team.py: ROLE_ICONS")
    position = models.PositiveIntegerField(default=0)
    groups = models.ManyToManyField("auth.Group", blank=True, related_name="starting_roles",
                                    help_text="Starting groups: ticked when someone gets this role")
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class GroupInfo(models.Model):
    """What the admin shows about a group (django.contrib.auth Group: the permissions). The Owners group is the one
    with `owners` set: its members are superusers (everything, including the admin)."""
    group = models.OneToOneField("auth.Group", on_delete=models.CASCADE, related_name="info")
    description = models.CharField(max_length=160, blank=True)
    color = models.CharField(max_length=7, default="#5B6A62")
    position = models.PositiveIntegerField(default=0)
    owners = models.BooleanField(default=False, help_text="Members get everything (they're made superusers)")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.group.name


class Staff(models.Model):
    """A staff member's profile (the account is the User: is_staff, name, email, is_active = can sign in)."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    phone = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(upload_to="files/staff/", blank=True, max_length=300)
    emergency_name = models.CharField(max_length=60, blank=True)
    emergency_phone = models.CharField(max_length=20, blank=True)
    since = models.DateField(default=timezone.localdate, help_text="Staff since")
    roles = models.ManyToManyField(Role, blank=True, related_name="staff")
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
    """A customer's profile: what the store's account pages edit (online_store/account.py)."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    profile_picture = models.ImageField(null=True, blank=True, upload_to='files/pfps')
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    home_address = models.TextField(null=True, blank=True)
    organization = models.CharField(max_length=120, blank=True, help_text="Shows on catering requests and invoices")
    order_texts = models.BooleanField(default=True, help_text="Texts when an order is ready or on the way")
    catering_emails = models.BooleanField(default=True, help_text="Emails about catering messages and proposed changes")
    newsletter = models.BooleanField(default=False, help_text="The monthly menu email")
    square_customer_id = models.CharField(max_length=64, blank=True, help_text="Where their saved cards live in Square")

    @property
    def phone_text(self) -> str:
        digits = str(self.phone or "")
        if len(digits) == 10:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return digits


class SavedCard(models.Model):
    """A card on file in Square for a signed-in customer (only its brand and last 4 are kept here)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_cards")
    square_customer_id = models.CharField(max_length=64)
    square_card_id = models.CharField(max_length=64, help_text="'demo:' ids are seed_demo's or a DEBUG test card")
    brand = models.CharField(max_length=24, blank=True)
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        return f"{(self.brand or 'Card').title()} ending {self.last4}"

    def as_card(self) -> dict:
        """The shape catering and checkout use for a card on file."""
        return {"customer": self.square_customer_id, "id": self.square_card_id, "brand": self.brand, "last4": self.last4}


class SavedAddress(models.Model):
    """A delivery address a customer keeps (Work, Home...)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_addresses")
    label = models.CharField(max_length=40, default="Home")
    line = models.CharField(max_length=160, help_text="Street address")
    unit = models.CharField(max_length=60, blank=True)
    city = models.CharField(max_length=80, blank=True)
    postal = models.CharField(max_length=12, blank=True)
    note = models.CharField(max_length=160, blank=True, help_text="For the driver: buzzer, where to leave it")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.label}: {self.line}"

    @property
    def one_line(self) -> str:
        return ", ".join(filter(None, [self.line, self.unit]))

@receiver(post_save, sender=User)
def create_customer_profile(sender, instance, created, **kwargs):
    if created:
        Customer.objects.create(user=instance)