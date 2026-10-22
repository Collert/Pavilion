"""
The store's opening hours (design: AdminHours): StoreHours' week, SpecialHours dates that replace it, Events laid
over that (extra hours for the channels an event takes; closures, which win where they overlap), the cutoffs that
decide when pickup and delivery orders can be placed, and pausing online orders. Minutes count from a day's midnight
and a range can end past 1440 (after midnight, still that day's hours).

Everything that asks "are we open?" comes here: the store's status and hours (online_store/storefront.py), checkout's
schedule (online_store/pages.py) and the admin's Store hours page (admin_portal/hours.py).
"""
import datetime

from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Event, SpecialHours, StoreHours

OPEN, DINE, PICKUP, DELIVERY = "open", "dine", "pickup", "delivery"


def merge(ranges) -> list[tuple[int, int]]:
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract(ranges, holes) -> list[tuple[int, int]]:
    out = list(ranges)
    for c, d in holes:
        nxt = []
        for a, b in out:
            if d <= a or c >= b:
                nxt.append((a, b))
                continue
            if a < c:
                nxt.append((a, c))
            if d < b:
                nxt.append((d, b))
        out = nxt
    return out


def takes(event, channel) -> bool:
    """An "open" event adds hours to this channel."""
    if channel == OPEN:
        return event.dine_in or event.pickup or event.delivery
    return {DINE: event.dine_in, PICKUP: event.pickup, DELIVERY: event.delivery}[channel]


def closes(event, channel) -> bool:
    """A "closed" event takes hours away from this channel."""
    if event.scope == Event.ALL:
        return True
    if event.scope == Event.ONLINE:
        return channel in (PICKUP, DELIVERY)
    return channel == DELIVERY


def cut(ranges, after, before, around_breaks=True) -> list[tuple[int, int]]:
    """Ranges with cutoffs: `after` minutes off each start and `before` off each end, or only off the day's first
    start and last end when the cutoffs don't apply around breaks."""
    rs = sorted(ranges)
    if around_breaks:
        out = [(a + after, b - before) for a, b in rs]
    else:
        out = [(a + after if k == 0 else a, b - before if k == len(rs) - 1 else b) for k, (a, b) in enumerate(rs)]
    return [(a, b) for a, b in out if b > a]


def week_ranges(week, weekday) -> list[tuple[int, int]]:
    day = week[weekday]
    return sorted((int(a), int(b)) for a, b in day.get("r", [])) if day.get("open") else []


class Hours:
    """The hours as saved (or a draft's, from the admin page): ranges per day and channel, as minutes or datetimes."""

    def __init__(self, cfg=None, specials=None, events=None):
        self.cfg = cfg or StoreHours.load()
        self._specials = specials  # {date: SpecialHours}, loaded on first use
        self._events = events  # [Event], loaded on first use

    @property
    def events(self) -> list:
        if self._events is None:
            self._events = list(Event.objects.filter(end_date__gte=timezone.localdate() - datetime.timedelta(days=2)))
        return self._events

    def events_on(self, day) -> list:
        return [e for e in self.events if e.covers(day)]

    @property
    def specials(self) -> dict:
        if self._specials is None:
            since = timezone.localdate() - datetime.timedelta(days=1)
            self._specials = {s.date: s for s in SpecialHours.objects.filter(date__gte=since)}
        return self._specials

    def special(self, day):
        return self.specials.get(day)

    def usual(self, day) -> list[tuple[int, int]]:
        """The day's hours before events: its special hours, else the week's."""
        sp = self.special(day)
        if sp is not None:
            return [] if sp.closed else [(sp.opens, sp.closes)]
        return week_ranges(self.cfg.week, day.weekday())

    def ranges(self, day, channel=OPEN) -> list[tuple[int, int]]:
        """The day's hours for a channel (OPEN = open at all; DINE, PICKUP and DELIVERY), events and cutoffs included."""
        rs = self.usual(day)
        added = [e.span() for e in self.events_on(day) if e.kind == Event.OPEN and takes(e, channel)]
        rs = merge(rs + added) if added else rs
        tomorrow = day + datetime.timedelta(days=1)
        holes = [e.span() for e in self.events_on(day) if e.kind == Event.CLOSED and closes(e, channel)]
        holes += [(a + 1440, b + 1440) for e in self.events_on(tomorrow) if e.kind == Event.CLOSED and closes(e, channel)
                  for a, b in [e.span()]]
        if holes:
            rs = subtract(rs, holes)
        c = self.cfg
        if channel == PICKUP:
            return cut(rs, c.pickup_after, c.pickup_before, c.around_breaks)
        if channel == DELIVERY:
            return cut(rs, c.delivery_after, c.delivery_before, c.around_breaks)
        return rs

    def spans(self, since, until, channel=OPEN) -> list[tuple[datetime.datetime, datetime.datetime]]:
        """The open (or ordering) times between two aware datetimes, clipped to them, including the hours a
        day before runs past midnight."""
        tz = timezone.get_current_timezone()
        out = []
        day = timezone.localtime(since).date() - datetime.timedelta(days=1)
        last = timezone.localtime(until).date()
        while day <= last:
            midnight = datetime.datetime.combine(day, datetime.time.min)
            for a, b in self.ranges(day, channel):
                start = timezone.make_aware(midnight + datetime.timedelta(minutes=a), tz)
                end = timezone.make_aware(midnight + datetime.timedelta(minutes=b), tz)
                if end > since and start < until:
                    out.append((max(start, since), min(end, until)))
            day += datetime.timedelta(days=1)
        return sorted(out)

    def on_day(self, day, channel=OPEN):
        """Spans within the calendar date `day` (midnight to midnight)."""
        tz = timezone.get_current_timezone()
        since = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min), tz)
        return self.spans(since, since + datetime.timedelta(days=1), channel)

    def current(self, now=None, channel=OPEN):
        """The span `now` is in (unclipped to the day), or None."""
        now = now or timezone.now()
        return next(((a, b) for a, b in self._around(now, channel) if a <= now < b), None)

    def next_start(self, now=None, channel=OPEN, days=8):
        now = now or timezone.now()
        return next((a for a, _b in self.spans(now, now + datetime.timedelta(days=days), channel) if a > now), None)

    def _around(self, now, channel):
        tz = timezone.get_current_timezone()
        out = []
        for day in (timezone.localtime(now).date() - datetime.timedelta(days=1), timezone.localtime(now).date()):
            midnight = datetime.datetime.combine(day, datetime.time.min)
            for a, b in self.ranges(day, channel):
                out.append((timezone.make_aware(midnight + datetime.timedelta(minutes=a), tz),
                            timezone.make_aware(midnight + datetime.timedelta(minutes=b), tz)))
        return out

    def paused_until(self, now=None):
        """When online orders are back, while they're paused."""
        until = self.cfg.paused_until
        return until if until and until > (now or timezone.now()) else None


def text(minutes) -> str:
    """7 AM, 3:30 PM, 12 AM for midnight (and past it)."""
    from online_store.storefront import clock
    m = int(minutes) % 1440
    return clock(datetime.time(m // 60, m % 60))


def ranges_text(ranges) -> str:
    return ", ".join(f"{text(a)} – {text(b)}" for a, b in ranges)


def duration(minutes) -> str:
    h, m = divmod(int(minutes), 60)
    return " ".join(filter(None, [_("%(n)d h") % {"n": h} if h else "", _("%(n)d min") % {"n": m} if m else ""])) or _("0 min")
