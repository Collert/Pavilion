"""
Events (design: AdminEvents): a month calendar over the usual hours, what's coming up, and the selected event: open
for an event (extra hours) or close the store (holidays, training, a short-staffed shift), for the channels ticked
(dine-in, pickup, delivery), when, what changes on its next days, and the online store's banner. One form with Save; picking,
adding and deleting events are part of the draft until it's saved. events/hours.py lays saved events over Store hours.

    draft = {"events": [{"id": 3 or "", "kind", "name", "desc", "start": "2026-11-04", "end", "all_day", "from", "to",
                         "dine", "pickup", "delivery", "banner", "lead"}],
             "sel": index or -1, "month": "2026-11", "gone": [ids deleted]}
"""
import calendar
import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from events.hours import Hours, text as clock
from events.models import Event

CHANNELS = [("dine", _("Dine-in")), ("pickup", _("Pickup")), ("delivery", _("Delivery"))]
LEADS = [(1, _("1 day before")), (3, _("3 days before")), (5, _("5 days before")), (7, _("1 week before")), (14, _("2 weeks before"))]
SHORT_DAYS = [_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")]
FIELDS = ("id", "kind", "name", "desc", "start", "end", "was", "all_day", "from", "to", "dine", "pickup", "delivery", "banner", "lead")
HISTORY_DAYS = 400  # older events stay as they are and aren't in the draft


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _row(e: Event) -> dict:
    return {"id": e.id, "kind": e.kind, "name": e.title, "desc": e.description or "", "start": e.start_date.isoformat(),
            "end": e.end_date.isoformat(), "all_day": e.all_day, "from": e.opens, "to": e.closes, "dine": e.dine_in,
            "pickup": e.pickup, "delivery": e.delivery, "banner": e.banner, "lead": e.banner_days}


def draft_of() -> dict:
    today = timezone.localdate()
    events = [_row(e) for e in Event.objects.filter(end_date__gte=today - datetime.timedelta(days=HISTORY_DAYS))]
    upcoming = sorted(((x["start"] if x["start"] >= today.isoformat() else today.isoformat(), k) for k, x in enumerate(events) if x["end"] >= today.isoformat()))
    return {"events": events, "sel": upcoming[0][1] if upcoming else -1, "month": today.strftime("%Y-%m"), "gone": []}


def from_post(post) -> dict:
    events = []
    for k in range(min(_int(post.get("count")), 1000)):
        v = {f: post.get(f"ev-{k}-{f}", "") for f in FIELDS}
        start, end, was = _date(v["start"]), _date(v["end"]), _date(v["was"])
        if start and was and end and start != was:  # moving the start keeps the length
            end = start + (end - was)
        start = start or timezone.localdate()
        events.append({"id": _int(v["id"]) or "", "kind": Event.CLOSED if v["kind"] == Event.CLOSED else Event.OPEN,
                       "name": v["name"].strip()[:48], "desc": v["desc"].strip()[:200], "start": start.isoformat(),
                       "end": (end or start).isoformat(), "all_day": v["all_day"] == "1",
                       "from": min(max(_int(v["from"], 540), 0), 1425), "to": min(max(_int(v["to"], 1260), 15), 1680),
                       "dine": v["dine"] == "1", "pickup": v["pickup"] == "1", "delivery": v["delivery"] == "1",
                       "banner": v["banner"] == "1", "lead": _int(v["lead"], 7) if _int(v["lead"], 7) in dict(LEADS) else 7})
    month = post.get("month", "")
    if not _date(month + "-01"):
        month = timezone.localdate().strftime("%Y-%m")
    sel = _int(post.get("sel"), -1)
    return {"events": events, "sel": sel if 0 <= sel < len(events) else -1, "month": month,
            "gone": [_int(x) for x in post.getlist("gone") if _int(x)]}


def _new(day, hours):
    usually_open = bool(hours.usual(day))
    return {"id": "", "kind": Event.CLOSED if usually_open else Event.OPEN, "name": "", "desc": "", "start": day.isoformat(),
            "end": day.isoformat(), "all_day": usually_open, "from": 540, "to": 1260, "dine": True, "pickup": True,
            "delivery": usually_open, "banner": True, "lead": 7}


def _covers(x, day):
    return x["start"] <= day.isoformat() <= x["end"]


def apply_op(draft, op) -> tuple[dict, str]:
    events, k = draft["events"], draft["sel"]
    e = events[k] if 0 <= k < len(events) else None
    name, _sep, arg = op.partition(":")
    toast = ""
    month = _date(draft["month"] + "-01")
    if name == "month":
        if arg == "prev":
            draft["month"] = (month - datetime.timedelta(days=1)).strftime("%Y-%m")
        elif arg == "next":
            draft["month"] = (month + datetime.timedelta(days=32)).strftime("%Y-%m")
        else:
            draft["month"] = timezone.localdate().strftime("%Y-%m")
    elif name == "new":
        events.append(_new(timezone.localdate() + datetime.timedelta(days=7), Hours()))
        draft["sel"] = len(events) - 1
    elif name == "day" and _date(arg):
        day = _date(arg)
        here = [i for i, x in enumerate(events) if _covers(x, day)]
        if here:  # step through the day's events
            draft["sel"] = here[(here.index(k) + 1) % len(here)] if k in here else here[0]
        else:
            events.append(_new(day, Hours()))
            draft["sel"] = len(events) - 1
    elif name == "pick":  # pick:<index>[:<date to show>]
        index, _s, iso = arg.partition(":")
        if 0 <= _int(index, -1) < len(events):
            draft["sel"] = _int(index)
            if _date(iso):
                draft["month"] = _date(iso).strftime("%Y-%m")
    elif e and name == "kind" and arg in (Event.OPEN, Event.CLOSED):
        if e["kind"] != arg:  # each kind starts with its usual channels: closing everything, opening dine-in and pickup
            e["dine"], e["pickup"], e["delivery"] = True, True, arg == Event.CLOSED
        e["kind"] = arg
        if arg == Event.OPEN:
            e["all_day"] = False
    elif e and name == "allday":
        e["all_day"] = not e["all_day"]
    elif e and name == "chan" and arg in ("dine", "pickup", "delivery"):
        e[arg] = not e[arg]
    elif e and name == "banner":
        e["banner"] = not e["banner"]
    elif e and name == "delete":
        if e["id"]:
            draft["gone"].append(e["id"])
        events.pop(k)
        draft["sel"] = -1
        toast = _("%(name)s deleted. Save to keep it.") % {"name": e["name"] or _("Event")}
    return draft, toast


def errors(x) -> list[str]:
    out = []
    if not x["name"]:
        out.append(_("Give the event a name."))
    if x["end"] < x["start"]:
        out.append(_("The end date is before the start."))
    if not (x["kind"] == Event.CLOSED and x["all_day"]) and x["to"] <= x["from"]:
        out.append(_("The end time is before the start time."))
    if not (x["dine"] or x["pickup"] or x["delivery"]):
        out.append(_("Pick at least one way customers can order.") if x["kind"] == Event.OPEN else _("Pick at least one thing to close."))
    return out


def _canonical(draft):
    return sorted((str(x["id"]),) + tuple(str(x[f]) for f in FIELDS if f not in ("id", "was")) for x in draft["events"])


def is_dirty(draft) -> bool:
    return bool(draft["gone"]) or _canonical(draft) != _canonical(draft_of())


@transaction.atomic
def save(draft) -> list:
    """Save the draft; returns the events it added (events/announce.py emails customers about them)."""
    Event.objects.filter(pk__in=draft["gone"]).delete()
    added = []
    for x in draft["events"]:
        values = {"kind": x["kind"], "title": x["name"], "description": x["desc"], "start_date": _date(x["start"]),
                  "end_date": _date(x["end"]), "all_day": x["all_day"] and x["kind"] == Event.CLOSED, "opens": x["from"],
                  "closes": x["to"], "dine_in": x["dine"], "pickup": x["pickup"], "delivery": x["delivery"],
                  "banner": x["banner"], "banner_days": x["lead"]}
        if not (x["id"] and Event.objects.filter(pk=x["id"]).update(**values)):
            added.append(Event.objects.create(**values))
    return added


# ---------- the page ----------

def _days(x, since, until):
    """The event's dates between two dates."""
    a, b = max(_date(x["start"]), since), min(_date(x["end"]), until)
    return [a + datetime.timedelta(days=n) for n in range((b - a).days + 1)] if b >= a else []


def _nice(day, long=False):
    return f"{day:%A, %B} {day.day}" if long else f"{day:%a, %b} {day.day}"


def _span(x):
    return (0, 1440) if x["kind"] == Event.CLOSED and x["all_day"] else (x["from"], x["to"])


def _effects(x, hours, today):
    out = []
    usual_bar = lambda r: {"kind": "usual", "left": _pct(r[0]), "width": _wid(*r), "title": _("Usually open %(a)s–%(b)s") % {"a": clock(r[0]), "b": clock(r[1])}}
    days = _days(x, today, today + datetime.timedelta(days=400))
    for day in days[:3]:
        usual = hours.usual(day)
        bars = [usual_bar(r) for r in usual]
        a, b = (0, 1680) if x["kind"] == Event.CLOSED and x["all_day"] else (x["from"], x["to"])
        if x["kind"] == Event.OPEN:
            bars.append({"kind": "added", "left": _pct(a), "width": _wid(a, b), "title": _("Event %(a)s–%(b)s") % {"a": clock(a), "b": clock(b)}})
            chans = ", ".join(str(label).lower() for key, label in CHANNELS if x[key])
            if not usual:
                text = _("Usually closed. Opens %(a)s–%(b)s for %(chans)s.") % {"a": clock(a), "b": clock(b), "chans": chans}
            elif any(r[0] < b and r[1] > a for r in usual):
                text = _("Already open for part of this. Extends hours to %(a)s–%(b)s.") % {"a": clock(min(a, usual[0][0])), "b": clock(max(b, usual[-1][1]))}
            else:
                text = _("Adds %(a)s–%(b)s on top of usual hours.") % {"a": clock(a), "b": clock(b)}
        else:
            bars.append({"kind": "closed", "left": _pct(a), "width": _wid(a, b), "title": _("Closed %(a)s–%(b)s") % {"a": clock(a), "b": clock(b)}})
            cut = [r for r in usual if r[0] < b and r[1] > a]
            lost = sum(min(r[1], b) - max(r[0], a) for r in cut)
            if not usual:
                text = _("Already closed this day, so nothing changes.")
            elif not cut:
                text = _("You’re not usually open then, so nothing changes.")
            else:
                from events.hours import duration
                if x["dine"] and x["pickup"] and x["delivery"]:
                    text = _("Closes %(t)s of usual hours.") % {"t": duration(lost)}
                else:
                    text = _("Stops %(what)s for %(t)s of usual hours.") % {"what": _chans_text(x), "t": duration(lost)}
        out.append({"date": _nice(day, True), "bars": bars, "text": text})
    more = ""
    if len(days) > 3:
        more = ngettext("+ %(n)d more date", "+ %(n)d more dates", len(days) - 3) % {"n": len(days) - 3}
    elif not days:
        more = _("No upcoming dates. The event is in the past.")
    return out, more


X0, X1 = 360, 1680


def _pct(m):
    return f"{max(0, min(1, (m - X0) / (X1 - X0))) * 100:.1f}%"


def _wid(a, b):
    return f"{max(0, (min(b, X1) - max(a, X0)) / (X1 - X0)) * 100:.1f}%"


def _chans_text(x):
    return _(" and ").join(str(label).lower() for key, label in CHANNELS if x[key])


def _closed_label(x):
    """Closed, or what a closure stops: "No delivery", "No pickup or delivery"."""
    if x["dine"] and x["pickup"] and x["delivery"]:
        return _("Closed")
    return _("No %(what)s") % {"what": _(" or ").join(str(label).lower() for key, label in CHANNELS if x[key])}


def _kind_tag(x):
    return _("Open") if x["kind"] == Event.OPEN else _closed_label(x)


def page(draft) -> dict:
    today = timezone.localdate()
    hours = Hours()
    events, k = draft["events"], draft["sel"]
    bad = sum(1 for x in events if errors(x))
    dirty = is_dirty(draft)
    if bad:
        status, kind = ngettext("%(n)d event needs fixing", "%(n)d events need fixing", bad) % {"n": bad}, "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""

    # the calendar
    first = _date(draft["month"] + "-01")
    grid_start = first - datetime.timedelta(days=first.weekday())
    n_days = calendar.monthrange(first.year, first.month)[1]
    n_cells = -(-(first.weekday() + n_days) // 7) * 7
    cells = []
    for n in range(n_cells):
        day = grid_start + datetime.timedelta(days=n)
        here = [(i, x) for i, x in enumerate(events) if _covers(x, day)]
        usual = bool(hours.usual(day))
        names = ", ".join(x["name"] or _("untitled event") for _i, x in here)
        cells.append({"iso": day.isoformat(), "num": day.day, "in_month": day.month == first.month, "today": day == today,
                      "usual": usual, "on": any(i == k for i, _x in here),
                      "aria": _nice(day, True) + ", " + (_("usually open") if usual else _("usually closed")) + ", " + (names or _("add an event")),
                      "evs": [{"label": x["name"] or _("New event"), "kind": x["kind"], "on": i == k} for i, x in here[:2]],
                      "more": len(here) - 2 if len(here) > 2 else 0})

    # coming up
    horizon = today + datetime.timedelta(days=120)
    ups = []
    for i, x in enumerate(events):
        days = _days(x, today, horizon)
        if not days:
            continue
        d = days[0]
        when = _("All day") if x["kind"] == Event.CLOSED and x["all_day"] else f"{clock(x['from'])}–{clock(x['to'])}"
        length = (_date(x["end"]) - _date(x["start"])).days + 1
        dd = (d - today).days
        rel = _("today") if dd == 0 else _("tomorrow") if dd == 1 else _("in %(n)d days") % {"n": dd}
        ups.append({"k": i, "d": d, "on": i == k, "kind": x["kind"], "name": x["name"] or _("Untitled event"), "tag": _kind_tag(x),
                    "mon": f"{d:%b}".upper(), "day": d.day, "iso": d.isoformat(),
                    "sub": " · ".join([_nice(d), when] + ([ngettext("%(n)d day", "%(n)d days", length) % {"n": length}] if length > 1 else []) + [rel])})
    ups.sort(key=lambda u: u["d"])

    # the selected event
    e = None
    if 0 <= k < len(events):
        x = events[k]
        is_open = x["kind"] == Event.OPEN
        effects, more = _effects(x, hours, today)
        warns = []
        mine = _days(x, today, today + datetime.timedelta(days=400))[:60]
        for i, y in enumerate(events):
            if i == k:
                continue
            theirs = set(_days(y, today, today + datetime.timedelta(days=400)))
            both_timed = not (x["kind"] == Event.CLOSED and x["all_day"]) and not (y["kind"] == Event.CLOSED and y["all_day"])
            hit = next((d for d in mine if d in theirs and (not both_timed or (x["from"] < y["to"] and x["to"] > y["from"]))), None)
            if hit:
                warns.append({"bad": y["kind"] != x["kind"], "text": _("Overlaps “%(name)s” on %(day)s.") % {"name": y["name"] or _("untitled"), "day": _nice(hit)}
                              + " " + (_("Closing wins where they overlap.") if y["kind"] != x["kind"] else _("Check the times."))})
        if not is_open and mine and all(not hours.usual(d) for d in mine[:3]):
            warns.append({"bad": False, "text": _("You’re usually closed on these days already. This only matters if you add hours later.")})
        if is_open and x["delivery"] and x["to"] - x["from"] < 90:
            warns.append({"bad": False, "text": _("Under 90 minutes with delivery on. After cutoffs there may be no time to order.")})
        nxt = (mine[0] if mine else _date(x["start"]))
        if is_open:
            head = x["name"] or _("Event")
        else:
            head = _closed_label(x)
        head += " · " + _nice(nxt) + ("" if x["kind"] == Event.CLOSED and x["all_day"] else f", {clock(x['from'])}–{clock(x['to'])}") + "."
        e = {"x": x, "k": k, "open": is_open, "errs": errors(x),
             "kinds": [(Event.OPEN, _("Open for an event"), _("Extra public hours"), "M12 5v14M5 12h14"),
                       (Event.CLOSED, _("Close the store"), _("Holidays, training, repairs"), "M6 6l12 12M18 6 6 18")],
             "name_ph": _("e.g. Trivia night") if is_open else _("e.g. Closed for Thanksgiving"),
             "desc_ph": _("What’s happening, what’s on the menu") if is_open else _("Why you’re closed and when you’re back"),
             "show_times": is_open or not x["all_day"], "effects": effects, "more": more, "warns": warns,
             "channels": [(key, label, x[key]) for key, label in CHANNELS], "leads": LEADS,
             "banner_hint": _("A strip at the top with the event details") if is_open else _("Warns customers before they try to order"),
             "banner_head": head, "banner_text": x["desc"]}

    return {"d": draft, "status": status, "status_kind": kind, "can_save": dirty and not bad, "count": len(events),
            "month_title": f"{first:%B %Y}", "dows": SHORT_DAYS, "cells": cells, "upcoming": ups, "e": e,
            "from_opts": [(v, clock(v)) for v in range(0, 1440, 15)],
            "to_opts": [(v, clock(v) + (" " + _("(next day)") if v >= 1440 else "")) for v in range(15, 1681, 15)],
            "rows": [{**x, "k": i} for i, x in enumerate(events) if i != k]}
