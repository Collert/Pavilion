"""
The team's schedule (designs: AdminShifts, AdminShiftPars, AdminRequests): who works when, how many people each role
needs, and when people can't work. Times of day are minutes since midnight (0-1440), dates are the day they start on.

- Shift: one person (or nobody yet: an open shift) working one role on one day. Changes stay unpublished until the
  admin publishes them (staff are told then); a published shift that's deleted is kept as `deleted` until then.
- ParSet: how many people each role needs, by hour: the weekly pars, and seasonal ones that add to them or replace
  them between two dates (team/schedule.py works out a day's needs).
- Availability: when someone can work, by weekday, from a date (until a date or for good).
- TimeOff: days (or part of a day) someone is off. Both are asked for and approved on Availability requests.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Shift(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="shifts",
                             help_text="Empty: an open shift")
    role = models.ForeignKey("users.Role", null=True, on_delete=models.SET_NULL, related_name="shifts")
    date = models.DateField()
    start = models.PositiveSmallIntegerField(help_text="Minutes since midnight")
    end = models.PositiveSmallIntegerField(help_text="Minutes since midnight, up to 1440")
    break_minutes = models.PositiveSmallIntegerField(default=0, help_text="Unpaid")
    note = models.CharField(max_length=80, blank=True, help_text="Shows on their schedule")
    published = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False, help_text="Deleted after it was published: gone once published")
    published_as = models.JSONField(null=True, blank=True, help_text="What staff were last told (who, role, date, times)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "start", "id"]

    def __str__(self):
        return f"{self.date} {self.start}-{self.end} {self.user or 'open'}"

    @property
    def paid_minutes(self) -> int:
        return max(0, self.end - self.start - self.break_minutes)


class ParSet(models.Model):
    WEEKLY, SEASON = "weekly", "season"
    ADD, REPLACE = "add", "replace"
    kind = models.CharField(max_length=6, choices=[(WEEKLY, _("Weekly")), (SEASON, _("Seasonal"))], default=SEASON)
    name = models.CharField(max_length=40)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    mode = models.CharField(max_length=7, choices=[(ADD, _("Add on top")), (REPLACE, _("Replace"))], default=ADD)
    days = models.JSONField(default=list, blank=True, help_text="Weekdays it applies on (0 = Monday)")
    pars = models.JSONField(default=dict, blank=True, help_text='{"0": {"<role id>": [[from, to, people], ...]}, ...} by weekday')

    class Meta:
        ordering = ["kind", "start", "id"]

    def __str__(self):
        return self.name


class Availability(models.Model):
    """When someone can work, by weekday. The latest approved one in effect on a date counts."""
    PENDING, APPROVED, DECLINED = "pending", "approved", "declined"
    STATUSES = [(PENDING, _("Pending")), (APPROVED, _("Approved")), (DECLINED, _("Declined"))]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="availability")
    windows = models.JSONField(default=dict, help_text='{"0": [[from, to], ...], ...} by weekday; a missing or empty day: not available')
    effective = models.DateField(default=timezone.localdate)
    until = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=8, choices=STATUSES, default=PENDING)
    submitted_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.CharField(max_length=300, blank=True, help_text="The manager's reply")

    class Meta:
        ordering = ["-effective", "-id"]


class TimeOff(models.Model):
    PENDING, APPROVED, DECLINED = Availability.PENDING, Availability.APPROVED, Availability.DECLINED
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_off")
    first_day = models.DateField()
    last_day = models.DateField()
    all_day = models.BooleanField(default=True)
    start = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Part of a day: minutes since midnight")
    end = models.PositiveSmallIntegerField(null=True, blank=True)
    category = models.CharField(max_length=20, blank=True, help_text="Vacation, Appointment, Personal, Leave...")
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=8, choices=Availability.STATUSES, default=PENDING)
    submitted_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-first_day", "-id"]
