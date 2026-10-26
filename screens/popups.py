"""
Pop-ups: widgets of kind "popup" that a screen shows over its layout when their trigger fires (see registry.py). A
screen's pop-ups are on `Screen.popups` ([{"widget": name, "params": {...}}]); each is rendered as its own live region
(layout.render_popups), present only while it's showing, with a hidden data-ends so the page refetches when it should
go away (or, for scheduled ones, appear).

Moments (an order ready, a staff announcement) are recorded in the cache and nudge open screens ("popups"); scheduled
ones (a menu starting, an event coming up) are worked out from the schedule at render time.

Each pop-up's `context` is fn(request, params, now) -> {"show": bool, "until": datetime, "next": datetime or None, ...}
("until" is when it hides, "next" when to look again); the rest goes to its template as `data`.
"""
import datetime

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext

from pavilion.realtime import publish

EVENTS_KEY = "screens:popup-events"
KEEP_SECONDS = 15 * 60


def record(trigger, data, session=None):
    """Something a pop-up waits for just happened: remember it a while and nudge open screens."""
    now = timezone.now().timestamp()  # the clock render time uses (time.time() can run ahead of it on Windows)
    events = [e for e in cache.get(EVENTS_KEY, []) if now - e["at"] < KEEP_SECONDS][-40:]
    events.append({"trigger": trigger, "data": data, "at": now, "session": session})
    cache.set(EVENTS_KEY, events, KEEP_SECONDS)
    publish("popups")


def latest(trigger, session=None):
    """The newest recorded moment for `trigger` (for this session, when given), or None."""
    return next((e for e in reversed(cache.get(EVENTS_KEY, [])) if e["trigger"] == trigger
                 and (session is None or e.get("session") == session)), None)


def _moment(trigger, params, now, session=None):
    event = latest(trigger, session)
    if event is None:
        return {"show": False}
    start = datetime.datetime.fromtimestamp(event["at"], tz=datetime.timezone.utc)
    until = start + datetime.timedelta(seconds=params.get("seconds", 6))
    return {"show": start - datetime.timedelta(seconds=1) <= now < until, "until": until, **event["data"]}


# ---------- the pop-ups ----------

def order_ready(request, params, now):
    return _moment("order_ready", params, now)


def announcement(request, params, now):
    return _moment("announcement", params, now)


def menu_change(request, params, now):
    """While a scheduled menu has just started (for `seconds`), and when the next one starts."""
    from pos_server.menu_schedule import Schedule, minute_of_week
    from pos_server.models import Menu
    menus = list(Menu.objects.filter(draft=False))
    schedule = Schedule(menus)
    local = timezone.localtime(now)
    minute = minute_of_week(local)
    week = 7 * 1440
    started, upcoming = None, None
    for menu in menus:
        if not menu.auto:
            continue
        for a, _b in schedule.segments(menu):
            since = (minute - a) % week  # minutes since this segment started
            ago = since * 60 + local.second
            if ago < params.get("seconds", 8) and schedule.is_live(menu, now):
                started = menu
            wait = (a - minute) % week or week
            upcoming = wait if upcoming is None else min(upcoming, wait)
    nxt = (local.replace(second=0, microsecond=0) + datetime.timedelta(minutes=upcoming)) if upcoming else None
    if started is None:
        return {"show": False, "next": nxt}
    start = local.replace(second=0, microsecond=0)
    return {"show": True, "until": start + datetime.timedelta(seconds=params.get("seconds", 8)), "next": nxt, "menu": started}


def event_reminder(request, params, now):
    """For `seconds`, `before` minutes ahead of the next event on Store › Events."""
    from events.models import Event
    local = timezone.localtime(now)
    soon = []
    for e in Event.objects.filter(end_date__gte=local.date(), start_date__lte=local.date() + datetime.timedelta(days=2)):
        day = max(e.start_date, local.date())
        while day <= min(e.end_date, local.date() + datetime.timedelta(days=2)):
            start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min)) + datetime.timedelta(minutes=0 if e.all_day else e.opens)
            show = start - datetime.timedelta(minutes=params.get("before", 30))
            soon.append((show, start, e))
            day += datetime.timedelta(days=1)
    soon.sort(key=lambda x: x[0])
    seconds = datetime.timedelta(seconds=params.get("seconds", 10))
    current = next(((show, start, e) for show, start, e in soon if show <= now < show + seconds), None)
    upcoming = next((show for show, _s, _e in soon if show > now), None)
    if current is None:
        return {"show": False, "next": upcoming}
    show, start, e = current
    return {"show": True, "until": show + seconds, "next": upcoming, "event": e, "starts": start}


def order_summary(request, params, now):
    """The paired register's order while it takes payment, on this session's display only (screens/views.py: summary)."""
    session = getattr(request, "screen_session", None)
    data = cache.get(f"screens:order-summary:{session.code}") if session else None
    if not data:
        return {"show": False}
    return {"show": True, "until": now + datetime.timedelta(minutes=15), **_order(data)}


def _order(data):
    def money(cents):
        return f"${cents / 100:,.2f}"
    lines = [{**line, "price": money(line["total"])} for line in data["lines"]]
    totals = [(gettext("Subtotal"), money(data["subtotal"]), "")]
    if data.get("discount"):
        totals.append((gettext("Discount"), "−" + money(data["discount"]), ""))
    if data.get("tax"):
        totals.append((gettext("Tax"), money(data["tax"]), ""))
    totals.append((gettext("Total"), money(data["total"]), "total"))
    over = " · ".join(filter(None, [gettext("Order #%(n)s") % {"n": data["number"]} if data.get("number") else "",
                                     gettext("To go") if data.get("to_go") else gettext("For here")]))
    return {"lines": lines, "totals": totals, "count": sum(line["qty"] for line in data["lines"]), "over": over,
            "name": data.get("name", "")}


SAMPLES = {  # what the editor's Preview shows
    "order_ready": lambda: {"name": "Priya S.", "number": 142},
    "announcement": lambda: {"text": "Fresh cardamom buns just came out of the oven."},
    "menu_change": lambda: {"menu": type("M", (), {"title": "Happy hour"})()},
    "order_summary": lambda: _order({"number": "0142", "name": "Maya", "to_go": False, "subtotal": 3135, "discount": 0,
                                      "tax": 157, "total": 3292, "lines": [
                                          {"title": "Smash burger", "qty": 2, "mods": "No onion · Extra sauce", "total": 2900},
                                          {"title": "Iced latte", "qty": 1, "mods": "Oat milk", "total": 235}]}),
    "event_reminder": lambda: {"event": type("E", (), {"title": "Trivia night", "description": "Teams of up to six. Winners eat free."})(),
                               "starts": timezone.localtime() + datetime.timedelta(minutes=30)},
}
