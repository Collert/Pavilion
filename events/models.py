from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Event(models.Model):
    title = models.CharField(max_length=120)
    start = models.DateTimeField()
    end = models.DateTimeField()
    location_title = models.CharField(max_length=120, null=True, blank=True)
    location_address = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    restaurant_open = models.BooleanField(default=True)
    in_person_open = models.BooleanField()
    online_open = models.BooleanField()
    delivery_open = models.BooleanField()


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


class SpecialHours(models.Model):
    """A date whose hours replace the week's: closed, or open `opens` to `closes` (minutes, as in StoreHours.week)."""
    date = models.DateField(unique=True)
    closed = models.BooleanField(default=True)
    opens = models.PositiveSmallIntegerField(default=480)
    closes = models.PositiveSmallIntegerField(default=1080)
    note = models.CharField(max_length=200, blank=True, help_text="Shown on the online store")

    class Meta:
        ordering = ["date"]
        verbose_name_plural = _("special hours")

    def __str__(self):
        return f"{self.date}: {self.note or ('closed' if self.closed else 'special hours')}"
