"""
Reports (design: AdminReports): a month of business days as a table or a calendar, and a report for one day.

A business day is a day with at least one order (until store hours exist, that's the only record of being open).
Components and ingredients used are worked out from the dishes made and their standard recipes (DishComponent,
ComponentIngredient); customizations such as extra portions aren't counted.
"""
import calendar
import csv
import datetime
import io
import math
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Max, Min
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from pos_server.models import Order, Station

from .units import split_qty

PREP_TARGET = 600  # seconds: tickets slower than this count as over target
SLOT = 15  # minutes


def day_start(day):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def history_bounds():
    """(first day with an order, today), local dates. The first day is today when there are no orders."""
    today = timezone.localdate()
    first = Order.objects.aggregate(first=Min("timestamp"))["first"]
    return (timezone.localtime(first).date() if first else today), today


def slot_label(minute):
    h, m = divmod(minute, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def slot_range_label(minute):
    end = minute + SLOT
    h1, m1 = divmod(minute, 60)
    h2, m2 = divmod(end, 60)
    return f"{h1 % 12 or 12}:{m1:02d}–{h2 % 12 or 12}:{m2:02d} {'AM' if h2 % 24 < 12 else 'PM'}"


def mmss(seconds):
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------- a month ----------

class DaySummary:
    def __init__(self, day):
        self.day = day
        self.sales = Decimal("0")
        self.orders = 0
        self.preps = []
        self.slots = Counter()
        self.slot_sales = defaultdict(Decimal)
        self.dishes = Counter()

    @property
    def avg(self):
        return self.sales / self.orders if self.orders else Decimal("0")

    @property
    def prep(self):
        return sum(self.preps) / len(self.preps) if self.preps else None

    @property
    def busiest(self):
        """(slot start minute, orders) of the busiest 15 minutes (more sales breaks a tie)."""
        if not self.slots:
            return None, 0
        minute = max(self.slots, key=lambda m: (self.slots[m], self.slot_sales[m]))
        return minute, self.slots[minute]

    @property
    def top_dish(self):
        return self.dishes.most_common(1)[0][0] if self.dishes else "—"


def summaries(first: datetime.date, last: datetime.date) -> dict:
    """{date: DaySummary} for every day from `first` to `last` (inclusive) that had orders."""
    orders = (Order.objects.filter(timestamp__gte=day_start(first), timestamp__lt=day_start(last + datetime.timedelta(days=1)))
              .prefetch_related("items"))
    days = {}
    for order in orders:
        when = timezone.localtime(order.timestamp)
        s = days.setdefault(when.date(), DaySummary(when.date()))
        s.sales += order.total
        s.orders += 1
        if order.prep_time:
            s.preps.append(order.prep_time.total_seconds())
        minute = (when.hour * 60 + when.minute) // SLOT * SLOT
        s.slots[minute] += 1
        s.slot_sales[minute] += order.total
        for item in order.items.all():
            if not item.parent_id:
                s.dishes[item.title] += item.quantity
    return days


def closed_weekdays_note(month_days, open_days):
    """'No orders on Tuesdays' when a weekday never had orders this month (counting only days that are over)."""
    seen, opened = Counter(), Counter()
    for d in month_days:
        seen[d.weekday()] += 1
        opened[d.weekday()] += d in open_days
    closed = [w for w in range(7) if seen[w] and not opened[w]]
    if not closed:
        return ""
    names = [date_format(datetime.date(2024, 1, 1) + datetime.timedelta(days=w), "l") + "s" for w in closed]
    return _("No orders on %(days)s") % {"days": " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)}


def month_report(year: int, month: int, now=None):
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    first_day, _today = history_bounds()
    month_start = datetime.date(year, month, 1)
    month_end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    over = [month_start + datetime.timedelta(days=i) for i in range((month_end - month_start).days + 1)
            if first_day <= month_start + datetime.timedelta(days=i) < today]
    days = summaries(month_start, min(month_end, today - datetime.timedelta(days=1))) if over else {}
    business = [days[d] for d in sorted(days)]
    total = sum((d.sales for d in business), Decimal("0"))
    best = max(business, key=lambda d: d.sales) if business else None
    this_month = (year, month) == (today.year, today.month)

    stats = [
        {"label": _("Net sales, month to date") if this_month else _("Net sales"), "value": total,
         "money": True, "sub": ngettext("%(n)s order", "%(n)s orders", sum(d.orders for d in business)) % {"n": f"{sum(d.orders for d in business):,}"}},
        {"label": _("Business days"), "value": str(len(business)), "sub": closed_weekdays_note(over, days)},
        {"label": _("Average per day"), "value": total / len(business) if business else Decimal("0"), "money": True,
         "sub": _("across days open") if business else ""},
        {"label": _("Best day"), "value": best.sales if best else "—", "money": bool(best),
         "sub": date_format(best.day, "l, M j") if best else ""},
    ]

    top = max([d.sales for d in business] + [Decimal("1")])
    rows = []
    for d in reversed(business):
        minute, count = d.busiest
        rows.append({
            "day": d.day, "sales": d.sales, "orders": d.orders, "avg": d.avg, "prep": mmss(d.prep),
            "busy": f"{slot_label(minute)} · " + ngettext("%(n)d order", "%(n)d orders", count) % {"n": count} if minute is not None else "—",
            "top": d.top_dish, "width": round(float(d.sales / top) * 100),
        })

    # Calendar, Monday first. Cells are coloured by sales, lowest to highest in four steps.
    lo = min([d.sales for d in business] + [top])
    cells = [{"kind": "blank"} for _ in range(month_start.weekday())]
    for i in range(month_end.day):
        d = month_start + datetime.timedelta(days=i)
        if d == today:
            cells.append({"kind": "today", "day": d})
        elif d > today or d < first_day:
            cells.append({"kind": "out", "day": d})
        elif d not in days:
            cells.append({"kind": "closed", "day": d})
        else:
            s = days[d]
            step = 3 if top == lo else min(3, math.floor(float((s.sales - lo) / (top - lo)) * 4))
            cells.append({"kind": "open", "day": d, "sales": s.sales, "orders": s.orders, "tint": step,
                          "best": best is not None and best.day == d})
    while len(cells) % 7:
        cells.append({"kind": "blank"})

    prev_month = (month_start - datetime.timedelta(days=1)).replace(day=1)
    next_month = month_end + datetime.timedelta(days=1)
    return {
        "month_start": month_start, "stats": stats, "rows": rows, "cells": cells, "weeks": len(cells) // 7,
        "prev": prev_month if prev_month >= first_day.replace(day=1) else None,
        "next": next_month if next_month <= today.replace(day=1) else None,
    }


# ---------- one day ----------

def neighbours(day):
    """The business days just before and after `day` (None at either end of history; today isn't over yet)."""
    today = timezone.localdate()
    earlier = Order.objects.filter(timestamp__lt=day_start(day)).aggregate(t=Max("timestamp"))["t"]
    later = Order.objects.filter(timestamp__gte=day_start(day + datetime.timedelta(days=1)),
                                 timestamp__lt=day_start(today)).aggregate(t=Min("timestamp"))["t"]
    return (timezone.localtime(earlier).date() if earlier else None), (timezone.localtime(later).date() if later else None)


def last_business_day():
    today = timezone.localdate()
    last = Order.objects.filter(timestamp__lt=day_start(today)).aggregate(t=Max("timestamp"))["t"]
    return timezone.localtime(last).date() if last else None


def day_report(day: datetime.date):
    orders = list(Order.objects.filter(timestamp__gte=day_start(day), timestamp__lt=day_start(day + datetime.timedelta(days=1)))
                  .order_by("timestamp")
                  .prefetch_related("items__dish__dishcomponent_set__component__componentingredient_set__ingredient",
                                    "items__dish__station", "items__children__dish"))
    stations = {s.id: s for s in Station.objects.all()}
    sales = sum((o.total for o in orders), Decimal("0"))

    sold = {}  # title -> {qty, sales, station}
    made = Counter()  # station id -> items made
    station_sales = defaultdict(Decimal)
    station_hours = defaultdict(Counter)
    components = {}  # component id -> [component, qty, {dish titles}]
    ingredients = {}  # ingredient id -> [ingredient, qty, {component ids}]
    slots = Counter()
    slot_sales = defaultdict(Decimal)
    slot_prep = defaultdict(list)

    def use(dish, qty):
        for dc in dish.dishcomponent_set.all():
            entry = components.setdefault(dc.component_id, [dc.component, 0.0, set()])
            entry[1] += dc.quantity * qty
            entry[2].add(dish.title)
            for ci in dc.component.componentingredient_set.all():
                ing = ingredients.setdefault(ci.ingredient_id, [ci.ingredient, 0.0, set()])
                ing[1] += ci.quantity * dc.quantity * qty
                ing[2].add(dc.component_id)

    for order in orders:
        when = timezone.localtime(order.timestamp)
        minute = (when.hour * 60 + when.minute) // SLOT * SLOT
        slots[minute] += 1
        slot_sales[minute] += order.total
        if order.prep_time:
            slot_prep[minute].append(order.prep_time.total_seconds())
        for item in order.items.all():
            if item.parent_id:
                continue
            line = sold.setdefault(item.title, {"qty": 0, "sales": Decimal("0"), "station": None})
            line["qty"] += item.quantity
            line["sales"] += item.line_total
            if item.dish:  # a dish: made at its station
                line["station"] = item.dish.station
                made[item.dish.station_id] += item.quantity
                station_sales[item.dish.station_id] += item.line_total
                station_hours[item.dish.station_id][when.hour] += item.quantity
                use(item.dish, item.quantity)
            else:  # a combo: each pick is made at its own station; the price is split by the picks' own prices
                picks = [child for child in item.children.all() if child.dish]
                weight = sum((child.dish.price * child.quantity for child in picks), Decimal("0")) or Decimal("1")
                for child in picks:
                    qty = child.quantity * item.quantity
                    made[child.dish.station_id] += qty
                    station_sales[child.dish.station_id] += item.line_total * child.dish.price * child.quantity / weight
                    station_hours[child.dish.station_id][when.hour] += qty
                    use(child.dish, qty)

    items_sold = sum(line["qty"] for line in sold.values())
    items_made = sum(made.values())
    top_qty = max([line["qty"] for line in sold.values()] + [1])
    dishes = [{"name": title, "qty": line["qty"], "sales": line["sales"],
               "station": line["station"], "share": round(line["qty"] / items_sold * 100, 1) if items_sold else 0,
               "width": round(line["qty"] / top_qty * 100)}
              for title, line in sorted(sold.items(), key=lambda pair: (-pair[1]["qty"], pair[0]))]

    def qty(value, unit):
        number, unit = split_qty(value, unit)
        return {"qty": number, "unit": unit}

    comp_rows = [{"name": c.title, **qty(n, c.unit_of_measurement),
                  "used_in": ngettext("%(n)d dish", "%(n)d dishes", len(uses)) % {"n": len(uses)}}
                 for c, n, uses in sorted(components.values(), key=lambda e: -e[1]) if n > 0]
    ing_rows = [{"name": i.title, **qty(n, i.unit_of_measurement),
                 "used_in": ngettext("%(n)d comp.", "%(n)d comps.", len(uses)) % {"n": len(uses)}}
                for i, n, uses in sorted(ingredients.values(), key=lambda e: e[0].title.lower()) if n > 0]

    # Station mix, with a donut (2px gaps between pieces)
    station_rows, donut = [], []
    angle = -math.pi / 2
    gap = 0.025
    cx = cy = 100
    outer, inner = 96, 64
    for sid, n in sorted(made.items(), key=lambda pair: (stations[pair[0]].position, pair[0])):
        st = stations[sid]
        frac = n / items_made if items_made else 0
        a0, a1 = angle + gap / 2, angle + frac * 2 * math.pi - gap / 2
        angle += frac * 2 * math.pi
        if frac > 0:
            large = 1 if a1 - a0 > math.pi else 0
            p = lambda r, a: f"{cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}"  # noqa: E731
            if frac >= 0.999:  # a full ring: two halves
                d = (f"M{p(outer, -math.pi / 2)}A{outer} {outer} 0 1 1 {p(outer, math.pi / 2)}A{outer} {outer} 0 1 1 {p(outer, -math.pi / 2)}"
                     f"M{p(inner, -math.pi / 2)}A{inner} {inner} 0 1 0 {p(inner, math.pi / 2)}A{inner} {inner} 0 1 0 {p(inner, -math.pi / 2)}Z")
            else:
                d = f"M{p(outer, a0)}A{outer} {outer} 0 {large} 1 {p(outer, a1)}L{p(inner, a1)}A{inner} {inner} 0 {large} 0 {p(inner, a0)}Z"
            donut.append({"d": d, "color": st.color})
        hour = station_hours[sid].most_common(1)[0][0] if station_hours[sid] else None
        station_rows.append({"name": st.name, "color": st.color, "items": n, "share": round(frac * 100, 1),
                             "sales": station_sales[sid],
                             "peak": f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}–{(hour + 1) % 12 or 12} {'AM' if hour + 1 < 12 or hour + 1 == 24 else 'PM'}" if hour is not None else "—"})

    # 15-minute slots across the day: at least 7 AM to 9 PM, wider if orders came earlier or later
    first_slot = min([7 * 60] + list(slots))
    last_slot = max([21 * 60 - SLOT] + list(slots))
    span = range(first_slot, last_slot + SLOT, SLOT)
    busiest = max(slots, key=lambda m: (slots[m], slot_sales[m])) if slots else None
    top_slot = max(list(slots.values()) + [1])
    preps = {m: sum(v) / len(v) for m, v in slot_prep.items()}
    prep_scale = max([PREP_TARGET * 1.5] + list(preps.values()))
    slot_rows = [{"time": slot_range_label(m), "orders": slots.get(m, 0),
                  "sales": slot_sales[m] if slots.get(m) else None, "busiest": m == busiest, "hour_start": m % 60 == 0,
                  "width": round(slots.get(m, 0) / top_slot * 100)} for m in span]
    prep_rows = [{"time": slot_range_label(m), "avg": mmss(preps.get(m)), "n": slots.get(m) or "—",
                  "over": preps.get(m, 0) > PREP_TARGET, "hour_start": m % 60 == 0,
                  "width": min(100, round(preps.get(m, 0) / prep_scale * 100))} for m in span]
    all_preps = [s for v in slot_prep.values() for s in v]
    avg_prep = sum(all_preps) / len(all_preps) if all_preps else None
    over_slots = sum(1 for v in preps.values() if v > PREP_TARGET)

    count = len(orders)
    kpis = [
        {"label": _("Net sales"), "value": sales, "money0": True, "sub_money": sales},
        {"label": _("Orders"), "value": f"{count:,}", "sub": ngettext("%(n)d item", "%(n)d items", items_sold) % {"n": items_sold}},
        {"label": _("Average order"), "value": sales / count if count else Decimal("0"), "money": True,
         "sub": _("%(n)s items per order") % {"n": f"{items_sold / count:.1f}" if count else "0"}},
        {"label": _("Average prep time"), "value": mmss(avg_prep),
         "sub": ngettext("%(n)d of %(m)d slot over %(t)s", "%(n)d of %(m)d slots over %(t)s", len(preps)) % {"n": over_slots, "m": len(preps), "t": mmss(PREP_TARGET)}},
        {"label": _("Busiest 15 minutes"), "value": slot_label(busiest) if busiest is not None else "—",
         "sub": (ngettext("%(n)d order", "%(n)d orders", slots[busiest]) % {"n": slots[busiest]}) if busiest is not None else "",
         "sub_money": slot_sales[busiest] if busiest is not None else None},
    ]
    return {
        "day": day, "kpis": kpis, "orders": count, "sales": sales, "items": items_sold, "items_made": items_made,
        "dishes": dishes, "components": comp_rows, "ingredients": ing_rows,
        "stations": station_rows, "donut": donut, "slots": slot_rows, "prep": prep_rows,
        "target_pct": round(PREP_TARGET / prep_scale * 100, 2), "target": mmss(PREP_TARGET),
        "donut_aria": ", ".join(f"{s['name']} {round(s['share'])}%" for s in station_rows),
        # raw numbers for the CSV
        "_components": components, "_ingredients": ingredients, "_slots": (span, slots, slot_sales, preps),
    }


def day_csv(report) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Report", report["day"].isoformat()])
    w.writerow([])
    w.writerow(["Dishes"])
    w.writerow(["Dish", "Station", "Sold", "Sales"])
    for d in report["dishes"]:
        w.writerow([d["name"], d["station"].name if d["station"] else "Combo", d["qty"], f"{d['sales']:.2f}"])
    w.writerow([])
    w.writerow(["Components"])
    w.writerow(["Component", "Used", "Unit"])
    for c, n, _uses in report["_components"].values():
        w.writerow([c.title, round(n, 2), c.unit_of_measurement])
    w.writerow([])
    w.writerow(["Ingredients"])
    w.writerow(["Ingredient", "Used", "Unit"])
    for i, n, _uses in report["_ingredients"].values():
        w.writerow([i.title, round(n, 2), i.unit_of_measurement])
    w.writerow([])
    w.writerow(["Station mix"])
    w.writerow(["Station", "Items", "Sales"])
    for s in report["stations"]:
        w.writerow([s["name"], s["items"], f"{s['sales']:.2f}"])
    w.writerow([])
    w.writerow(["15-minute slots"])
    w.writerow(["Slot", "Orders", "Sales", "Average prep (sec)"])
    span, slots, slot_sales, preps = report["_slots"]
    for m in span:
        w.writerow([slot_range_label(m), slots.get(m, 0), f"{slot_sales.get(m, Decimal('0')):.2f}",
                    round(preps[m]) if m in preps else ""])
    return out.getvalue()
