"""
Dishes (design: AdminDishes, list + editor). Food cost, allergens and diets come from the recipe
(pos_server.dish_info); modifiers are the dish's own option groups (pick one) and add-ons (pick any).
"""
import datetime
from decimal import Decimal

from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from pos_server.dish_info import DISH_PREFETCH, component_cost, dish_allergens, dish_diets, food_cost_pct, plate_cost
from pos_server.menu_schedule import Schedule
from pos_server.models import Component, Dish, DishCategory, Extra, Menu, OptionGroup, OrderItem, Station

HIGH_COST = 35  # food cost % the list flags
FILTERS = [("all", "All"), ("out", "Sold out or off"), ("cost", "Food cost over 35%"), ("nomenu", "Not on a menu")]
GROUP_BY = [("station", "Station"), ("category", "Menu category")]


def availability(dish):
    """The dish's selling state as {key, label}: off, out (sold out), always or on."""
    if dish.archived:
        return {"key": "off", "label": _("Archived")}
    if dish.availability == "off":
        return {"key": "off", "label": _("Turned off")}
    if dish.blocked or (dish.availability != "always" and not dish.in_stock):
        return {"key": "out", "label": _("Sold out")}
    if dish.availability == "always":
        return {"key": "always", "label": _("Always on")}
    return {"key": "on", "label": _("Available")}


def cost_level(pct):
    if pct is None:
        return "none"
    return "ok" if pct <= 30 else "high" if pct <= HIGH_COST else "bad"


def _matches(row, key):
    if key == "out":
        return row["avail"]["key"] in ("off", "out")
    if key == "cost":
        return (row["fc"] or 0) > HIGH_COST
    if key == "nomenu":
        return not row["menus"]
    return True


def dish_list(query="", by="station", show="all"):
    by = by if by in dict(GROUP_BY) else "station"
    archived = show == "archived"
    dishes = (Dish.objects.filter(archived=archived).select_related("station", "category")
              .prefetch_related(DISH_PREFETCH, "menu").order_by("title"))
    q = (query or "").strip()
    if q:
        dishes = dishes.filter(Q(title__icontains=q) | Q(description__icontains=q))
    rows = []
    for dish in dishes:
        cost = plate_cost(dish)
        fc = food_cost_pct(cost, dish.price)
        menus = sorted(dish.menu.all(), key=lambda m: m.position)
        tags = [d["short"] for d in dish_diets(dish) if d["key"] != "pescatarian"]
        allergens = dish_allergens(dish)
        rows.append({"dish": dish, "fc": fc, "level": cost_level(fc), "menus": menus, "avail": availability(dish),
                     "tags": tags, "allergens": allergens,
                     "menus_text": (ngettext("%(n)d menu", "%(n)d menus", len(menus)) % {"n": len(menus)} + " · " + ", ".join(m.title for m in menus))
                     if menus else _("Not on any menu")})
    archived_count = Dish.objects.filter(archived=True).count()
    if archived:
        filters = [{"key": "all", "label": _("All"), "count": Dish.objects.filter(archived=False).count()}]
        shown = rows
    else:
        show = show if show in dict(FILTERS) else "all"
        filters = [{"key": key, "label": _(label), "count": sum(1 for r in rows if _matches(r, key))} for key, label in FILTERS]
        shown = [r for r in rows if _matches(r, show)]
    if archived_count:
        filters.append({"key": "archived", "label": _("Archived"), "count": archived_count})

    sections = []
    if by == "station":
        groups = [(s.name, s.color, s.id) for s in Station.objects.all()]
        key_of = lambda r: r["dish"].station_id  # noqa: E731
    else:
        groups = [(c.name, "#1F4A3A", c.id) for c in DishCategory.objects.all()] + [(_("No category"), "#9AA8A1", None)]
        key_of = lambda r: r["dish"].category_id  # noqa: E731
    for name, color, key in groups:
        part = [r for r in shown if key_of(r) == key]
        if part:
            avg = sum((r["dish"].price for r in part), Decimal("0")) / len(part)
            sections.append({"name": name, "color": color, "rows": part, "count": len(part), "avg": avg})
    return {"sections": sections, "filters": filters, "show": show, "by": by, "query": q,
            "group_by": [(key, _(label)) for key, label in GROUP_BY]}


# ---------- editor ----------

def editor_queryset():
    return Dish.objects.select_related("station", "category").prefetch_related(
        DISH_PREFETCH, "dishcomponent_set__component__option_groups__options", "catering_prices", "menu",
        Prefetch("option_groups", queryset=OptionGroup.objects.prefetch_related("options")),
        Prefetch("extras", queryset=Extra.objects.select_related("component")))


def unit_cost_text(component):
    """A component's cost per unit as people say it: $1.35 each, $12.00/kg, $3.00/L."""
    each = component_cost(component)
    unit = component.unit_of_measurement
    if unit == "g":
        return _("%(price)s/kg") % {"price": _money(each * 1000)}
    if unit == "ml":
        return _("%(price)s/L") % {"price": _money(each * 1000)}
    return _money(each)


def _money(value):
    return f"${Decimal(value).quantize(Decimal('0.01')):,}"


def catering(dish, cost):
    tiers = list(dish.catering_prices.all())
    rows = []
    for t in tiers:
        off = round((1 - float(t.price / dish.price)) * 100, 1) if dish.price else 0
        fc = food_cost_pct(cost, t.price)
        if off > 0:
            saving = _("%(pct)s%% off · %(each)s less each") % {"pct": f"{off:g}", "each": _money(dish.price - t.price)}
        elif off < 0:
            saving = _("%(pct)s%% above retail") % {"pct": f"{-off:g}"}
        else:
            saving = _("Same as retail")
        rows.append({"tier": t, "saving": saving, "above": off < 0, "fc": fc, "level": cost_level(fc)})
    notes = []
    if dish.catering:
        if not tiers:
            notes.append(_("No quantity breaks yet, so catering orders use the retail price."))
        else:
            if tiers[0].quantity > dish.catering_min:
                notes.append(_("Orders of %(a)d to %(b)d have no catering price and will use retail.")
                             % {"a": dish.catering_min, "b": tiers[0].quantity - 1})
            if tiers[0].quantity < dish.catering_min:
                notes.append(_("The first break starts below the minimum order of %(n)d.") % {"n": dish.catering_min})
            for prev, t in zip(tiers, tiers[1:]):
                if t.quantity == prev.quantity:
                    notes.append(_("Two breaks start at %(n)d. Only one can apply.") % {"n": t.quantity})
                elif t.price > prev.price:
                    notes.append(_("Ordering %(n)d+ costs more per item (%(p)s) than %(m)d+ (%(q)s).")
                                 % {"n": t.quantity, "p": _money(t.price), "m": prev.quantity, "q": _money(prev.price)})
            if any(t.price > dish.price for t in tiers):
                notes.append(_("A catering price is higher than the retail price of %(price)s.") % {"price": _money(dish.price)})
    qty = tiers[-1].quantity if tiers else max(dish.catering_min, 10)
    each = dish.catering_price(qty)
    example = _("Example: %(n)d × %(each)s = %(total)s") % {"n": qty, "each": _money(each), "total": _money(each * qty)}
    return {"on": dish.catering, "min": dish.catering_min, "tiers": rows, "notes": notes, "example": example}


def modifiers(dish):
    groups = [{"group": g, "options": list(g.options.all())} for g in dish.option_groups.all()]
    inherited = []
    for dc in dish.dishcomponent_set.all():
        for g in dc.component.option_groups.all():
            inherited.append({"group": g, "component": dc.component, "options": list(g.options.all())})
    addons = [e for e in dish.extras.all() if e.kind == Extra.ADDON]
    used = {e.component_id for e in addons}
    return {"groups": groups, "inherited": inherited, "addons": addons,
            "addon_choices": Component.objects.exclude(pk__in=used).order_by("title"),
            "empty": not groups and not inherited and not addons}


def last_week(dish, cost):
    """Sold per day for the last 7 days (in combos too), sales and gross profit of the dish sold on its own."""
    today = timezone.localdate()
    start = today - datetime.timedelta(days=6)
    start_dt = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))
    per_day = [0] * 7
    sales, alone = Decimal("0"), 0
    items = OrderItem.objects.filter(dish=dish, order__timestamp__gte=start_dt, order__voided_at__isnull=True).select_related("order")
    for item in items:
        day = (timezone.localtime(item.order.timestamp).date() - start).days
        if 0 <= day < 7:
            per_day[day] += item.quantity
        if item.parent_id is None:
            sales += item.line_total
            alone += item.quantity
    top = max(per_day + [1])
    bars = []
    for i, n in enumerate(per_day):
        day = start + datetime.timedelta(days=i)
        bars.append({"height": max(4, round(n / top * 100)), "today": i == 6,
                     "title": _("%(day)s: %(n)d sold") % {"day": date_format(day, "D"), "n": n}})
    profit = sales - cost * alone if cost is not None else None
    return {"sold": sum(per_day), "sales": sales, "profit": profit, "bars": bars,
            "aria": _("Sold per day over the last 7 days: %(list)s") % {"list": ", ".join(str(n) for n in per_day)}}


def editor(dish):
    cost = plate_cost(dish)
    fc = food_cost_pct(cost, dish.price)
    parts = [{"dc": dc, "unit": dc.component.unit_of_measurement, "each": unit_cost_text(dc.component),
              "cost": Decimal(str(dc.quantity)) * component_cost(dc.component)} for dc in dish.dishcomponent_set.all()]
    used = {dc.component_id for dc in dish.dishcomponent_set.all()}
    if fc is None:
        fc_text = _("Add a recipe to see food cost")
    else:
        word = {"ok": _("healthy"), "high": _("a little high"), "bad": _("too high")}[cost_level(fc)]
        fc_text = _("Food cost %(pct)s%% · %(word)s") % {"pct": f"{fc:g}", "word": word}
    avail = availability(dish)
    if dish.availability == "off":
        hint = _("Hidden from the register and online until you switch it back.")
    elif dish.availability == "always":
        hint = _("Keeps selling even when the components run out in Cookery.")
    elif avail["key"] == "out":
        hint = _("Sold out right now because a component ran out. It comes back once Cookery records more.")
    else:
        hint = _("Sells while its components are in stock, then shows as sold out.")

    schedule = Schedule()
    on = {m.id for m in dish.menu.all()}
    menus = [{"menu": m, "on": m.id in on, "note": _("Draft") if m.draft else _("Live now") if schedule.is_live(m) else ""}
             for m in Menu.objects.all()]
    return {
        "dish": dish, "avail": avail, "avail_hint": hint,
        "stations": Station.objects.all(), "categories": DishCategory.objects.all(),
        "catering": catering(dish, cost),
        "parts": parts, "plate_cost": cost or Decimal("0"), "fc": fc, "fc_level": cost_level(fc), "fc_text": fc_text,
        "component_choices": [{"c": c, "label": f"{c.title} · {unit_cost_text(c)}"}
                              for c in Component.objects.exclude(pk__in=used).prefetch_related("componentingredient_set__ingredient").order_by("title")],
        "mods": modifiers(dish),
        "allergens": dish_allergens(dish), "diets": dish_diets(dish), "has_recipe": bool(parts),
        "menus": menus, "week": last_week(dish, cost),
    }
