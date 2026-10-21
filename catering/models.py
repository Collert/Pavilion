"""
Catering: big pre-ordered orders for events (food from dishes with catering pricing, plus services, condiments and
disposables). They never reach the register, the KDS or the order boards: the admin's Catering page (admin_portal/
catering.py) answers them. A new order is accepted (the card on file is charged), rejected, or sent back with
proposed changes the customer accepts or declines on their order page (catering/views.py). Actions live in
catering/flow.py, money in catering/pricing.py, and the card on file in catering/payments.py.
"""
import datetime
import math
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _all_days():
    return [True] * 7


class CateringSettings(models.Model):
    """How the store takes catering orders (one row; use CateringSettings.load())."""
    HOURS, DAYS = "hours", "days"
    HOLDS = [(0, _("on the event day")), (1, _("the day before")), (2, _("2 days before"))]

    on = models.BooleanField(default=True, help_text="Customers can order catering online")
    lead = models.PositiveSmallIntegerField(default=7, help_text="Minimum notice, in lead_unit")
    lead_unit = models.CharField(max_length=5, choices=[(HOURS, _("Hours")), (DAYS, _("Days"))], default=DAYS)
    max_ahead = models.PositiveSmallIntegerField(default=120, help_text="Book up to this many days ahead")
    big_on = models.BooleanField(default=True, help_text="Ask for more notice when an order ties up the kitchen")
    big_hours = models.PositiveSmallIntegerField(default=8, help_text="One more day of notice for every this many hours of kitchen time")
    rush = models.BooleanField(default=True, help_text="Customers can ask for less notice (it comes in flagged)")
    min_subtotal = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("250"))
    days = models.JSONField(default=_all_days, help_text="Event days, Monday first")
    earliest = models.TimeField(default=datetime.time(7, 0))
    latest = models.TimeField(default=datetime.time(22, 0))
    pickup = models.BooleanField(default=True)
    delivery = models.BooleanField(default=True)
    full_service = models.BooleanField(default=True)
    hold_days = models.PositiveSmallIntegerField(default=1, choices=HOLDS, help_text="Days before the event to hold deposits")
    policy = models.TextField(blank=True, help_text="Cancellation policy, shown at checkout")
    auto_reply = models.TextField(blank=True, help_text="Sent when an order comes in")

    class Meta:
        verbose_name = _("catering settings")
        verbose_name_plural = _("catering settings")

    def __str__(self):
        return "Catering settings"

    @classmethod
    def load(cls) -> "CateringSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row

    @property
    def lead_days(self) -> float:
        return float(self.lead) if self.lead_unit == self.DAYS else self.lead / 24

    def extra_days(self, kitchen_minutes) -> int:
        """Days of notice added for an order needing `kitchen_minutes` of kitchen time."""
        if not self.big_on or not self.big_hours:
            return 0
        return int(kitchen_minutes // 60 // self.big_hours)

    def notice_days(self, kitchen_minutes=0) -> float:
        return self.lead_days + self.extra_days(kitchen_minutes)


class CateringOrder(models.Model):
    NEW, WAITING, CONFIRMED, DONE, REJECTED, CANCELLED = "new", "waiting", "confirmed", "done", "rejected", "cancelled"
    STATUSES = [(NEW, _("New")), (WAITING, _("Waiting on customer")), (CONFIRMED, _("Confirmed")), (DONE, _("Completed")),
                (REJECTED, _("Rejected")), (CANCELLED, _("Cancelled"))]
    OPEN = (NEW, WAITING, CONFIRMED)
    PICKUP, DELIVERY, FULL = "pickup", "delivery", "full"
    FULFILMENT = [(PICKUP, _("Pickup")), (DELIVERY, _("Delivery")), (FULL, _("Full service"))]
    HELD, RELEASED, KEPT = "held", "released", "kept"
    HOLD_STATES = [("", _("Not held")), (HELD, _("Held")), (RELEASED, _("Released")), (KEPT, _("Kept"))]

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, help_text="The customer's order page link")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="catering_orders")
    name = models.CharField(max_length=120)
    organization = models.CharField(max_length=120, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    title = models.CharField(max_length=140, help_text="What the event is, e.g. Office lunch")
    event_date = models.DateField()
    event_time = models.TimeField()
    fulfilment = models.CharField(max_length=8, choices=FULFILMENT, default=PICKUP)
    address = models.CharField(max_length=240, blank=True, help_text="Delivery and full service: where the event is")
    note = models.TextField(blank=True, help_text="From the customer")
    status = models.CharField(max_length=10, choices=STATUSES, default=NEW)
    rush = models.BooleanField(default=False, help_text="Asked for less notice than the minimum")
    placed_at = models.DateTimeField(default=timezone.now)
    proposed_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=60, blank=True, help_text="Why it was rejected or cancelled")
    previous = models.JSONField(null=True, blank=True, help_text="The lines before the changes waiting on the customer")
    # The card on file (Square customer + card); ids starting "demo:" are seed_demo's and never reach Square
    square_customer_id = models.CharField(max_length=64, blank=True)
    square_card_id = models.CharField(max_length=64, blank=True)
    card_brand = models.CharField(max_length=24, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    payment_id = models.CharField(max_length=64, blank=True)
    charged = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    refunded = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    hold_payment_id = models.CharField(max_length=64, blank=True)
    hold_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    hold_state = models.CharField(max_length=8, choices=HOLD_STATES, blank=True)

    class Meta:
        ordering = ["event_date", "event_time", "id"]

    def __str__(self):
        return f"{self.number} {self.title}"

    @property
    def number(self) -> str:
        return f"C-{1000 + (self.id or 0)}"

    @property
    def first_name(self) -> str:
        return (self.name.split() or [""])[0]

    @property
    def card(self) -> str:
        if not self.card_last4:
            return _("No card on file")
        return f"{self.card_brand.title() or _('Card')} •• {self.card_last4}"

    @property
    def days_ahead(self) -> int:
        """Days between ordering and the event."""
        return (self.event_date - timezone.localtime(self.placed_at).date()).days

    def hold_on(self, hold_days) -> datetime.date:
        return self.event_date - datetime.timedelta(days=hold_days)


class CateringLine(models.Model):
    DISH, SERVICE, CONDIMENT, DISPOSABLE = "dish", "service", "condiment", "disposable"
    KINDS = [(DISH, _("Dish")), (SERVICE, _("Service")), (CONDIMENT, _("Condiment")), (DISPOSABLE, _("Disposable"))]
    FIELDS = {DISH: "dish", SERVICE: "service", CONDIMENT: "condiment", DISPOSABLE: "disposable"}

    order = models.ForeignKey(CateringOrder, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=10, choices=KINDS)
    dish = models.ForeignKey("pos_server.Dish", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey("services.Service", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    condiment = models.ForeignKey("pos_server.Condiment", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    disposable = models.ForeignKey("pos_server.Disposable", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField(default=1, help_text="Portions, people, units or items")
    amount = models.PositiveIntegerField(default=0, help_text="Hourly and daily services: hours or days")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"),
                                help_text="Condiments and disposables: what the customer pays each (others use the catalog)")
    position = models.PositiveSmallIntegerField(default=0)
    booking = models.OneToOneField("services.Booking", null=True, blank=True, on_delete=models.SET_NULL, related_name="catering_line")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.quantity}× {self.item}"

    @property
    def ref(self):
        return getattr(self, self.FIELDS[self.kind] + "_id")

    @property
    def item(self):
        return getattr(self, self.FIELDS[self.kind])


class CateringMessage(models.Model):
    """One step of an order's conversation (the timeline on both sides)."""
    CUSTOMER, STAFF, SYSTEM = "customer", "staff", "system"
    PLACED, CHANGES, ACCEPTED, REJECTED, CANCELLED, MESSAGE, AGREED, DECLINED = (
        "placed", "changes", "accepted", "rejected", "cancelled", "message", "agreed", "declined")
    KINDS = [(PLACED, _("Placed")), (CHANGES, _("Proposed changes")), (ACCEPTED, _("Accepted")), (REJECTED, _("Rejected")),
             (CANCELLED, _("Cancelled")), (MESSAGE, _("Message")), (AGREED, _("Accepted the changes")),
             (DECLINED, _("Declined the changes"))]

    order = models.ForeignKey(CateringOrder, on_delete=models.CASCADE, related_name="messages")
    who = models.CharField(max_length=8, choices=[(CUSTOMER, _("Customer")), (STAFF, _("Staff")), (SYSTEM, _("System"))])
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=10, choices=KINDS, default=MESSAGE)
    text = models.TextField(blank=True)
    reason = models.CharField(max_length=60, blank=True)
    changes = models.JSONField(default=list, blank=True, help_text="[[text, 'add'|'rm'|'ch'], ...]")
    was_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    now_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "id"]

    def __str__(self):
        return f"{self.order.number} {self.kind}"


def ceil_days(days: float) -> int:
    return int(math.ceil(days))
