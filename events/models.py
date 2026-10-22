from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Event(models.Model):
    """
    A change to the usual hours (design: AdminEvents), on every day from `start_date` to `end_date`: open for an event
    (extra hours) or closed, for the channels ticked (dine-in, pickup, delivery), from `opens` to `closes` (minutes from
    that day's midnight, past 1440 = after midnight) or all day (closures only). Holidays are closures too.
    events/hours.py lays them over Store hours; closing wins where they overlap. `banner` shows it across the top of
    the online store from `banner_days` before until it ends.
    """
    OPEN, CLOSED = "open", "closed"
    KINDS = [(OPEN, _("Open for an event")), (CLOSED, _("Close the store"))]

    kind = models.CharField(max_length=6, choices=KINDS, default=OPEN)
    title = models.CharField(max_length=120)
    description = models.TextField(null=True, blank=True, help_text="What customers see")
    start_date = models.DateField()
    end_date = models.DateField()
    all_day = models.BooleanField(default=False, help_text="Closed all day (closures only)")
    opens = models.PositiveSmallIntegerField(default=540)
    closes = models.PositiveSmallIntegerField(default=1260)
    dine_in = models.BooleanField(default=True, help_text="Dine-in opens (or closes)")
    pickup = models.BooleanField(default=True, help_text="Pickup orders open (or close)")
    delivery = models.BooleanField(default=False, help_text="Delivery orders open (or close)")
    banner = models.BooleanField(default=True, help_text="A banner on the online store")
    banner_days = models.PositiveSmallIntegerField(default=7, help_text="Days before it starts that the banner shows")
    location_title = models.CharField(max_length=120, null=True, blank=True)
    location_address = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["start_date", "opens"]

    def __str__(self):
        return self.title

    def covers(self, day) -> bool:
        return self.start_date <= day <= self.end_date

    def closes_everything(self) -> bool:
        return self.kind == self.CLOSED and self.dine_in and self.pickup and self.delivery

    def span(self) -> tuple[int, int]:
        """(from, to) minutes on each of its days."""
        return (0, 1440) if self.all_day and self.kind == self.CLOSED else (self.opens, self.closes)


class EventTemplate(Event):
    pass


def _default_week():
    return [{"open": True, "r": [[480, 1260]]} for _n in range(7)]


class StoreHours(models.Model):
    """
    The store's weekly hours and ordering rules (one row; edited on the admin's Store hours page, worked out in
    events/hours.py). `week` is Monday first: {"open": bool, "r": [[from, to], ...]} in minutes from midnight, where
    `to` past 1440 is after midnight (still that day's hours). A switched-off day keeps its ranges for when it's back on.
    """
    week = models.JSONField(default=_default_week)
    pickup_after = models.PositiveSmallIntegerField(default=15, validators=[MaxValueValidator(120)], help_text="Minutes after opening before pickup orders start")
    pickup_before = models.PositiveSmallIntegerField(default=20, validators=[MaxValueValidator(120)], help_text="Minutes before closing that pickup orders stop")
    delivery_after = models.PositiveSmallIntegerField(default=30, validators=[MaxValueValidator(120)])
    delivery_before = models.PositiveSmallIntegerField(default=45, validators=[MaxValueValidator(120)])
    last_call = models.PositiveSmallIntegerField(default=15, validators=[MaxValueValidator(60)], help_text="Kitchen last call, minutes before closing")
    around_breaks = models.BooleanField(default=True, help_text="Apply the cutoffs around breaks too, not only the day's first opening and last closing")
    ahead = models.BooleanField(default=True, help_text="Customers can schedule orders for later, even while the store is closed")
    ahead_days = models.PositiveSmallIntegerField(default=3, validators=[MaxValueValidator(14)], help_text="How many days ahead (0 = today only)")
    lead = models.PositiveSmallIntegerField(default=20, help_text="Soonest a scheduled pickup can be ready, in minutes")
    paused_until = models.DateTimeField(null=True, blank=True, help_text="Online orders are paused until then")

    class Meta:
        verbose_name = _("store hours")
        verbose_name_plural = _("store hours")

    def __str__(self):
        return str(_("Store hours"))

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]
