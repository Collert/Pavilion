"""
Dishes (design: AdminDishes, list + editor). Food cost, allergens and diets come from the recipe
(pos_server.dish_info). Modifier groups are the dish's OptionGroups (pick one / pick any, required or not).

The editor is one form. Its state is a *draft* (plain dicts and strings, see `draft_from_dish` / `draft_from_post`):
adding and removing rows, or changing a field, posts the draft back to be shown again with the sums worked out,
and nothing is written until Save (`save_draft`).
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from pos_server.dish_info import (DISH_PREFETCH, component_cost, dish_allergens, dish_diets, food_cost_pct, parts_allergens,
                                  parts_cost, parts_diets, plate_cost)
from pos_server.models import CateringPrice, Component, Dish, DishComponent, Menu, Option, OptionGroup, OrderItem, Station

HIGH_COST = 35  # food cost % the list flags
FILTERS = [("all", "All"), ("out", "Sold out or off"), ("cost", "Food cost over 35%"), ("nomenu", "Not on a menu")]


def availability(dish, setting=None):
    """The dish's selling state as {key, label}: off, out (sold out), always or on. `setting` previews a change."""
    setting = setting or dish.availability
    if dish.archived:
        return {"key": "off", "label": _("Archived")}
    if setting == "off":
        return {"key": "off", "label": _("Turned off")}
    if dish.blocked or (setting != "always" and not dish.in_stock):
        return {"key": "out", "label": _("Sold out")}
    if setting == "always":
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


def dish_list(query="", show="all"):
    archived = show == "archived"
    dishes = (Dish.objects.filter(archived=archived).select_related("station")
              .prefetch_related(DISH_PREFETCH, "menu").order_by("title"))
    q = (query or "").strip()
    if q:
        dishes = dishes.filter(Q(title__icontains=q) | Q(description__icontains=q))
    rows = []
    for dish in dishes:
        fc = food_cost_pct(plate_cost(dish), dish.price)
        menus = sorted(dish.menu.all(), key=lambda m: m.position)
        rows.append({"dish": dish, "fc": fc, "level": cost_level(fc), "menus": menus, "avail": availability(dish),
                     "tags": [d["short"] for d in dish_diets(dish) if d["key"] != "pescatarian"],
                     "allergens": dish_allergens(dish),
                     "menus_text": (ngettext("%(n)d menu", "%(n)d menus", len(menus)) % {"n": len(menus)} + " · "
                                    + ", ".join(m.title for m in menus)) if menus else _("Not on any menu")})
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
    for station in Station.objects.all():
        part = [r for r in shown if r["dish"].station_id == station.id]
        if part:
            avg = sum((r["dish"].price for r in part), Decimal("0")) / len(part)
            sections.append({"name": station.name, "color": station.color, "rows": part, "count": len(part), "avg": avg})
    return {"sections": sections, "filters": filters, "show": show, "query": q}


# ---------- the editor's draft ----------

def editor_queryset():
    return Dish.objects.select_related("station").prefetch_related(
        DISH_PREFETCH, "catering_prices", "menu", "option_groups__options")


def _dec(value):
    try:
        number = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _num(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _plain(number):
    """A number as a form shows it: 1.0 -> "1", 2.5 -> "2.5"."""
    return f"{number:g}" if isinstance(number, float) else str(number)


def draft_from_dish(dish):
    return {
        "title": dish.title, "price": str(dish.price), "description": dish.description,
        "ticket_name": dish.ticket_name, "prep_minutes": str(dish.prep_minutes), "station": str(dish.station_id),
        "availability": dish.availability, "menus": sorted(m.id for m in dish.menu.all()),
        "catering": dish.catering, "catering_min": str(dish.catering_min),
        "tiers": [{"quantity": str(t.quantity), "price": str(t.price)} for t in dish.catering_prices.all()],
        "parts": [{"id": str(dc.id), "component": str(dc.component_id), "quantity": _plain(dc.quantity), "adjustable": dc.adjustable}
                  for dc in dish.dishcomponent_set.all()],
        "groups": [{"id": str(g.id), "title": g.title, "pick": g.pick, "required": g.required,
                    "options": [{"id": str(o.id), "title": o.title, "price": str(o.price)} for o in g.options.all()]}
                   for g in dish.option_groups.all()],
    }


def draft_from_post(post):
    """The draft as the form sent it (rows are numbered fields: part-0-quantity, group-1-opt-2-price...)."""
    def rows(prefix):
        return range(min(_int(post.get(f"{prefix}count")) or 0, 200))

    groups = []
    for i in rows("group-"):
        g = f"group-{i}-"
        groups.append({"id": post.get(g + "id", ""), "title": post.get(g + "title", ""),
                       "pick": OptionGroup.ANY if post.get(g + "pick") == OptionGroup.ANY else OptionGroup.ONE,
                       "required": bool(post.get(g + "required")),
                       "options": [{"id": post.get(f"{g}opt-{j}-id", ""), "title": post.get(f"{g}opt-{j}-title", ""),
                                    "price": post.get(f"{g}opt-{j}-price", "")} for j in rows(g + "opt-")]})
    menus = sorted({m for m in (_int(v) for v in post.getlist("menus")) if m})
    return {
        "title": post.get("title", ""), "price": post.get("price", ""), "description": post.get("description", ""),
        "ticket_name": post.get("ticket_name", "").upper()[:14], "prep_minutes": post.get("prep_minutes", ""),
        "station": post.get("station", ""),
        "availability": post.get("availability") if post.get("availability") in ("off", "auto", "always") else "auto",
        "menus": menus, "catering": bool(post.get("catering")), "catering_min": post.get("catering_min", ""),
        "tiers": [{"quantity": post.get(f"tier-{i}-quantity", ""), "price": post.get(f"tier-{i}-price", "")} for i in rows("tier-")],
        "parts": [{"id": post.get(f"part-{i}-id", ""), "component": post.get(f"part-{i}-component", ""),
                   "quantity": post.get(f"part-{i}-quantity", ""), "adjustable": bool(post.get(f"part-{i}-adjustable"))}
                  for i in rows("part-")],
        "groups": groups,
    }


def apply_op(draft, op, post):
    """Add or remove a row of the draft. `op` is the button pressed, e.g. "remove_tier:2", "add_option:0"."""
    name, _sep, arg = (op or "").partition(":")
    args = [_int(a) for a in arg.split(":")] if arg else []
    price = _dec(draft["price"]) or Decimal("0")

    def at(items, i):
        return items[i] if i is not None and 0 <= i < len(items) else None

    if name == "catering":
        draft["catering"] = not draft["catering"]
        if draft["catering"] and not draft["tiers"]:  # a starting point, like the design
            minimum = _int(draft["catering_min"]) or 10
            draft["tiers"].append({"quantity": str(max(10, minimum)), "price": str((price * Decimal("0.92") * 4).quantize(Decimal("1")) / 4)})
    elif name == "add_tier":
        tiers = sorted(draft["tiers"], key=lambda t: _int(t["quantity"]) or 0)
        if tiers:
            last_qty, last_price = _int(tiers[-1]["quantity"]) or 10, _dec(tiers[-1]["price"]) or price
            draft["tiers"].append({"quantity": str(last_qty * 2), "price": str(max(Decimal("0.25"), last_price - Decimal("0.50")))})
        else:
            draft["tiers"].append({"quantity": draft["catering_min"] or "10", "price": str(price)})
    elif name == "remove_tier" and at(draft["tiers"], args[0]):
        draft["tiers"].pop(args[0])
    elif name == "remove_part" and at(draft["parts"], args[0]):
        draft["parts"].pop(args[0])
    elif name == "add_group":
        draft["groups"].append({"id": "", "title": _("New group"), "pick": OptionGroup.ONE, "required": False,
                                "options": [{"id": "", "title": _("Option"), "price": "0"}]})
    elif name == "remove_group" and at(draft["groups"], args[0]):
        draft["groups"].pop(args[0])
    elif name == "add_option" and at(draft["groups"], args[0]):
        draft["groups"][args[0]]["options"].append({"id": "", "title": _("New option"), "price": "0"})
    elif name == "remove_option" and len(args) == 2 and at(draft["groups"], args[0]):
        options = draft["groups"][args[0]]["options"]
        if at(options, args[1]):
            options.pop(args[1])
    # A component picked in "Add a component" joins the recipe whatever was pressed
    component = _int(post.get("add_component"))
    if component and Component.objects.filter(pk=component).exists() and str(component) not in {p["component"] for p in draft["parts"]}:
        draft["parts"].append({"id": "", "component": str(component), "quantity": "1", "adjustable": False})
    return draft


def _canonical(draft):
    """The draft reduced to what would be saved, to tell whether anything changed."""
    return (
        draft["title"].strip(), _dec(draft["price"]), draft["description"].strip(), draft["ticket_name"].strip(),
        _int(draft["prep_minutes"]), draft["station"], draft["availability"], tuple(draft["menus"]),
        draft["catering"], _int(draft["catering_min"]),
        tuple(sorted((_int(t["quantity"]), _dec(t["price"])) for t in draft["tiers"])),
        tuple((p["component"], _num(p["quantity"]), p["adjustable"]) for p in draft["parts"]),
        tuple((g["id"], g["title"].strip(), g["pick"], g["required"], tuple((o["id"], o["title"].strip(), _dec(o["price"])) for o in g["options"]))
              for g in draft["groups"]),
    )


def is_dirty(draft, dish):
    return _canonical(draft) != _canonical(draft_from_dish(dish))


def problems(draft):
    """Why the draft can't be saved (empty when it can)."""
    price = _dec(draft["price"])
    if not draft["title"].strip() or price is None or price <= 0:
        return [_("Needs a name and a price")]
    out = []
    if not Station.objects.filter(pk=_int(draft["station"])).exists():
        out.append(_("Pick a station"))
    if any(_int(t["quantity"]) is None or _int(t["quantity"]) < 1 or _dec(t["price"]) is None or _dec(t["price"]) < 0
           for t in draft["tiers"]):
        out.append(_("Check the catering prices"))
    if any((_num(p["quantity"]) or 0) <= 0 for p in draft["parts"]):
        out.append(_("Every component needs an amount"))
    if any(not g["title"].strip() or not g["options"] for g in draft["groups"]):
        out.append(_("Every modifier group needs a name and an option"))
    if any(not o["title"].strip() or _dec(o["price"]) is None for g in draft["groups"] for o in g["options"]):
        out.append(_("Every option needs a name and a price"))
    return out


@transaction.atomic
def save_draft(draft, dish):
    """Write the draft to `dish` (a new Dish when it has no pk): fields, catering prices, recipe, modifiers, menus."""
    dish.title = draft["title"].strip()[:140]
    dish.price = _dec(draft["price"]).quantize(Decimal("0.01"))
    dish.description = draft["description"].strip()
    dish.ticket_name = draft["ticket_name"].strip().upper()[:14]
    dish.prep_minutes = min(max(_int(draft["prep_minutes"]) or dish.prep_minutes or 5, 1), 240)
    dish.station = Station.objects.get(pk=_int(draft["station"]))
    dish.availability = draft["availability"]
    dish.catering = draft["catering"]
    dish.catering_min = min(max(_int(draft["catering_min"]) or 10, 1), 10000)
    dish.save()

    dish.catering_prices.all().delete()
    for t in draft["tiers"]:
        CateringPrice.objects.create(dish=dish, quantity=_int(t["quantity"]), price=_dec(t["price"]).quantize(Decimal("0.01")))

    existing = {str(dc.id): dc for dc in dish.dishcomponent_set.all()}
    keep = set()
    for position, p in enumerate(draft["parts"]):
        dc = existing.get(p["id"])
        if dc is None or str(dc.component_id) != p["component"]:
            dc = DishComponent(dish=dish, component_id=_int(p["component"]))
        dc.quantity, dc.adjustable, dc.position = _num(p["quantity"]), p["adjustable"], position
        dc.save()
        keep.add(dc.id)
    for dc in existing.values():
        if dc.id not in keep:
            dc.delete()

    groups = {str(g.id): g for g in dish.option_groups.all()}
    keep_groups = set()
    for position, g in enumerate(draft["groups"]):
        group = groups.get(g["id"]) or OptionGroup(dish=dish)
        group.title, group.pick, group.required, group.position = g["title"].strip()[:80], g["pick"], g["required"], position
        group.save()
        keep_groups.add(group.id)
        options = {str(o.id): o for o in group.options.all()}
        keep_options = set()
        for option_position, o in enumerate(g["options"]):
            option = options.get(o["id"]) or Option(group=group)
            option.title, option.price, option.position = o["title"].strip()[:80], _dec(o["price"]).quantize(Decimal("0.01")), option_position
            option.save()
            keep_options.add(option.id)
        group.options.exclude(pk__in=keep_options).delete()
    dish.option_groups.exclude(pk__in=keep_groups).delete()

    dish.menu.set(Menu.objects.filter(pk__in=draft["menus"]))
    dish.update_stock()
    return dish


# ---------- what the editor shows ----------

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


def _catering(draft, price, cost):
    tiers = []
    for i, t in enumerate(draft["tiers"]):
        quantity, each = _int(t["quantity"]), _dec(t["price"])
        row = {"i": i, "t": t, "saving": "", "above": False, "fc": None, "level": "none"}
        if each is not None and price:
            off = round((1 - float(each / price)) * 100, 1)
            row["above"] = off < 0
            row["saving"] = (_("%(pct)s%% off · %(each)s less each") % {"pct": f"{off:g}", "each": _money(price - each)} if off > 0
                             else _("%(pct)s%% above retail") % {"pct": f"{-off:g}"} if off < 0 else _("Same as retail"))
            row["fc"] = food_cost_pct(cost, each) if each else None
            row["level"] = cost_level(row["fc"])
        tiers.append((quantity, each, row))
    valid = sorted(((q, p) for q, p, _row in tiers if q is not None and p is not None), key=lambda x: x[0])
    minimum = _int(draft["catering_min"]) or 1
    notes = []
    if draft["catering"]:
        if not valid:
            notes.append(_("No quantity breaks yet, so catering orders use the retail price."))
        else:
            if valid[0][0] > minimum:
                notes.append(_("Orders of %(a)d to %(b)d have no catering price and will use retail.") % {"a": minimum, "b": valid[0][0] - 1})
            if valid[0][0] < minimum:
                notes.append(_("The first break starts below the minimum order of %(n)d.") % {"n": minimum})
            for (q0, p0), (q1, p1) in zip(valid, valid[1:]):
                if q1 == q0:
                    notes.append(_("Two breaks start at %(n)d. Only one can apply.") % {"n": q1})
                elif p1 > p0:
                    notes.append(_("Ordering %(n)d+ costs more per item (%(p)s) than %(m)d+ (%(q)s).")
                                 % {"n": q1, "p": _money(p1), "m": q0, "q": _money(p0)})
            if price and any(p > price for _q, p in valid):
                notes.append(_("A catering price is higher than the retail price of %(price)s.") % {"price": _money(price)})
    qty = valid[-1][0] if valid else max(minimum, 10)
    each = price or Decimal("0")
    for q, p in valid:
        if qty >= q:
            each = p
    shown = sorted(tiers, key=lambda x: (x[0] is None, x[0] or 0))  # by quantity; each row keeps its field number
    return {"tiers": [row for _q, _p, row in shown], "notes": notes,
            "example": _("Example: %(n)d × %(each)s = %(total)s") % {"n": qty, "each": _money(each), "total": _money(each * qty)}}


def last_week(dish, cost, price):
    """Sold per day for the last 7 days (in combos too), sales and gross profit of the dish sold on its own."""
    today = timezone.localdate()
    start = today - datetime.timedelta(days=6)
    start_dt = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))
    per_day = [0] * 7
    sales, alone = Decimal("0"), 0
    if dish.pk:
        for item in OrderItem.objects.filter(dish=dish, order__timestamp__gte=start_dt, order__voided_at__isnull=True).select_related("order"):
            day = (timezone.localtime(item.order.timestamp).date() - start).days
            if 0 <= day < 7:
                per_day[day] += item.quantity
            if item.parent_id is None:
                sales += item.line_total
                alone += item.quantity
    top = max(per_day + [1])
    bars = [{"height": max(4, round(n / top * 100)), "today": i == 6,
             "title": _("%(day)s: %(n)d sold") % {"day": date_format(start + datetime.timedelta(days=i), "D"), "n": n}}
            for i, n in enumerate(per_day)]
    return {"sold": sum(per_day), "sales": sales, "profit": sales - cost * alone if cost is not None else None, "bars": bars,
            "aria": _("Sold per day over the last 7 days: %(list)s") % {"list": ", ".join(str(n) for n in per_day)}}


def editor(dish, draft, leave_ask=False, photo_pending=False):
    price = _dec(draft["price"])
    price = price if price and price > 0 else None
    components = {str(c.id): c for c in Component.objects.prefetch_related("componentingredient_set__ingredient").order_by("title")}
    parts, recipe = [], []
    for i, p in enumerate(draft["parts"]):
        component = components.get(p["component"])
        if component is None:
            continue
        quantity = _num(p["quantity"]) or 0
        parts.append((component, quantity))
        recipe.append({"i": i, "p": p, "component": component, "unit": component.unit_of_measurement,
                       "each": unit_cost_text(component), "cost": Decimal(str(quantity)) * component_cost(component)})
    cost = parts_cost(parts)
    fc = food_cost_pct(cost, price)
    if fc is None:
        fc_text = _("Add a recipe to see food cost")
    else:
        word = {"ok": _("healthy"), "high": _("a little high"), "bad": _("too high")}[cost_level(fc)]
        fc_text = _("Food cost %(pct)s%% · %(word)s") % {"pct": f"{fc:g}", "word": word}
    avail = availability(dish, draft["availability"])
    hints = {"off": _("Hidden from the register and menus until you switch it back."),
             "always": _("Keeps selling even when the components run out in Cookery.")}
    hint = hints.get(draft["availability"]) or (
        _("Sold out right now because a component ran out. It comes back once Cookery records more.") if avail["key"] == "out"
        else _("Sells while its components are in stock, then shows as sold out."))
    used = {p["component"] for p in draft["parts"]}
    issues = problems(draft)
    dirty = photo_pending or is_dirty(draft, dish)
    return {
        "dish": dish, "d": draft, "avail": avail, "avail_hint": hint, "price": price,
        "title": draft["title"].strip() or _("Untitled dish"),
        "status": issues[0] if issues else (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issues else "dirty" if dirty else "",
        "can_save": dirty and not issues, "leave_ask": leave_ask,
        "stations": Station.objects.all(),
        "catering": _catering(draft, price, cost),
        "recipe": recipe, "plate_cost": cost or Decimal("0"), "fc_level": cost_level(fc), "fc_text": fc_text,
        "component_choices": [{"id": cid, "label": f"{c.title} · {unit_cost_text(c)}"} for cid, c in components.items() if cid not in used],
        "allergens": parts_allergens(parts), "diets": parts_diets(parts), "has_recipe": bool(parts),
        "menus": [{"menu": m, "on": m.id in draft["menus"]} for m in Menu.objects.all()],
        "week": last_week(dish, cost, price),
        "counts": {"groups": len(draft["groups"])},
    }
