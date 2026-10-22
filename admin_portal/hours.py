"""
Store hours (design: AdminHours): the weekly hours with breaks, the cutoffs for pickup,
delivery and the kitchen's last call, ordering ahead, and pausing online orders (holidays and other changes are Events). One form with Save: every change
posts the draft back (op=preview or a button's op) and the page shows it, the week chart and "Right now" included.
Pausing and resuming act right away. The hours themselves are worked out in events/hours.py.
"""
import datetime

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from events.hours import DELIVERY, DINE, PICKUP, Hours, cut, duration, ranges_text, text as clock, week_ranges
from events.models import StoreHours

DAYS = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
SHORT = [_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")]
LAST = 1680  # a period can end up to 4 AM the next day
# (key, lowest, highest, step)
STEPS = {"pickup_after": (0, 120, 5), "pickup_before": (0, 120, 5), "delivery_after": (0, 120, 5), "delivery_before": (0, 120, 5),
         "last_call": (0, 60, 5), "ahead_days": (0, 14, 1), "lead": (5, 120, 5)}
SWITCHES = ("around_breaks", "ahead")
COPY = {"all": range(7), "week": range(5), "end": (5, 6)}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def draft_of(cfg=None) -> dict:
    cfg = cfg or StoreHours.load()
    return {"week": [{"open": bool(d.get("open")), "r": sorted([int(a), int(b)] for a, b in d.get("r", []))} for d in cfg.week],
            **{k: getattr(cfg, k) for k in STEPS}, **{k: getattr(cfg, k) for k in SWITCHES}}


def from_post(post) -> dict:
    week = []
    for i in range(7):
        starts, ends = post.getlist(f"d{i}-from"), post.getlist(f"d{i}-to")
        r = [[min(max(_int(a), 0), 1425), min(max(_int(b), 15), LAST)] for a, b in zip(starts, ends)]
        week.append({"open": post.get(f"d{i}-open") == "1", "r": sorted(r)})
    out = {"week": week, **{k: post.get(k) == "1" for k in SWITCHES}}
    for key, (lo, hi, _step) in STEPS.items():
        out[key] = min(max(_int(post.get(key), lo), lo), hi)
    return out


def copy_days(draft, post) -> str:
    """A day's "Copy to…" picker: its hours onto other days. Returns the toast."""
    for i in range(7):
        target = post.get(f"copy-{i}", "")
        if not target:
            continue
        day = draft["week"][i]
        if not day["open"] or day_errors(day):
            return ""
        days = [k for k, d in enumerate(draft["week"]) if d["open"]] if target == "open" else list(COPY.get(target, ()))
        for k in days:
            draft["week"][k] = {"open": True, "r": [list(r) for r in day["r"]]}
        n = len([k for k in days if k != i])
        return ngettext("%(day)s’s hours copied to %(n)d other day", "%(day)s’s hours copied to %(n)d other days", n) % {"day": DAYS[i], "n": n}
    return ""


def apply_op(draft, op) -> dict:
    name, _sep, arg = op.partition(":")
    week = draft["week"]
    if name == "day" and 0 <= _int(arg, -1) < 7:
        day = week[_int(arg)]
        day["open"] = not day["open"]
        if day["open"] and not day["r"]:
            day["r"] = [[480, 1260]]
    elif name == "add" and 0 <= _int(arg, -1) < 7:
        day = week[_int(arg)]
        last = max((b for _a, b in day["r"]), default=None)
        start = min(last + 60, 1620) if last is not None else 480
        if last is None or last < LAST - 30:
            day["r"].append([start, min(start + 180, LAST)])
    elif name == "rm":
        i, _s, j = arg.partition(":")
        day = week[_int(i)] if 0 <= _int(i, -1) < 7 else None
        if day and len(day["r"]) > 1 and 0 <= _int(j, -1) < len(day["r"]):
            day["r"].pop(_int(j))
    elif name in ("less", "more") and arg in STEPS:
        lo, hi, step = STEPS[arg]
        draft[arg] = min(max(draft[arg] + (step if name == "more" else -step), lo), hi)
    elif name == "toggle" and arg in SWITCHES:
        draft[arg] = not draft[arg]
    for day in week:
        day["r"].sort()
    return draft


# ---------- checks ----------

def day_errors(day) -> list[str]:
    if not day["open"]:
        return []
    out = []
    if not day["r"]:
        out.append(_("Add at least one period, or switch the day off."))
    for a, b in day["r"]:
        if b <= a:
            out.append(_("%(a)s to %(b)s ends before it starts.") % {"a": clock(a), "b": clock(b)})
    rs = sorted(day["r"])
    for k in range(1, len(rs)):
        if rs[k][0] < rs[k - 1][1]:
            out.append(_("%(a)s overlaps the period before it.") % {"a": clock(rs[k][0])})
    return out


def problem_count(draft) -> int:
    return sum(len(day_errors(d)) for d in draft["week"])


def is_dirty(draft) -> bool:
    return draft != draft_of()


def save(draft):
    cfg = StoreHours.load()
    cfg.week = [{"open": d["open"], "r": [list(r) for r in sorted(d["r"])]} for d in draft["week"]]
    for key in (*STEPS, *SWITCHES):
        setattr(cfg, key, draft[key])
    cfg.save()


def pause(minutes, now=None) -> datetime.datetime:
    """Pause online orders for `minutes`, or (0) until the store closes (an hour if it's closed)."""
    now = now or timezone.now()
    cfg = StoreHours.load()
    if minutes:
        until = now + datetime.timedelta(minutes=minutes)
    else:
        current = Hours(cfg).current(now)
        until = current[1] if current else now + datetime.timedelta(hours=1)
    cfg.paused_until = until
    cfg.save(update_fields=["paused_until"])
    return until


def resume():
    StoreHours.objects.filter(pk=1).update(paused_until=None)


# ---------- the page ----------

def _hours_of(draft) -> Hours:
    """The draft as an Hours (with the real pause), so the chart and "Right now" show what saving would do."""
    saved = StoreHours.load()
    cfg = StoreHours(week=draft["week"], paused_until=saved.paused_until, **{k: draft[k] for k in (*STEPS, *SWITCHES)})
    return Hours(cfg)


def _options(start, end):
    return [(v, clock(v) + (" " + _("(next day)") if v >= 1440 else "")) for v in range(start, end + 1, 15)]


def _step(value, key, label, unit="min"):
    lo, hi, _s = STEPS[key]
    if value:
        shown = ngettext("%(n)d day", "%(n)d days", value) % {"n": value} if unit == "days" else _("%(n)d min") % {"n": value}
    else:
        shown = _("Today only") if unit == "days" else _("Right away")
    return {"key": key, "label": label, "value": shown, "less_off": value <= lo, "more_off": value >= hi}


def page(draft, now=None) -> dict:
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    hours = _hours_of(draft)
    errors = problem_count(draft)
    dirty = is_dirty(draft)

    # weekly rows
    days, week_minutes = [], 0
    for i, d in enumerate(draft["week"]):
        rs = d["r"]
        total = sum(max(0, b - a) for a, b in rs) if d["open"] else 0
        week_minutes += total
        breaks = len(rs) - 1
        sub = (duration(total) + (" · " + ngettext("%(n)d break", "%(n)d breaks", breaks) % {"n": breaks} if breaks else "")) if total else _("No hours")
        ranges = []
        for j, (a, b) in enumerate(rs):
            gap = a - rs[j - 1][1] if j else 0
            ranges.append({"j": j, "a": a, "b": b, "len": duration(b - a) if b > a else "",
                           "break": _("Break %(a)s to %(b)s · %(len)s") % {"a": clock(rs[j - 1][1]), "b": clock(a), "len": duration(gap)} if j and gap > 0 else ""})
        last = rs[-1][1] if rs else None
        days.append({"i": i, "name": DAYS[i], "open": d["open"], "sub": sub if d["open"] else _("Closed"), "ranges": ranges if d["open"] else [],
                     "hidden": [] if d["open"] else rs, "errs": day_errors(d), "add_off": last is not None and last >= LAST - 30})

    # the chart: open hours, and when pickup and delivery orders can be placed
    open_days = [d["r"] for d in draft["week"] if d["open"] and d["r"]]
    lo = min((r[0][0] for r in open_days), default=480)
    hi = max((max(b for _a, b in r) for r in open_days), default=1260)
    x0, x1 = lo // 120 * 120, -(-hi // 120) * 120
    x1 = max(x1, x0 + 120)

    def pct(m):
        return f"{(m - x0) / (x1 - x0) * 100:.2f}%"

    def wpct(a, b):
        return f"{(b - a) / (x1 - x0) * 100:.2f}%"

    ticks = [{"label": clock(m).replace(":00", ""), "left": pct(m)} for m in range(x0, x1 + 1, 120)]
    chart = []
    for i, d in enumerate(draft["week"]):
        rs = week_ranges(draft["week"], i)
        bars = []
        for a, b in rs:
            if b > a:
                bars.append({"kind": "store", "left": pct(a), "width": wpct(a, b), "title": _("Open %(a)s to %(b)s") % {"a": clock(a), "b": clock(b)}})
        for kind, after, before, label in (("pick", draft["pickup_after"], draft["pickup_before"], _("Pickup orders")),
                                           ("deliv", draft["delivery_after"], draft["delivery_before"], _("Delivery orders"))):
            for a, b in cut(rs, after, before, draft["around_breaks"]):
                bars.append({"kind": kind, "left": pct(a), "width": wpct(a, b), "title": f"{label} {clock(a)} – {clock(b)}"})
        chart.append({"label": SHORT[i], "open": bool(rs), "bars": bars})
    now_minute = now.hour * 60 + now.minute
    now_line = {"top": now.weekday() * 34, "left": pct(now_minute)} if x0 <= now_minute <= x1 else None

    # right now
    current = hours.current(now)
    dine = hours.current(now, DINE)
    nxt = hours.next_start(now, days=1)
    nxt = nxt if nxt and timezone.localtime(nxt).date() == today else None
    paused = hours.paused_until(now)
    today_events = hours.events_on(today)

    def at(dt):
        return clock(timezone.localtime(dt).hour * 60 + timezone.localtime(dt).minute)

    def channel(label, ch, kind):
        win = hours.current(now, ch)
        upcoming = next((a for a, _b in hours.on_day(today, ch) if a > now), None)
        value = _("Paused") if paused else _("Until %(t)s") % {"t": at(win[1])} if win else _("From %(t)s") % {"t": at(upcoming)} if upcoming else _("Done for today")
        return {"label": label, "kind": kind, "value": value, "state": "paused" if paused else "on" if win else "off"}

    if today_events:
        sub = _("Today: %(events)s") % {"events": ", ".join(e.title for e in today_events)}
    elif current and nxt:
        sub = _("Break after %(a)s, back at %(b)s") % {"a": at(current[1]), "b": at(nxt)}
    else:
        sub = _("%(day)s hours") % {"day": DAYS[today.weekday()]}
    today_ranges = hours.ranges(today)
    if today_ranges:
        store_line = _("Open today %(hours)s") % {"hours": ranges_text(today_ranges)}
    else:
        store_line = _("Closed today")
        if draft["ahead"]:
            tomorrow_open = bool(hours.ranges(today + datetime.timedelta(days=1)))
            store_line += " · " + (_("order ahead for tomorrow") if tomorrow_open else _("order ahead for later"))
    right_now = {
        "clock": clock(now_minute), "open": bool(current),
        "head": _("Open · closes at %(t)s") % {"t": at(current[1])} if current else _("Closed · opens at %(t)s") % {"t": at(nxt)} if nxt else _("Closed for today"),
        "sub": sub, "store_line": store_line,
        "lines": [{"label": _("Dine-in kitchen"), "kind": "store", "state": "on" if dine else "off",
                   "value": _("Last call %(t)s") % {"t": at(dine[1] - datetime.timedelta(minutes=draft["last_call"]))} if dine else "—"},
                  channel(_("Pickup orders"), PICKUP, "pick"), channel(_("Delivery orders"), DELIVERY, "deliv")]}

    # cutoffs
    first = next((sorted(d["r"]) for d in draft["week"] if d["open"] and d["r"]), [[480, 1260]])
    ex_open, ex_close = first[0][0], first[-1][1]
    cutoffs = [
        {"name": _("Pickup and online orders"), "kind": "pick",
         "example": _("e.g. %(a)s to %(b)s") % {"a": clock(ex_open + draft["pickup_after"]), "b": clock(ex_close - draft["pickup_before"])},
         "rows": [_step(draft["pickup_after"], "pickup_after", _("Start taking orders after opening")),
                  _step(draft["pickup_before"], "pickup_before", _("Stop taking orders before closing"))]},
        {"name": _("Delivery orders"), "kind": "deliv",
         "example": _("e.g. %(a)s to %(b)s") % {"a": clock(ex_open + draft["delivery_after"]), "b": clock(ex_close - draft["delivery_before"])},
         "rows": [_step(draft["delivery_after"], "delivery_after", _("Start taking orders after opening")),
                  _step(draft["delivery_before"], "delivery_before", _("Stop taking orders before closing"))]},
        {"name": _("Dine-in"), "kind": "store", "example": _("e.g. last call %(t)s") % {"t": clock(ex_close - draft["last_call"])},
         "rows": [_step(draft["last_call"], "last_call", _("Kitchen last call before closing"))]},
    ]
    warnings = []
    if draft["delivery_before"] < draft["pickup_before"]:
        warnings.append(_("Deliveries stop later than pickup. Drivers usually need more time, not less."))
    gaps = [rs[k][0] - rs[k - 1][1] for d in draft["week"] if d["open"] for rs in [sorted(d["r"])] for k in range(1, len(rs))]
    if draft["around_breaks"] and any(g < 60 and draft["delivery_after"] + draft["delivery_before"] >= g * 2 for g in gaps):
        warnings.append(_("With a short break, delivery stays off for a long stretch around it. Turn off “Apply cutoffs around breaks” if that’s too much."))

    if errors:
        status, kind = ngettext("%(n)d thing to fix", "%(n)d things to fix", errors) % {"n": errors}, "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    return {
        "d": draft, "status": status, "status_kind": kind, "can_save": dirty and not errors,
        "days": days, "week_total": _("Open %(t)s a week") % {"t": duration(week_minutes)},
        "from_opts": _options(0, 1425), "to_opts": _options(15, LAST),
        "copy_opts": [("", _("Copy to…")), ("all", _("Every day")), ("week", _("Mon to Fri")), ("end", _("Sat and Sun")), ("open", _("Open days"))],
        "ticks": ticks, "chart": chart, "now_line": now_line,
        "now": right_now, "paused": at(paused) if paused else "",
        "pauses": [(15, _("15 min")), (30, _("30 min")), (60, _("1 hour")), (0, _("Rest of day"))],
        "cutoffs": cutoffs, "warnings": warnings,
        "ahead_rows": [_step(draft["ahead_days"], "ahead_days", _("How far ahead"), "days"),
                       _step(draft["lead"], "lead", _("Soonest a pickup can be ready"))],
        "switches": [(k, draft[k]) for k in SWITCHES], "steps": [(k, draft[k]) for k in STEPS],
        "tz": settings.TIME_ZONE,
    }
