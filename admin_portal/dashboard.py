"""
Numbers for the dashboard home (design: AdminDashboard).

Three ranges: "today" (sales by hour, against the same weekday last week up to the same time) and "7d" / "30d"
(sales by day, against the period before). With `open_only`, days without any orders are skipped in both periods,
so a store that's closed two days a week compares like with like. Everything is worked out from Orders.
"""
import datetime
import math
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy, ngettext

from deliveries.models import CourierShift, Delivery
from pos_server.models import CashDrawerSession, Component, Dish, Menu, Order, OrderItem, OrderStation, Station

from .templatetags.admin_tags import money0
from .units import fmt_qty

RANGES = [("today", gettext_lazy("Today")), ("7d", gettext_lazy("7 days")), ("30d", gettext_lazy("30 days"))]
CHANNELS = [("store", "In person", "#12875A"), ("web", "Online pickup", "#C9821A"), ("delivery", "Delivery", "#4E79B5")]
LOOKBACK_DAYS = 150  # how far back "open days only" looks for enough open days

# Chart canvas, in SVG units (the SVG scales to the card's width)
W, H, LEFT, RIGHT, TOP = 708, 238, 48, 702, 8
BASE = H - 28


def day_start(day: datetime.date) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def channel_label(code):
    return {"store": _("In person"), "web": _("Online pickup"), "delivery": _("Delivery")}.get(code, code)


class Sample:
    """One order, reduced to what the dashboard needs."""
    __slots__ = ("id", "when", "day", "minute", "total", "prep", "channel")

    def __init__(self, row):
        self.id, when, self.total, prep, self.channel = row
        self.when = timezone.localtime(when)
        self.day = self.when.date()
        self.minute = self.when.hour * 60 + self.when.minute
        self.prep = prep.total_seconds() if prep else None


def load(since: datetime.datetime, until: datetime.datetime) -> list[Sample]:
    rows = (Order.objects.filter(timestamp__gte=since, timestamp__lt=until)
            .values_list("id", "timestamp", "total", "prep_time", "channel"))
    return [Sample(row) for row in rows]


def totals(samples) -> dict:
    sales = sum((s.total for s in samples), Decimal("0"))
    preps = [s.prep for s in samples if s.prep]
    count = len(samples)
    return {"sales": sales, "orders": count, "avg": sales / count if count else Decimal("0"),
            "prep": sum(preps) / len(preps) if preps else None}


def pct_change(cur, prev):
    if not prev:
        return None
    return round(float((Decimal(str(cur)) - Decimal(str(prev))) / Decimal(str(prev)) * 100), 1)


def mmss(seconds):
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60:02d}s"


def kpi(label, icon, value, delta, good_when_up, vs):
    if delta is None:
        tone, arrow, text = "flat", None, "—"
    else:
        good = delta >= 0 if good_when_up else delta <= 0
        tone, arrow, text = ("good" if good else "bad"), ("up" if delta >= 0 else "down"), f"{abs(delta):.1f}%"
    return {"label": label, "icon": icon, "value": value, "delta": text, "tone": tone, "arrow": arrow, "vs": vs}


def nice_step(top):
    raw = max(top, 1) / 4
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if m * mag >= raw)


def compact(v):
    if v >= 1000:
        k = v / 1000
        return f"${k:.0f}K" if k == int(k) else f"${k:.1f}K"
    return f"${v:.0f}"


def chart(columns, cur_label, prev_label, title, sub):
    """
    SVG geometry for bars (this period) and a line (the one before). `columns` is a list of dicts with
    `cur`, `prev` (None = no value), `label` (axis), `long` (tooltip title), `partial`, `closed`, `prev_long`.
    """
    values = [float(c["cur"]) for c in columns if c["cur"] is not None] + [float(c["prev"]) for c in columns if c["prev"] is not None]
    step = nice_step(max(values + [1]))
    y_max = step * 4
    n = len(columns)
    band = (RIGHT - LEFT) / n
    bar_w = min(24, band * 0.62)

    def y(v):
        return BASE - (float(v) / y_max) * (BASE - TOP)

    def cx(i):
        return LEFT + band * i + band / 2

    r = lambda v: round(v, 1)  # noqa: E731
    bars, line, dots, pen = [], [], [], False
    for i, c in enumerate(columns):
        if c["cur"] is not None and float(c["cur"]) > 0:
            top, x = y(c["cur"]), cx(i) - bar_w / 2
            rad = min(4, BASE - top, bar_w / 2)
            bars.append({"d": f"M{r(x)} {r(BASE)}V{r(top + rad)}Q{r(x)} {r(top)} {r(x + rad)} {r(top)}H{r(x + bar_w - rad)}"
                              f"Q{r(x + bar_w)} {r(top)} {r(x + bar_w)} {r(top + rad)}V{r(BASE)}Z",
                         "partial": c["partial"]})
        if c.get("closed"):
            bars.append({"closed": True, "x": r(cx(i) - bar_w / 2), "w": r(bar_w), "y": BASE - 4})
        if c["prev"] is None:
            pen = False
            continue
        point = (r(cx(i)), r(y(c["prev"])))
        line.append(("L" if pen else "M") + f"{point[0]} {point[1]}")
        pen = True
        if n <= 15:
            dots.append({"x": point[0], "y": point[1]})
    grid = [{"y": r(y(step * k)), "label_y": r(y(step * k) + 4), "label": compact(step * k)} for k in range(5)]
    labels = [{"x": r(cx(i)), "label": c["label"], "strong": c["partial"]} for i, c in enumerate(columns) if c["label"]]

    def money_or(value, empty):
        return money0(value) if value is not None else empty

    hits = [{"title": c["long"] + (" · " + _("so far") if c["partial"] else ""),
             "cur": money_or(c["cur"], c.get("empty", _("Closed"))), "prev": money_or(c["prev"], _("Closed")),
             "prev_label": c.get("prev_long") or prev_label}
            for c in columns]
    return {"title": title, "sub": sub, "cur_label": cur_label, "prev_label": prev_label,
            "w": W, "h": H, "left": LEFT, "right": RIGHT, "top": TOP, "base": BASE, "label_y": BASE + 20,
            "bars": bars, "line": "".join(line), "dots": dots, "grid": grid, "labels": labels, "hits": hits,
            "hit_left": round(LEFT / W * 100, 3), "hit_right": round((W - RIGHT) / W * 100, 3),
            "hit_bottom": round((H - BASE) / H * 100, 3)}


def hour_label(h):
    return f"{h % 12 or 12}{'a' if h < 12 else 'p'}"


def today_view(now, open_only):
    today = now.date()
    last_week = today - datetime.timedelta(days=7)
    elapsed = now - day_start(today)
    cur = load(day_start(today), now)
    prev_day = load(day_start(last_week), day_start(last_week + datetime.timedelta(days=1)))
    prev_by_now = [s for s in prev_day if s.when - day_start(last_week) <= elapsed]

    hours = [s.when.hour for s in cur + prev_day]
    first, last = min(hours + [7]), max(hours + [21, now.hour])
    by_hour_cur, by_hour_prev = defaultdict(Decimal), defaultdict(Decimal)
    for s in cur:
        by_hour_cur[s.when.hour] += s.total
    for s in prev_day:
        by_hour_prev[s.when.hour] += s.total
    weekday = date_format(now, "l")
    columns = [{"cur": by_hour_cur[h] if h <= now.hour else None, "prev": by_hour_prev[h],
                "label": hour_label(h), "long": f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}",
                "partial": h == now.hour, "empty": _("Not yet")}
               for h in range(first, last + 1)]
    prev_label = _("Last %(weekday)s") % {"weekday": weekday}
    return {
        "current": cur, "previous": prev_by_now,
        "chart": chart(columns, _("Today"), prev_label, _("Sales by hour"), _("Today so far vs the same day last week")),
        "vs": _("vs last %(weekday)s by now") % {"weekday": weekday}, "short": _("Today"),
    }


def days_view(now, n, open_only):
    today = now.date()
    elapsed = now - day_start(today)
    since = today - datetime.timedelta(days=LOOKBACK_DAYS if open_only else 2 * n + 35)
    samples = load(day_start(since), now)
    by_day = defaultdict(list)
    for s in samples:
        by_day[s.day].append(s)

    if open_only:
        open_days = sorted({d for d in by_day} | {today})
        cur_days = open_days[-n:]
        prev_days = open_days[-2 * n:-n]
        prev_days = [None] * (n - len(prev_days)) + prev_days
    else:
        off = math.ceil(n / 7) * 7  # the same weekdays a whole number of weeks earlier
        cur_days = [today - datetime.timedelta(days=n - 1 - i) for i in range(n)]
        prev_days = [d - datetime.timedelta(days=off) for d in cur_days]

    # The last day of this period is today, still going: compare it with its partner up to the same time of day
    def day_samples(day, partner_of_today=False):
        if day is None:
            return []
        rows = by_day.get(day, [])
        if partner_of_today:
            rows = [s for s in rows if s.when - day_start(day) <= elapsed]
        return rows

    cur = [s for d in cur_days for s in day_samples(d)]
    prev = [s for i, d in enumerate(prev_days) for s in day_samples(d, i == n - 1)]

    def label(i, d):
        if d == today:
            return _("Today")
        if n == 7:
            return date_format(d, "D j") if open_only else date_format(d, "D")
        return date_format(d, "M j") if (i % 5 == 4 or i == 0) and i != n - 2 else ""

    columns = []
    for i, (d, p) in enumerate(zip(cur_days, prev_days)):
        rows = day_samples(d)
        closed = not rows and d != today
        columns.append({
            "cur": None if closed else sum((s.total for s in rows), Decimal("0")),
            "prev": (sum((s.total for s in day_samples(p, i == n - 1)), Decimal("0")) if p and by_day.get(p) else None),
            "label": label(i, d), "partial": d == today, "closed": closed and not open_only,
            "long": (_("Today") if d == today else date_format(d, "D, M j")) + (" · " + _("no orders") if closed else ""),
            "prev_long": date_format(p, "D, M j") if p else None, "empty": _("No orders"),
        })
    unit = ngettext("%(n)d open day", "%(n)d open days", n) % {"n": n} if open_only else ngettext("%(n)d day", "%(n)d days", n) % {"n": n}
    closed_count = sum(1 for c in columns if c["closed"])
    if open_only:
        sub = _("Last %(unit)s vs the %(n)d open days before · days without orders hidden") % {"unit": unit, "n": n}
        prev_label = _("Previous %(n)d open days") % {"n": n}
    else:
        before = _("the week before") if n == 7 else _("the same days 5 weeks earlier")
        sub = _("Last %(unit)s vs %(before)s") % {"unit": unit, "before": before}
        if closed_count:
            sub += " · " + ngettext("%(n)d day without orders", "%(n)d days without orders", closed_count) % {"n": closed_count}
        prev_label = _("Week before") if n == 7 else _("5 weeks earlier")
    return {
        "current": cur, "previous": prev,
        "chart": chart(columns, _("This period"), prev_label, _("Sales by day"), sub),
        "vs": _("vs previous period"), "short": _("Last %(unit)s") % {"unit": unit},
    }


def live_panel(now):
    open_orders = list(Order.objects.filter(picked_up=False).prefetch_related("station_statuses"))
    by_channel = Counter(o.channel for o in open_orders)
    channels = [{"label": channel_label(code), "n": by_channel.get(code, 0), "color": color} for code, _l, color in CHANNELS]

    load_rows = dict(OrderStation.objects.filter(order__picked_up=False, status__in=[OrderStation.STATUS_PENDING, OrderStation.STATUS_APPROVED])
                     .values("station").annotate(n=Count("id")).values_list("station", "n"))
    stations = list(Station.objects.all())
    scale = max([8] + list(load_rows.values()))
    kitchen = [{"name": st.name, "n": load_rows.get(st.id, 0), "busy": load_rows.get(st.id, 0) >= 5,
                "width": round(load_rows.get(st.id, 0) / scale * 100)} for st in stations]

    waiting = [o for o in open_orders if not o.all_stations_done()]
    wait = (sum((now - o.timestamp).total_seconds() for o in waiting) / len(waiting) / 60) if waiting else None
    ready = sum(1 for o in open_orders if o.station_statuses.all() and o.all_stations_done())
    out = Delivery.objects.filter(picked_up_at__isnull=False, delivered_at__isnull=True).count()
    couriers = CourierShift.objects.count()
    return {
        "open": len(open_orders), "channels": channels, "stations": kitchen,
        "mini": [
            {"value": _("%(n)d min") % {"n": round(wait)} if wait is not None else "—", "label": _("Average wait")},
            {"value": str(ready), "label": _("Ready for pickup")},
            {"value": f"{out} / {couriers}", "label": _("Couriers out")},
        ],
    }


def top_sellers(samples, limit=5):
    ids = [s.id for s in samples]
    rows = list(OrderItem.objects.filter(order_id__in=ids, parent__isnull=True).values("title")
                .annotate(n=Sum("quantity")).order_by("-n", "title")[:limit])
    top = rows[0]["n"] if rows else 1
    return [{"name": r["title"], "n": r["n"], "width": round(r["n"] / top * 100)} for r in rows]


def channel_mix(samples):
    sums = defaultdict(Decimal)
    for s in samples:
        sums[s.channel] += s.total
    total = sum(sums.values(), Decimal("0"))
    mix = []
    for code, _l, color in CHANNELS:
        share = round(float(sums[code] / total * 100)) if total else 0
        mix.append({"label": channel_label(code), "money": sums[code], "pct": share, "color": color})
    return mix


def alerts(now):
    out = []
    sold_out = (Dish.objects.filter(menu__in=Menu.live_menus(now)).exclude(availability="off")
                .filter(in_stock=False).exclude(availability="always").distinct()[:3])
    for dish in sold_out:
        out.append({"level": "critical", "title": _("%(dish)s sold out") % {"dish": dish.title},
                    "sub": _("A part ran out, so it's off the menu"), "url": reverse("component_availability")})
    for comp in Component.objects.filter(par__gt=0, inventory__lt=F("par")).order_by("title")[:3]:
        out.append({"level": "warning", "title": _("%(item)s below par") % {"item": comp.title},
                    "sub": _("%(left)s left, par is %(par)s") % {"left": fmt_qty(comp.inventory, comp.unit_of_measurement),
                                                              "par": fmt_qty(comp.par, comp.unit_of_measurement)},
                    "url": reverse("crafting")})
    recent = now - datetime.timedelta(days=2)
    for drawer in CashDrawerSession.objects.filter(closed_at__gte=recent).select_related("closed_by", "device"):
        diff = drawer.difference
        if not diff:
            continue
        who = (drawer.closed_by.first_name or drawer.closed_by.username) if drawer.closed_by else _("someone")
        title = (_("Drawer counted %(amount)s over") if diff > 0 else _("Drawer counted %(amount)s short")) % {"amount": f"${abs(diff):.2f}"}
        out.append({"level": "warning", "title": title,
                    "sub": _("Closed by %(who)s at %(time)s") % {"who": who, "time": date_format(timezone.localtime(drawer.closed_at), "D g:i A")},
                    "url": reverse("cash_drawer")})
    waiting = Order.objects.filter(station_statuses__status=OrderStation.STATUS_PENDING,
                                   timestamp__lt=now - datetime.timedelta(minutes=5), picked_up=False).distinct().count()
    if waiting:
        out.append({"level": "info", "title": ngettext("%(n)d online order waiting for approval", "%(n)d online orders waiting for approval", waiting) % {"n": waiting},
                    "sub": _("Placed more than 5 minutes ago"), "url": reverse("order_marking")})
    levels = {"critical": _("Critical"), "warning": _("Warning"), "info": _("To review")}
    for a in out:
        a["level_label"] = levels[a["level"]]
    return out


def dashboard(range_key="today", open_only=False, now=None):
    now = timezone.localtime(now or timezone.now())
    if range_key not in dict(RANGES):
        range_key = "today"
    open_only = open_only and range_key != "today"
    view = today_view(now, open_only) if range_key == "today" else days_view(now, 7 if range_key == "7d" else 30, open_only)
    cur, prev = totals(view["current"]), totals(view["previous"])
    vs = view["vs"]
    kpis = [
        kpi(_("Net sales"), "sales", money0(cur["sales"]), pct_change(cur["sales"], prev["sales"]), True, vs),
        kpi(_("Orders"), "receipt", f"{cur['orders']:,}", pct_change(cur["orders"], prev["orders"]), True, vs),
        kpi(_("Average order"), "card", f"${cur['avg']:,.2f}", pct_change(cur["avg"], prev["avg"]), True, vs),
        kpi(_("Avg ticket time"), "clock", mmss(cur["prep"]), pct_change(cur["prep"], prev["prep"]) if cur["prep"] else None, False, vs),
    ]
    mix = channel_mix(view["current"])
    return {
        "range": range_key, "open_only": open_only, "ranges": RANGES, "now": now,
        "kpis": kpis, "chart": view["chart"], "range_short": view["short"],
        "live": live_panel(now), "sellers": top_sellers(view["current"]), "mix": mix,
        "mix_aria": ", ".join(f"{m['label']} {m['pct']}%" for m in mix),
        "alerts": alerts(now),
    }
