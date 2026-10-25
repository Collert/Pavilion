import hashlib

from django.conf import settings
from django.db import models
from decimal import Decimal
from django.utils import timezone
from django.utils.crypto import get_random_string

# Create your models here.

def hash_number(number) -> str:
    """What's stored for a gift card number: its digits hashed. The number itself is never kept."""
    digits = "".join(c for c in str(number) if c.isdigit())
    return hashlib.sha256(f"pavilion-gift-card:{digits}".encode()).hexdigest()


class GiftCardDesign(models.Model):
    """
    A gift card's look (design: AdminGiftDesigns; edited on the admin's Gift card designs page): its picture,
    whether the store name and amount are printed on it in light or dark ink, the amounts it's sold in (plus any
    amount between `min_amount` and `max_amount` when `custom`), the message it starts with, where it's sold
    (`online`, `on_register`) and, for seasonal ones, when: `show_from`/`show_until` repeat every year (only the
    month and day count). `archived`: deleted on the page, kept for the cards sold with it.
    """
    INKS = (("light", "Light"), ("dark", "Dark"))
    name = models.CharField(max_length=40)
    image = models.ImageField(upload_to="gift-cards/designs", null=True, blank=True)
    color = models.CharField(max_length=7, default="#1F4A3A", help_text="Shown while there's no picture")
    ink = models.CharField(max_length=5, choices=INKS, default="light")
    show_text = models.BooleanField(default=True, help_text="Print the store name and amount on the card")
    amounts = models.JSONField(default=list, blank=True, help_text="Whole dollars, e.g. [25, 50, 100]")
    custom = models.BooleanField(default=False, help_text="Customers can type another amount")
    min_amount = models.PositiveIntegerField(default=10)
    max_amount = models.PositiveIntegerField(default=250)
    message = models.CharField(max_length=120, blank=True)
    online = models.BooleanField(default=True)
    on_register = models.BooleanField(default=True)
    seasonal = models.BooleanField(default=False)
    show_from = models.DateField(null=True, blank=True)
    show_until = models.DateField(null=True, blank=True)
    archived = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name

    def in_season(self, today=None) -> bool:
        if not self.seasonal or not self.show_from or not self.show_until:
            return True
        day = (today or timezone.localdate()).strftime("%m-%d")
        start, end = self.show_from.strftime("%m-%d"), self.show_until.strftime("%m-%d")
        return start <= day <= end if start <= end else (day >= start or day <= end)  # a season over New Year

    def sold(self, where, today=None) -> bool:
        """On sale `where` ("online" or "register") today."""
        return not self.archived and (self.online if where == "online" else self.on_register) and self.in_season(today)

    def takes(self, amount) -> bool:
        """Whether a card of `amount` (whole dollars) can be sold in this design."""
        return amount == amount.to_integral_value() and 0 < amount <= 1000 and (
            int(amount) in [int(a) for a in self.amounts] or self.custom and self.min_amount <= amount <= self.max_amount)

    @classmethod
    def on_sale(cls, where, today=None):
        return [d for d in cls.objects.filter(archived=False) if d.sold(where, today)]


class GiftCard(models.Model):
    """
    A gift card. Its 16-digit number is shown once, when it's made (emailed, printed or read out), and never kept:
    only `number_hash` (hash_number) and the `last4`. Look a typed or scanned number up with `GiftCard.find`.
    `original` is what it started with; every change of balance is a GiftCardEvent. A `frozen` card can't pay.
    `empty_since`: the day its balance ran out; empty cards are deleted 30 days later (manage.py gift_cards_cleanup).
    """
    ONLINE, REGISTER, STAFF = "online", "pos", "staff"
    SOURCES = ((ONLINE, "Online store"), (REGISTER, "Register"), (STAFF, "Issued by staff"))
    KEEP_EMPTY_DAYS = 30
    MAX_BALANCE = Decimal("1000.00")

    number_hash = models.CharField(max_length=64, unique=True, editable=False)
    last4 = models.CharField(max_length=4, editable=False)
    design = models.ForeignKey(GiftCardDesign, on_delete=models.SET_NULL, null=True, blank=True, related_name="cards")
    image = models.ImageField(upload_to='gift-cards/presets', null=True, blank=True, help_text="Cards bought before designs")
    email = models.CharField(max_length=64, blank=True)
    recipient = models.CharField(max_length=60, blank=True)
    message = models.CharField(max_length=120, blank=True)
    note = models.CharField(max_length=120, blank=True, help_text="Only staff see it")
    available_balance = models.DecimalField(default=0, max_digits=7, decimal_places=2)
    original = models.DecimalField(default=0, max_digits=7, decimal_places=2)
    source = models.CharField(max_length=6, choices=SOURCES, default=ONLINE)
    buyer = models.CharField(max_length=80, blank=True, help_text="Who bought or issued it")
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    frozen = models.BooleanField(default=False)
    empty_since = models.DateField(null=True, blank=True)
    sold_on = models.ForeignKey("pos_server.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_cards_sold",
                                help_text="The order that paid for it (online store or register)")

    number = ""  # the full number, only on the instance that made it (save)

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return f"Gift card •• {self.last4}"

    def save(self, *args, **kwargs):
        if not self.number_hash:
            self.number = new_card_number()
            self.number_hash, self.last4 = hash_number(self.number), self.number[-4:]
            if not self.original:
                self.original = self.available_balance
        if self.available_balance <= 0 and self.empty_since is None:
            self.empty_since = timezone.localdate()
        elif self.available_balance > 0:
            self.empty_since = None
        super().save(*args, **kwargs)

    @classmethod
    def find(cls, number, lock=False):
        """The card with this number (spaces and dashes are fine), or None."""
        digits = "".join(c for c in str(number or "") if c.isdigit())
        if len(digits) < 8:
            return None
        cards = cls.objects.select_for_update() if lock else cls.objects
        return cards.filter(number_hash=hash_number(digits)).first()

    @property
    def deletes_on(self):
        import datetime
        return self.empty_since + datetime.timedelta(days=self.KEEP_EMPTY_DAYS) if self.empty_since else None

    def picture_url(self) -> str:
        if self.design and self.design.image:
            return self.design.image.url
        return self.image.url if self.image else ""

    def charge_card(self, amount: Decimal, order=None, by=None) -> int:
        """Take `amount` off for an order. 200, or 402 if the balance is short or the card is frozen."""
        if self.frozen or self.available_balance < amount:
            return 402
        self.available_balance -= amount
        self.save()
        GiftCardEvent.objects.create(card=self, kind=GiftCardEvent.USE, amount=-amount, balance=self.available_balance, order=order, by=by)
        return 200

    def load_card(self, amount: Decimal, order=None, by=None, kind=None, note="") -> None:
        """Put `amount` on the card: money back from an order (the default), or an adjustment."""
        self.available_balance += amount
        self.save()
        GiftCardEvent.objects.create(card=self, kind=kind or GiftCardEvent.REFUND, amount=amount, balance=self.available_balance,
                                     order=order, by=by, note=note)


def new_card_number() -> str:
    """16 digits no gift card or staff card has."""
    while True:
        number = get_random_string(length=16, allowed_chars="1234567890")
        if number[0] != "0" and not GiftCard.objects.filter(number_hash=hash_number(number)).exists() \
                and not StaffCard.objects.filter(number=number).exists():
            return number


class GiftCardEvent(models.Model):
    """One line of a gift card's history: made, used on an order, money back, adjusted, frozen and so on."""
    ISSUE, USE, REFUND, ADJUST, NOTE = "issue", "use", "refund", "adjust", "note"
    KINDS = ((ISSUE, "Activated"), (USE, "Used"), (REFUND, "Money back"), (ADJUST, "Adjusted"), (NOTE, "Note"))
    card = models.ForeignKey(GiftCard, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=6, choices=KINDS)
    amount = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=7, decimal_places=2, default=0, help_text="Left after it")
    order = models.ForeignKey("pos_server.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=120, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["at", "id"]


class GiftCardAuthorization(models.Model):
    """A gift card paying for an order (kept when an empty card is deleted: `card` goes blank)."""
    card = models.ForeignKey(GiftCard, on_delete=models.SET_NULL, null=True, blank=True)
    order = models.ForeignKey("pos_server.Order", on_delete=models.CASCADE, related_name="gift_card_auth")
    charged_balance = models.DecimalField(decimal_places=2, max_digits=7)

class CardPreset(models.Model):
    """
    CardPreset model represents a preset configuration for gift cards.
    Attributes:
        name (str): The name of the card preset. It is an optional field with a maximum length of 64 characters.
        amount (Decimal): The amount associated with the card preset. It is a required field with a maximum of 5 digits and 2 decimal places.
        image (ImageField): An optional image associated with the card preset. The image is uploaded to 'gift-cards/presets'.
    Methods:
        __str__(): Returns the name of the card preset as its string representation.
    """
    name = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(null = False, max_digits=5, decimal_places=2)
    image = models.ImageField(upload_to='gift-cards/presets', null=True)

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
            if not StaffCard.objects.filter(number=number).exists() and not GiftCard.objects.filter(number_hash=hash_number(number)).exists():
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
