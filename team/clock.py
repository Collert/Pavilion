"""
The time clock (team/clock_views.py, the screen at /team/clock): staff clock in, take unpaid breaks and clock out.
Each stretch of work is a TimeEntry (with TimeBreaks); the Shift it's for is the plan.

Clocking in is fine from EARLY minutes before a published shift until it ends. Earlier than that, or with no shift
today, a manager has to approve (people who can approve clock-ins approve their own; the time clock screen does that) (someone whose groups give "Approve clock-ins", `act.clock`, types their PIN), and
with no shift a Shift is added for them (`TimeEntry.added_shift`; it starts when they clock in and ends when they
clock out). Clocking out adds the time worked to their Staff profile.
"""
import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.formats import time_format
from django.utils.translation import gettext as _

from users.access import can
from users.models import Staff

from .models import Shift, TimeBreak, TimeEntry
from .schedule import snapshot

EARLY = 60          # minutes before a shift they can clock in without a manager
ADDED_LENGTH = 240  # an added shift's planned length until they clock out


class ClockError(Exception):
    pass


def minutes(dt) -> int:
    local = timezone.localtime(dt)
    return local.hour * 60 + local.minute


def clock_text(minute) -> str:
    return time_format(datetime.time(min(minute, 1439) // 60, min(minute, 1439) % 60), "g:i A")


def open_entry(user):
    return TimeEntry.objects.filter(user=user, clock_out__isnull=True).prefetch_related("breaks").first()


def state(user) -> str:
    """"out", "in" or "break"."""
    entry = open_entry(user)
    if entry is None:
        return "out"
    return "break" if any(b.end is None for b in entry.breaks.all()) else "in"


def shifts_today(user, now=None):
    now = now or timezone.now()
    return list(Shift.objects.filter(user=user, date=timezone.localdate(now), deleted=False, published=True).select_related("role"))


def check_in(user, now=None) -> dict:
    """What clocking in now would be: {"ok", "shift" (the one it's for, or None), "reason" (why a manager has to
    approve), "later" (a shift later today)}."""
    now = now or timezone.now()
    at = minutes(now)
    shifts = shifts_today(user, now)
    current = next((s for s in shifts if s.start - EARLY <= at < s.end), None)
    if current:
        return {"ok": True, "shift": current, "reason": "", "later": None}
    later = next((s for s in sorted(shifts, key=lambda s: s.start) if s.start > at), None)
    if later:
        return {"ok": False, "shift": later, "later": later,
                "reason": _("Your shift starts at %(time)s. Clocking in more than an hour early needs a manager.") % {"time": clock_text(later.start)}}
    return {"ok": False, "shift": None, "later": None,
            "reason": _("You’re not on the schedule now. A manager has to approve, and a shift is added for you.")}


def approvers(exclude=None):
    """Who can approve a clock-in here: active staff with a register PIN whose groups give "Approve clock-ins"."""
    people = Staff.objects.filter(user__is_active=True, user__is_staff=True).exclude(pin="").select_related("user")
    return [s for s in people.order_by("user__first_name", "user__username") if can(s.user, "act.clock") and s.user_id != getattr(exclude, "id", None)]


@transaction.atomic
def clock_in(user, now=None, approver=None) -> TimeEntry:
    now = now or timezone.now()
    if open_entry(user):
        raise ClockError(_("You’re already clocked in."))
    plan = check_in(user, now)
    if not plan["ok"] and approver is None:
        raise ClockError(plan["reason"])
    shift, added = plan["shift"], False
    if shift is None:
        start = minutes(now) // 5 * 5
        profile = Staff.objects.filter(user=user).first()
        role = profile.roles.filter(archived=False).first() if profile else None
        shift = Shift(user=user, role=role, date=timezone.localdate(now), start=start, end=min(start + ADDED_LENGTH, 1440),
                      note=_("Added at the time clock")[:80], published=True)
        shift.published_as = snapshot(shift)
        shift.save()
        added = True
    entry = TimeEntry.objects.create(user=user, shift=shift, clock_in=now, approved_by=approver, added_shift=added)
    from gift_cards import staff_cards  # their "every shift" staff cards refill
    staff_cards.clocked_in(user, now)
    return entry


def start_break(user, now=None) -> TimeBreak:
    now = now or timezone.now()
    entry = open_entry(user)
    if entry is None:
        raise ClockError(_("Clock in first."))
    if any(b.end is None for b in entry.breaks.all()):
        raise ClockError(_("You’re already on a break."))
    return TimeBreak.objects.create(entry=entry, start=now)


def end_break(user, now=None):
    now = now or timezone.now()
    entry = open_entry(user)
    if entry is None or not entry.breaks.filter(end__isnull=True).update(end=now):
        raise ClockError(_("You’re not on a break."))


@transaction.atomic
def clock_out(user, now=None) -> TimeEntry:
    now = now or timezone.now()
    entry = open_entry(user)
    if entry is None:
        raise ClockError(_("You’re not clocked in."))
    entry.breaks.filter(end__isnull=True).update(end=now)
    entry.clock_out = now
    entry.save(update_fields=["clock_out"])
    entry = TimeEntry.objects.prefetch_related("breaks").get(pk=entry.pk)
    if entry.added_shift and entry.shift:  # the added shift follows what happened
        shift = entry.shift
        same_day = timezone.localdate(now) == shift.date
        shift.end = max(shift.start + 5, minutes(now) if same_day else 1440)
        shift.break_minutes = int(entry.break_seconds(now) // 60)
        shift.published_as = snapshot(shift)
        shift.save(update_fields=["end", "break_minutes", "published_as"])
    profile, _created = Staff.objects.get_or_create(user=user)
    profile.minutes_worked = (profile.minutes_worked or 0) + int(entry.worked_seconds(now) // 60)
    profile.last_worked = now
    profile.save(update_fields=["minutes_worked", "last_worked"])
    return entry


def today(user, now=None) -> list[dict]:
    """Today's punches, oldest first: [{"what", "at"}]."""
    now = now or timezone.now()
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for entry in TimeEntry.objects.filter(user=user, clock_in__gte=start).prefetch_related("breaks").order_by("clock_in"):
        out.append({"what": _("Clocked in"), "at": entry.clock_in, "kind": "in"})
        for b in entry.breaks.all():
            out.append({"what": _("Break"), "at": b.start, "kind": "break", "until": b.end})
        if entry.clock_out:
            out.append({"what": _("Clocked out"), "at": entry.clock_out, "kind": "out"})
    return out


def worked_today(user, now=None) -> int:
    """Seconds worked today (breaks left out)."""
    now = now or timezone.now()
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(sum(e.worked_seconds(now) for e in TimeEntry.objects.filter(user=user, clock_in__gte=start).prefetch_related("breaks")))


def on_now():
    """Who's clocked in right now: [(TimeEntry, on break)]."""
    entries = TimeEntry.objects.filter(clock_out__isnull=True).select_related("user", "shift__role").prefetch_related("breaks").order_by("clock_in")
    return [(e, any(b.end is None for b in e.breaks.all())) for e in entries]
