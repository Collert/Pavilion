"""
Services: things the store sells that aren't food (staff for an event, equipment rental, the venue, delivery and setup,
classes, extras like a cake), and the bookings made for them. Edited in the admin's Services page
(admin_portal/services.py). Service.quote() works out what a booking costs; confirmed catering orders keep a booking
per service line (catering/flow.py), and the counter will book them later.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

CENT = Decimal("0.01")


class Service(models.Model):
    STAFF, EQUIPMENT, VENUE, DELIVERY, EXPERIENCE, EXTRA = "staff", "equipment", "venue", "delivery", "experience", "extra"
    CATEGORIES = [
        (STAFF, _("Staffing")),
        (EQUIPMENT, _("Equipment rental")),
        (VENUE, _("Venue")),
        (DELIVERY, _("Delivery and setup")),
        (EXPERIENCE, _("Experiences")),
        (EXTRA, _("Extras")),
    ]
    FLAT, HOUR, PERSON, DAY, UNIT = "flat", "hour", "person", "day", "unit"
    PRICING = [(FLAT, _("Flat fee")), (HOUR, _("Per hour")), (PERSON, _("Per guest")), (DAY, _("Per day")), (UNIT, _("Per item"))]
    # The pricing a category starts on when it's picked in the editor
    CATEGORY_PRICING = {STAFF: HOUR, EQUIPMENT: DAY, VENUE: FLAT, DELIVERY: FLAT, EXPERIENCE: PERSON, EXTRA: UNIT}
    # Category details kept in `details`: {category: {key: default}}
    DETAILS = {
        STAFF: {"staff": 1, "gratuity": 0},  # people on the roster, automatic gratuity %
        EQUIPMENT: {"owned": 1},  # units owned
        VENUE: {"seated": 20, "standing": 30, "min_spend": 0, "hours": 0},  # capacity, food and drink minimum $, included hours
        DELIVERY: {"radius": 10, "per_km": 0},  # km covered, $ for each extra km
        EXPERIENCE: {"capacity": 10, "minutes": 90},
        EXTRA: {},
    }
    INCLUDES = [_("Tables and chairs"), _("Linens"), _("House sound system"), _("TV for slides"), _("Dedicated manager"),
                _("Cleanup"), _("Coat check")]
    STORE_TAX, NO_TAX = "store", "none"
    TAXES = [(STORE_TAX, _("Store tax rate")), (NO_TAX, _("No tax"))]
    FLEXIBLE, MODERATE, STRICT = "flexible", "moderate", "strict"
    CANCELLATION = [
        (FLEXIBLE, _("Flexible · free until 24 h before")),
        (MODERATE, _("Moderate · free until 3 days before")),
        (STRICT, _("Strict · 50% fee within 14 days")),
    ]
    PER_UNIT, PER_BOOKING = "unit", "booking"

    title = models.CharField(max_length=140)
    description = models.TextField(blank=True, help_text="What the customer sees when booking")
    category = models.CharField(max_length=12, choices=CATEGORIES, default=STAFF)
    pricing = models.CharField(max_length=8, choices=PRICING, default=HOUR)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    minimum = models.PositiveIntegerField(default=0, help_text="Fewest hours, guests, days or items charged (not for flat fees)")
    tax = models.CharField(max_length=8, choices=TAXES, default=STORE_TAX)
    details = models.JSONField(default=dict, blank=True, help_text="The category's details, keys in Service.DETAILS")
    includes = models.JSONField(default=list, blank=True, help_text="Venues: what the booking includes (Service.INCLUDES)")
    deposit = models.BooleanField(default=False, help_text="Equipment: hold a refundable security deposit")
    deposit_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    deposit_per = models.CharField(max_length=8, choices=[(PER_UNIT, _("Per unit")), (PER_BOOKING, _("Once per booking"))], default=PER_UNIT)
    lead_days = models.PositiveIntegerField(default=2, help_text="Book at least this many days ahead")
    cancellation = models.CharField(max_length=10, choices=CANCELLATION, default=MODERATE)
    needs_approval = models.BooleanField(default=False, help_text="A manager accepts or declines each request")
    at_counter = models.BooleanField(default=True, help_text="Can be booked at the counter (POS)")
    online = models.BooleanField(default=True, help_text="Can be booked online")
    catering = models.BooleanField(default=True, help_text="Can be added to catering orders")
    active = models.BooleanField(default=True, help_text="Can be booked at all")
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def detail(self, key):
        """A category detail as a Decimal (missing ones take their default)."""
        default = self.DETAILS.get(self.category, {}).get(key, 0)
        try:
            return Decimal(str(self.details.get(key, default)))
        except (ArithmeticError, ValueError):
            return Decimal(str(default))

    @property
    def counts_units(self):
        """Staffing and equipment are booked for a number of people or units as well."""
        return self.category in (self.STAFF, self.EQUIPMENT)

    def quote(self, amount=1, count=1, price=None):
        """
        What a booking costs: `amount` hours/guests/days/items (ignored for a flat fee; the minimum is charged when it's
        more) for `count` people or units (staffing and equipment only), at `price` (default: the service's own; a
        catering order keeps the price it was made at). Returns {quantity, subtotal, gratuity, tax,
        total, deposit, min_spend, notes}; the deposit is refundable and not taxed, the venue's food and drink minimum
        is spent on the catering order, not added here.
        """
        amount, count = max(int(amount or 1), 1), max(int(count or 1), 1) if self.counts_units else 1
        quantity = 1 if self.pricing == self.FLAT else max(amount, self.minimum)
        subtotal = ((self.price if price is None else Decimal(price)) * quantity * count).quantize(CENT)
        gratuity = (subtotal * self.detail("gratuity") / 100).quantize(CENT) if self.category == self.STAFF else Decimal("0")
        rate = Decimal(str(getattr(settings, "TAX_RATE", "0"))) if self.tax == self.STORE_TAX else Decimal("0")
        tax = ((subtotal + gratuity) * rate).quantize(CENT)
        deposit = Decimal("0")
        if self.category == self.EQUIPMENT and self.deposit:
            deposit = self.deposit_amount * (count if self.deposit_per == self.PER_UNIT else 1)
        notes = []
        if self.pricing != self.FLAT and amount < self.minimum:
            notes.append(_("Below the minimum, so it’s charged as %(min)s.") % {"min": self.amount_text(self.minimum)})
        limit = {self.STAFF: ("staff", _("Only %(n)s people on the roster.")), self.EQUIPMENT: ("owned", _("Only %(n)s units owned."))}.get(self.category)
        if limit and self.detail(limit[0]) and count > self.detail(limit[0]):
            notes.append(limit[1] % {"n": int(self.detail(limit[0]))})
        if self.category == self.EXPERIENCE and self.detail("capacity") and amount > self.detail("capacity"):
            notes.append(_("More than the %(n)s-guest limit.") % {"n": int(self.detail("capacity"))})
        return {"quantity": quantity, "count": count, "subtotal": subtotal, "gratuity": gratuity, "rate": rate, "tax": tax,
                "total": subtotal + gratuity + tax, "deposit": deposit,
                "min_spend": self.detail("min_spend") if self.category == self.VENUE else Decimal("0"), "notes": notes}

    def amount_text(self, n):
        """'4 hours', '1 guest', '2 days', '10 items' for this service's pricing."""
        words = {
            self.HOUR: lambda n: ngettext("%(n)d hour", "%(n)d hours", n),
            self.PERSON: lambda n: ngettext("%(n)d guest", "%(n)d guests", n),
            self.DAY: lambda n: ngettext("%(n)d day", "%(n)d days", n),
            self.UNIT: lambda n: ngettext("%(n)d item", "%(n)d items", n),
        }
        return words[self.pricing](n) % {"n": n} if self.pricing in words else ""


class Booking(models.Model):
    """One booking of a service (a catering order or a customer's request)."""
    PENDING, CONFIRMED, DECLINED, CANCELLED = "pending", "confirmed", "declined", "cancelled"
    STATUSES = [(PENDING, _("Needs approval")), (CONFIRMED, _("Confirmed")), (DECLINED, _("Declined")), (CANCELLED, _("Cancelled"))]
    COUNTER, ONLINE, CATERING = "counter", "online", "catering"
    CHANNELS = [(COUNTER, _("At the counter")), (ONLINE, _("Online")), (CATERING, _("Catering order"))]

    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="bookings")
    name = models.CharField(max_length=140, help_text="Who it's for: the person, family or organisation")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="service_bookings")
    date = models.DateField(help_text="The day of the event (the first day, for rentals)")
    amount = models.PositiveIntegerField(default=1, help_text="Hours, guests, days or items, by the service's pricing")
    count = models.PositiveIntegerField(default=1, help_text="People or units (staffing and equipment)")
    status = models.CharField(max_length=10, choices=STATUSES, default=CONFIRMED)
    channel = models.CharField(max_length=10, choices=CHANNELS, default=CATERING)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"), help_text="Quoted total with tax")
    deposit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"), help_text="Refundable deposit held")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["date", "id"]

    def __str__(self):
        return f"{self.service} · {self.name} · {self.date}"

    LIVE = (PENDING, CONFIRMED)
