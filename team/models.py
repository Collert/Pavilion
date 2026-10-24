"""
The team's schedule (designs: AdminShifts, AdminShiftPars, AdminRequests): who works when, how many people each role
needs, and when people can't work. Times of day are minutes since midnight (0-1440), dates are the day they start on.

- Shift: one person (or nobody yet: an open shift) working one role on one day. Changes stay unpublished until the
  admin publishes them (staff are told then); a published shift that's deleted is kept as `deleted` until then.
- ParSet: how many people each role needs, by hour: the weekly pars, and seasonal ones that add to them or replace
  them between two dates (team/schedule.py works out a day's needs).
- Availability: when someone can work, by weekday, from a date (until a date or for good).
- TimeOff: days (or part of a day) someone is off. Both are asked for and approved on Availability requests.
- ShiftOffer: a staff member passing a published shift on, from their account (team/swaps.py): given away (anyone
  with the role can grab it, and it's theirs straight away), or asked of one coworker as a hand-off or a swap for one
  of theirs (the coworker says yes, then a manager approves on Availability requests). Until then it's still theirs.
- TimeEntry and TimeBreak: when someone actually worked, punched at the time clock (team/clock.py): a Shift is the
  plan, a TimeEntry what happened. Clocking in early or off the schedule needs a manager; off the schedule it adds a
  Shift (`added_shift`) whose times follow the entry.
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


class ShiftOffer(models.Model):
    """A shift someone is passing on (team/swaps.py). Only one can be open on a shift at a time."""
    GIVE, HANDOFF, SWAP = "give", "handoff", "swap"
    KINDS = [(GIVE, _("Given away")), (HANDOFF, _("Hand-off")), (SWAP, _("Swap"))]
    OPEN, ASKED, ACCEPTED, DONE, DECLINED, CANCELLED = "open", "asked", "accepted", "done", "declined", "cancelled"
    STATUSES = [(OPEN, _("Up for grabs")), (ASKED, _("Asked")), (ACCEPTED, _("Waiting for a manager")), (DONE, _("Done")),
                (DECLINED, _("Declined")), (CANCELLED, _("Cancelled"))]
    ACTIVE = (OPEN, ASKED, ACCEPTED)
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="offers")
    kind = models.CharField(max_length=7, choices=KINDS)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shift_offers")
    to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="shift_asks",
                           help_text="Hand-offs and swaps: the coworker asked")
    theirs = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                               help_text="Swaps: the coworker's shift they take in return")
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=9, choices=STATUSES, default=OPEN)
    created_at = models.DateTimeField(default=timezone.now)
    answered_at = models.DateTimeField(null=True, blank=True, help_text="When the coworker said yes or no")
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.CharField(max_length=200, blank=True, help_text="The manager's reply")
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                 help_text="Given away: who grabbed it")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.shift} by {self.by}: {self.status}"


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
    handle = models.CharField(max_length=4, blank=True, help_text="On approval, their affected shifts: open (moved to open shifts) or keep")
    affected = models.JSONField(default=list, blank=True, help_text="The shifts it touched when it was decided: [{date, role, color, start, end}]")

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
    message = models.CharField(max_length=300, blank=True, help_text="The manager's reply")
    handle = models.CharField(max_length=4, blank=True, help_text="On approval, their affected shifts: open (moved to open shifts) or keep")
    affected = models.JSONField(default=list, blank=True, help_text="The shifts it touched when it was decided: [{date, role, color, start, end}]")

    class Meta:
        ordering = ["-first_day", "-id"]


class TimeEntry(models.Model):
    """One stretch of work from clocking in to clocking out (team/clock.py), with its unpaid breaks."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_entries")
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL, related_name="time_entries",
                              help_text="The scheduled shift it's for")
    clock_in = models.DateTimeField(default=timezone.now)
    clock_out = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                    help_text="The manager who let them clock in early or off the schedule")
    added_shift = models.BooleanField(default=False, help_text="Its shift was added at the time clock")

    class Meta:
        ordering = ["-clock_in", "-id"]

    def __str__(self):
        return f"{self.user} {self.clock_in:%Y-%m-%d %H:%M}"

    def break_seconds(self, now=None) -> float:
        now = now or timezone.now()
        return sum(((b.end or now) - b.start).total_seconds() for b in self.breaks.all())

    def worked_seconds(self, now=None) -> float:
        """Time clocked in, less unpaid breaks."""
        now = now or timezone.now()
        return max(0.0, ((self.clock_out or now) - self.clock_in).total_seconds() - self.break_seconds(now))


class TimeBreak(models.Model):
    """An unpaid break within a TimeEntry."""
    entry = models.ForeignKey(TimeEntry, on_delete=models.CASCADE, related_name="breaks")
    start = models.DateTimeField(default=timezone.now)
    end = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start", "id"]
