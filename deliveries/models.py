"""
Couriers and the deliveries they take (the courier app is deliveries/views.py).

A delivery order goes to couriers first: it stays off the kitchen display until a courier accepts it (or until no
courier is left to take it, see hidden_order_ids()). Then the restaurant confirms it on the KDS, the kitchen makes it,
and the courier picks it up, drives it over and confirms the drop-off.
"""
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from pos_server.models import Order, OrderStation


class CourierShift(models.Model):
    """A courier who is on shift and taking deliveries. Deleted when they end the shift."""
    WALKING, BICYCLING, DRIVING = "walking", "bicycling", "driving"  # Google's travel modes
    MODES = (
        (WALKING, _("Walking")),
        (BICYCLING, _("Cycling or scooter")),
        (DRIVING, _("Driving")),
    )
    SHORT = {WALKING: _("Walking"), BICYCLING: _("Cycling"), DRIVING: _("Driving")}
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="courier_shift")
    mode = models.CharField(max_length=20, choices=MODES, default=DRIVING)
    started_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user} ({self.mode})"

    def short_mode(self):
        return self.SHORT.get(self.mode, self.get_mode_display())

    def delivered_count(self):
        return Delivery.objects.filter(courier=self.user, delivered_at__gte=self.started_at).count()


def photo_path(instance, filename):
    # A random name: the photo shows someone's door, so its URL mustn't be guessable
    return f"deliveries/{uuid.uuid4().hex}.jpg"


class Delivery(models.Model):
    """
    One delivery order and the courier taking it. Steps (see phase()): open -> confirm (courier accepted, the
    restaurant hasn't confirmed) -> prep -> ready -> enroute (picked up) -> proof (arrived) -> done (delivered).

    If the restaurant rejects the order after a courier accepted it, the order is deleted but this row stays, with
    `order` empty and `declined_reason` set, until the courier has seen it ("declined").
    """
    DOOR, HAND = "door", "hand"
    HANDOFFS = ((DOOR, _("Left at the door")), (HAND, _("Handed to the customer")))

    timestamp = models.DateTimeField(default=timezone.now)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="delivery")
    order_number = models.PositiveIntegerField(default=0, help_text="The order's number, kept after it's rejected")
    customer_name = models.CharField(max_length=140, blank=True)
    destination = models.TextField()
    address_2 = models.TextField(blank=True, null=True)
    instructions = models.TextField(blank=True, null=True)
    phone = models.PositiveBigIntegerField()
    courier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")
    passed_by = models.ManyToManyField(User, blank=True, related_name="passed_deliveries",
                                       help_text="Couriers who passed on this delivery")
    accepted_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    handoff = models.CharField(max_length=10, choices=HANDOFFS, blank=True)
    photo = models.ImageField(upload_to=photo_path, blank=True, help_text="Proof of delivery")
    declined_reason = models.TextField(blank=True, help_text="Why the restaurant rejected the order after a courier took it")
    distance_m = models.PositiveIntegerField(null=True, blank=True, help_text="Driving distance from the restaurant")
    travel_seconds = models.JSONField(default=dict, blank=True, help_text="{travel mode: seconds} from the restaurant")

    class Meta:
        verbose_name_plural = "deliveries"

    def __str__(self):
        return f"Delivery for order {self.order_number}"

    def save(self, *args, **kwargs):
        if self.order_id and not self.order_number:
            self.order_number = self.order_id
        if self.order_id and not self.customer_name:
            self.customer_name = self.order.name or ""
        super().save(*args, **kwargs)

    def phase(self):
        """open, confirm, prep, ready, enroute, proof, done or declined."""
        if self.delivered_at:
            return "done"
        if self.order_id is None:
            return "declined"
        if not self.courier_id:
            return "open"
        if self.arrived_at:
            return "proof"
        if self.picked_up_at:
            return "enroute"
        statuses = self.order.statuses()
        if OrderStation.STATUS_PENDING in statuses:
            return "confirm"
        return "ready" if self.order.all_stations_done() else "prep"

    @property
    def first_name(self):
        return (self.customer_name or "").split(" ")[0] or _("the customer")

    @property
    def street(self):
        """The first part of the address ("2145 W 4th Ave"), with the unit if there is one."""
        street = self.destination.split(",")[0].strip()
        return f"{street}, {self.address_2}" if self.address_2 else street

    @property
    def area(self):
        """The rest of the address ("New Westminster, BC V3M 1A1")."""
        return ", ".join(part.strip() for part in self.destination.split(",")[1:] if part.strip())


def active_delivery(user):
    """The delivery `user` is on (or a declined one they haven't seen yet), or None."""
    return (Delivery.objects.filter(courier=user, delivered_at=None)
            .select_related("order").order_by("accepted_at").first())


def hidden_order_ids():
    """
    Delivery orders the kitchen doesn't see yet: nobody accepted them, and a courier on shift still might. Once
    every courier on shift has passed on one (or nobody is on shift) it shows on the KDS as "No courier" instead,
    so staff can reject it.
    """
    on_shift = set(CourierShift.objects.values_list("user_id", flat=True))
    if not on_shift:
        return []
    passed = {}
    for delivery_id, user_id in Delivery.passed_by.through.objects.filter(
            delivery__courier=None, delivery__order__isnull=False).values_list("delivery_id", "user_id"):
        passed.setdefault(delivery_id, set()).add(user_id)
    return [order_id for delivery_id, order_id in
            Delivery.objects.filter(courier=None, order__isnull=False).values_list("id", "order_id")
            if not on_shift <= passed.get(delivery_id, set())]
