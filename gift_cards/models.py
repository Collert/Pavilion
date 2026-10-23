from django.conf import settings
from django.db import models
from decimal import Decimal
from django.utils import timezone
from django.utils.crypto import get_random_string

# Create your models here.

class GiftCard(models.Model):
    """
    GiftCard model represents a gift card with an associated email, image, available balance, and unique number.
    Attributes:
        email (CharField): The email associated with the gift card. Optional.
        image (ImageField): The image associated with the gift card. Optional.
        available_balance (DecimalField): The available balance on the gift card. Required.
        number (CharField): The unique number of the gift card. Required.
    Methods:
        save(*args, **kwargs): Saves the gift card instance, generating a unique number if not set.
        generate_unique_number(): Generates a unique 16-digit number for the gift card.
        charge_card(amount: Decimal) -> int: Charges the gift card with the specified amount if the balance is sufficient. Returns 200 on success, 402 on insufficient balance.
        load_card(amount: Decimal) -> None: Loads the gift card with the specified amount.
    """
    email = models.CharField(max_length=64, blank=True)
    image = models.ImageField(upload_to='files/gift-cards/presets', null=True)
    available_balance = models.DecimalField(null = False, default=0, max_digits=5, decimal_places=2)
    number = models.CharField(
        max_length=16,
        default="",
        unique=True,
        null=False
    )

    def save(self, *args, **kwargs):
        # Generate a unique `number` if it's not set or if it clashes with an existing one
        if not self.number:
            self.number = self.generate_unique_number()
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_number(cls):
        """
        Generates a unique 16-digit number for a gift card.

        This method continuously generates a random 16-digit number using the 
        specified allowed characters ('1234567890') until it finds one that 
        does not already exist in the database.

        Returns:
            str: A unique 16-digit number.
        """
        while True:
            new_number = get_random_string(length=16, allowed_chars='1234567890')
            if not cls.objects.filter(number=new_number).exists():
                return new_number
    
    def charge_card(self, amount:Decimal) -> int:
        """
        Charges the gift card with the specified amount if the available balance is sufficient.

        Args:
            amount (Decimal): The amount to charge to the gift card.

        Returns:
            int: 200 if the charge was successful, 402 if the available balance is insufficient.
        """
        if self.available_balance >= amount:
            self.available_balance -= amount
            self.save()
            return 200
        else:
            return 402

    def load_card(self, amount:Decimal) -> None:
        """
        Loads a specified amount onto the gift card and updates the available balance.

        Args:
            amount (Decimal): The amount to be loaded onto the gift card.

        Returns:
            None
        """
        self.available_balance += amount
        self.save()

class GiftCardAuthorization(models.Model):
    """
    Represents an authorization for a gift card used in an order.

    Attributes:
        card (ForeignKey): The gift card being authorized.
        order (ForeignKey): The order associated with the gift card authorization.
        charged_balance (DecimalField): The amount charged to the gift card.
    """
    card = models.ForeignKey(GiftCard, on_delete = models.CASCADE)
    order = models.ForeignKey("pos_server.Order", on_delete=models.CASCADE, related_name="gift_card_auth")
    charged_balance = models.DecimalField(decimal_places=2, max_digits=5)

class CardPreset(models.Model):
    """
    CardPreset model represents a preset configuration for gift cards.
    Attributes:
        name (str): The name of the card preset. It is an optional field with a maximum length of 64 characters.
        amount (Decimal): The amount associated with the card preset. It is a required field with a maximum of 5 digits and 2 decimal places.
        image (ImageField): An optional image associated with the card preset. The image is uploaded to 'files/gift-cards/presets'.
    Methods:
        __str__(): Returns the name of the card preset as its string representation.
    """
    name = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(null = False, max_digits=5, decimal_places=2)
    image = models.ImageField(upload_to='files/gift-cards/presets', null=True)

    def __str__(self) -> str:
        return self.name
    
class SpecialCard(CardPreset):
    cost = models.DecimalField(null = False, max_digits=5, decimal_places=2)



class StaffCardType(models.Model):
    """
    A kind of staff card (design: AdminStaffCards; edited on the admin's Staff cards page). A *balance* card holds
    money to spend like a gift card, topped back up on a schedule (`refill`; `leftover` says whether unspent money
    resets or carries over, up to `cap`). A *discount* card takes a percent or an amount off (`discount_cap` per
    order for percents) as often as `uses` allows. Rules: `own_only` puts the order under the holder's name,
    `no_alcohol` leaves out dishes made with alcohol (Ingredient.alcohol), `stack` lets a discount card add to other
    discounts (else the better one wins), `on_shift` only while they're clocked in, `channels` where it works. An
    "every shift" balance refills when they clock in. Worked out in gift_cards/staff_cards.py.
    """
    BALANCE, DISCOUNT = "balance", "discount"
    KINDS = ((BALANCE, "Balance card"), (DISCOUNT, "Discount card"))
    REFILLS = (("shift", "Every shift, when they clock in"), ("day", "Every day"), ("week", "Every week"), ("month", "Every month"),
               ("never", "Never, one-time balance"))
    LEFTOVER = (("reset", "Resets"), ("carry", "Carries over"))
    USES = (("any", "Every order"), ("day", "Once a day"), ("week", "Once a week"))
    CHANNELS = (("store", "In store"), ("pickup", "Pickup"), ("delivery", "Delivery"))

    name = models.CharField(max_length=30, unique=True)
    color = models.CharField(max_length=7, default="#12875A")
    kind = models.CharField(max_length=8, choices=KINDS, default=BALANCE)
    live = models.BooleanField(default=True, help_text="Off: cards are declined")
    position = models.PositiveIntegerField(default=0)
    amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("10.00"), help_text="Balance cards: what each refill gives")
    refill = models.CharField(max_length=5, choices=REFILLS, default="day")
    refill_weekday = models.PositiveSmallIntegerField(default=0, help_text="Weekly refills: 0 Monday … 6 Sunday")
    refill_day = models.CharField(max_length=4, default="1", help_text="Monthly refills: 1, 15 or last")
    leftover = models.CharField(max_length=5, choices=LEFTOVER, default="reset")
    cap = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("50.00"), help_text="Carrying over: never more than this")
    discount_kind = models.CharField(max_length=3, choices=(("pct", "Percent off"), ("amt", "Amount off")), default="pct")
    discount_value = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("20.00"))
    discount_cap = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Percent off: at most this per order")
    uses = models.CharField(max_length=4, choices=USES, default="any")
    own_only = models.BooleanField(default=True, help_text="The order goes under the holder's name")
    no_alcohol = models.BooleanField(default=True)
    stack = models.BooleanField(default=False, help_text="Adds to other discounts (else the better one wins)")
    on_shift = models.BooleanField(default=False, help_text="Works only while the holder is clocked in (team/clock.py)")
    channels = models.JSONField(default=list, blank=True, help_text="Where it works: store, pickup, delivery")
    archived = models.BooleanField(default=False, help_text="Deleted on the Staff cards page; kept for past orders")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name


class StaffCard(models.Model):
    """One staff member's card of a type: a number on a printed (or pre-printed and scanned) card, and the balance
    of a balance card (refilled when it's next read, gift_cards/staff_cards.py: refill)."""
    type = models.ForeignKey(StaffCardType, on_delete=models.CASCADE, related_name="cards")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_cards")
    number = models.CharField(max_length=32, unique=True)
    balance = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    refilled_at = models.DateTimeField(default=timezone.now, help_text="When the balance was last topped up")
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["issued_at", "id"]

    def __str__(self):
        return f"{self.type} •• {self.number[-4:]}"

    @staticmethod
    def new_number() -> str:
        """16 digits no gift card or staff card has."""
        while True:
            number = get_random_string(length=16, allowed_chars="1234567890")
            if not StaffCard.objects.filter(number=number).exists() and not GiftCard.objects.filter(number=number).exists():
                return number


class StaffCardUse(models.Model):
    """What a card did on an order: took a discount, paid from its balance, or got money back (rejected, refunded)."""
    DISCOUNT, SPEND, BACK = "discount", "spend", "back"
    card = models.ForeignKey(StaffCard, on_delete=models.CASCADE, related_name="uses")
    order = models.ForeignKey("pos_server.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="staff_card_uses")
    kind = models.CharField(max_length=8, choices=((DISCOUNT, "Discount"), (SPEND, "Spent"), (BACK, "Put back")))
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    at = models.DateTimeField(default=timezone.now)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-at", "-id"]
