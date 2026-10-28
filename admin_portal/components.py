"""
Components (design: AdminComponents, list + editor). Each storage state is its own component (frozen and thawed
patties), so the editor's Stock card shows this component's stock with the components its transformations move
stock to and from. Add state adds a row there; Save makes it a component of its own ("Beef patty, frozen") with a
transformation each way, which recipes then attach to (admin Recipes) for their steps and time. Cost and allergens come from pos_server/dish_info.py. "Sell as an extra" edits the dishes' parts:
offering it on a dish makes that part adjustable with `max_extra` steps; not offering it sets `max_extra` to 0.
The editor works like the others: one form, a draft posted back to show the sums, saved on Save.
"""
import datetime
import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from inventory.models import Recipe, Transformation
from pos_server.dish_info import COMPONENT_PREFETCH, component_allergens, component_cost, made_from, recipe_rows
from pos_server.models import ALLERGENS, Component, DishComponent, Option, OptionGroup, OrderItem, StorageLocation

from .units import fmt_qty

CENT = Decimal("0.01")
MAX_EXTRA = 10
UNIT_NAMES = {"ea": _("Pieces (ea)"), "g": _("Grams (g)"), "ml": _("Millilitres (ml)")}
PER_UNIT = {"ea": _("per piece"), "g": _("per kg"), "ml": _("per L")}


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
    """A number for a form field: 2.5, 90, 0.25 (no trailing zeros)."""
    value = _dec(value) or Decimal("0")
    return f"{value.normalize():f}" if value != value.to_integral_value() else str(int(value))


def money(value):
    return f"${Decimal(value or 0).quantize(CENT):,}"


def money3(value):
    """$0.035 for fractions of a dime, else $1.35."""
    value = Decimal(value or 0)
    return f"${value:.3f}" if 0 < value < Decimal("0.1") else money(value)


def shown_cost(each, unit):
    """A cost per smallest unit as people say it: (number, 'per kg') for grams, per L for ml, per piece for ea."""
    return (each * 1000 if unit in ("g", "ml") else each), PER_UNIT[unit]


def cost_text(each, unit):
    """'$1.35 each', '$12.00/kg', '$3.00/L'."""
    if unit == "g":
        return _("%(price)s/kg") % {"price": money(each * 1000)}
    if unit == "ml":
        return _("%(price)s/L") % {"price": money(each * 1000)}
    return money3(each)


def stock_state(on_hand, par):
    if not par:
        return "none"
    if on_hand < par * 0.5:
        return "low"
    return "under" if on_hand < par else "ok"


STATE_TAG = {"low": _("Way under par"), "under": _("Under par"), "none": _("Made to order"), "ok": ""}


def bar(on_hand, par):
    """Widths (%) for the stock bar and the par mark."""
    top = max(on_hand, (par or 0) * 1.5, 1)
    return {"fill": round(min(100, on_hand / top * 100), 1), "par": round(par / top * 100, 1) if par else None}


def offered(dc):
    return dc.adjustable and dc.max_extra > 0


def extras_sold(since=None):
    """{component id: {"count": extra steps sold, "revenue", "cost"}} over the last 30 days."""
    since = since or timezone.now() - datetime.timedelta(days=30)
    items = OrderItem.objects.filter(order__timestamp__gte=since, order__voided_at__isnull=True, dish__isnull=False).only("selection", "quantity")
    steps = {}
    for item in items:
        for dc_id, count in ((item.selection or {}).get("parts") or {}).items():
            steps.setdefault(_int(dc_id), []).append((_int(count) or 0, item.quantity))
    out = {}
    if not steps:
        return out
    for dc in DishComponent.objects.filter(pk__in=[k for k in steps if k]).select_related("component"):
        extra = sum(max(0, count - dc.standard) * qty for count, qty in steps[dc.id])
        if not extra:
            continue
        row = out.setdefault(dc.component_id, {"count": 0, "revenue": Decimal("0"), "cost": Decimal("0")})
        row["count"] += extra
        row["revenue"] += dc.component.extra_price * extra
        row["cost"] += Decimal(str(dc.step)) * component_cost(dc.component) * extra
    return out


def family(component):
    """The components that transformations connect to this one (frozen ↔ thawed), this one first."""
    seen, queue = {component.id: component}, [component.id]
    edges = list(Transformation.objects.values_list("from_component_id", "to_component_id"))
    while queue:
        current = queue.pop()
        for a, b in edges:
            for here, there in ((a, b), (b, a)):
                if here == current and there and there not in seen:
                    seen[there] = None
                    queue.append(there)
    others = {c.id: c for c in Component.objects.filter(pk__in=[k for k in seen if k != component.id])}
    return [component] + sorted(others.values(), key=lambda c: c.title)


def dish_parts(component):
    if not component.pk:
        return []
    return list(component.dishcomponent_set.filter(dish__archived=False).select_related("dish__station").order_by("dish__title"))


# ---------- drafts ----------

def draft_from_component(component):
    parts = dish_parts(component)
    offers = [dc for dc in parts if offered(dc)]
    return {
        "title": component.title, "unit": component.unit_of_measurement, "category": component.category,
        "recipe": str(component.recipe_id or ""),
        "storage": component.storage.name if component.storage_id else "", "keeps_days": _num(component.keeps_days), "inventory": _num(component.inventory or 0),
        "inventory_was": _num(component.inventory or 0), "par": _num(component.par or 0),
        "extra_on": bool(offers) or bool(component.extra_label and not parts), "extra_label": component.extra_label,
        "extra_price": _num(component.extra_price), "extra_max": str(max((dc.max_extra for dc in offers), default=2)),
        "offer": sorted(str(dc.id) for dc in offers),
        "groups": [{"id": str(g.id), "title": g.title, "pick": g.pick, "required": g.required, "note": g.note,
                    "options": [{"id": str(o.id), "title": o.title, "price": f"{o.price:.2f}"} for o in g.options.all()]}
                   for g in (component.option_groups.all() if component.pk else [])],
        "states": [],
    }


def draft_from_post(post):
    unit = post.get("unit") if post.get("unit") in UNIT_NAMES else "ea"
    draft = {
        "title": post.get("title", ""), "unit": unit,
        "category": post.get("category") if post.get("category") in dict(Component.CATEGORIES) else "other",
        "recipe": post.get("recipe", ""),
        "storage": post.get("storage", "").strip()[:60],
        "keeps_days": post.get("keeps_days", "0"), "inventory": post.get("inventory", "0"),
        "inventory_was": post.get("inventory_was", "0"), "par": post.get("par", "0"),
        "extra_on": bool(post.get("extra_on")), "extra_label": post.get("extra_label", ""),
        "extra_price": post.get("extra_price", "0"), "extra_max": post.get("extra_max", "2"),
        "offer": sorted({v for v in post.getlist("offer") if _int(v)}),
        "groups": [{"id": post.get(f"group-{i}-id", ""), "title": post.get(f"group-{i}-title", ""),
                    "pick": OptionGroup.ANY if post.get(f"group-{i}-pick") == OptionGroup.ANY else OptionGroup.ONE,
                    "required": bool(post.get(f"group-{i}-required")), "note": post.get(f"group-{i}-note", "").strip()[:80],
                    "options": [{"id": post.get(f"group-{i}-opt-{j}-id", ""), "title": post.get(f"group-{i}-opt-{j}-title", ""),
                                 "price": post.get(f"group-{i}-opt-{j}-price", "0")}
                                for j in range(min(_int(post.get(f"group-{i}-opt-count")) or 0, 100))]}
                   for i in range(min(_int(post.get("group-count")) or 0, 50))],
        "states": [{"name": post.get(f"state-{i}-name", "").strip()[:60], "storage": post.get(f"state-{i}-storage", "").strip()[:60],
                    "keeps_days": post.get(f"state-{i}-keeps_days", "0"), "inventory": post.get(f"state-{i}-inventory", "0")}
                   for i in range(min(_int(post.get("state-count")) or 0, 10))],
    }
    return draft


def apply_op(draft, op, post, component):
    """extra_less / extra_more change the most extras per dish; turning the extra on fills in a label, price and dishes.
    add_group, remove_group:i, add_option:i and remove_option:i:j edit its modifier groups; add_state and
    remove_state:i the new storage states on the Stock card."""
    name, _sep, arg = (op or "").partition(":")
    args = [_int(a) for a in arg.split(":")] if arg else []
    groups = draft["groups"]
    i = args[0] if args and args[0] is not None and 0 <= args[0] < len(groups) else None
    if name == "add_group":
        groups.append({"id": "", "title": _("New group"), "pick": OptionGroup.ONE, "required": False, "note": "",
                       "options": [{"id": "", "title": _("Option"), "price": "0.00"}]})
    elif name == "remove_group" and i is not None:
        groups.pop(i)
    elif name == "add_option" and i is not None:
        groups[i]["options"].append({"id": "", "title": _("New option"), "price": "0.00"})
    elif name == "remove_option" and i is not None and len(args) == 2 and args[1] is not None and 0 <= args[1] < len(groups[i]["options"]):
        groups[i]["options"].pop(args[1])
    elif name == "add_state" and len(draft["states"]) < 10:
        draft["states"].append({"name": "", "storage": "", "keeps_days": "0", "inventory": "0"})
    elif name == "remove_state" and args and args[0] is not None and 0 <= args[0] < len(draft["states"]):
        draft["states"].pop(args[0])
    count = min(max(_int(draft["extra_max"]) or 1, 1), MAX_EXTRA)
    if op == "extra_less":
        count = max(1, count - 1)
    elif op == "extra_more":
        count = min(MAX_EXTRA, count + 1)
    draft["extra_max"] = str(count)
    if draft["extra_on"] and post.get("extra_was") == "0":
        if not draft["extra_label"].strip():
            draft["extra_label"] = (_("Extra %(name)s") % {"name": draft["title"].strip().lower()})[:40]
        if not (_dec(draft["extra_price"]) or 0) > 0:
            each = _draft_cost(draft, component) * Decimal(str(_avg_step(draft, component)))
            draft["extra_price"] = _num(max(Decimal("0.5"), Decimal(math.ceil(each * 3 * 4)) / 4))
        if not draft["offer"]:
            draft["offer"] = sorted(str(dc.id) for dc in dish_parts(component))
    return draft


def state_title(draft, state):
    """The component a new storage state becomes: "Beef patty" + "Frozen" = "Beef patty, frozen"."""
    name = state["name"].strip()
    return f"{draft['title'].strip()}, {name[:1].lower()}{name[1:]}"[:140] if name else ""


def _state_moves(state):
    """(scene, hours) there and back for a new state's transformations; a recipe attached later sets the real time."""
    name = state["name"].lower()
    if "froz" in name or "freez" in name:
        return ("freeze", 2), ("thaw", 12)
    if "chill" in name or "cold" in name:
        return ("chill", 2), ("prep", 1)
    return ("prep", 1), ("prep", 1)


def _recipe(draft):
    return Recipe.objects.prefetch_related("ingredients__ingredient", "ingredients__component").filter(pk=_int(draft["recipe"])).first() if _int(draft["recipe"]) else None


def _as_component(draft, component):
    """An unsaved copy of `component` with the draft's recipe and unit (for working out costs)."""
    copy = Component(pk=component.pk, title=draft["title"], unit_of_measurement=draft["unit"])
    recipe = _recipe(draft)
    copy.recipe = recipe
    return copy


def _draft_cost(draft, component):
    return component_cost(_as_component(draft, component))


def _avg_step(draft, component):
    parts = [dc for dc in dish_parts(component) if str(dc.id) in draft["offer"]] or dish_parts(component)
    return sum(dc.step for dc in parts) / len(parts) if parts else 1


def problems(draft, component):
    """What stops Save, as a status line (None when it can be saved)."""
    title = draft["title"].strip()
    if title and Component.objects.filter(title__iexact=title, archived=False).exclude(pk=component.pk).exists():
        return _("Another component has this name")
    numbers = [draft["keeps_days"], draft["inventory"], draft["par"]]
    if not title or any(_dec(v) is None or _dec(v) < 0 for v in numbers):
        return _("Fill in the highlighted fields")
    if draft["extra_on"] and (not draft["extra_label"].strip() or _dec(draft["extra_price"]) is None or _dec(draft["extra_price"]) < 0):
        return _("Fill in the highlighted fields")
    if any(not g["title"].strip() or not g["options"] or any(not o["title"].strip() or _dec(o["price"]) is None for o in g["options"])
           for g in draft["groups"]):
        return _("Every modifier group needs a name and named options")
    titles = [state_title(draft, st) for st in draft["states"]]
    if any(not t or _dec(st["keeps_days"]) is None or _dec(st["inventory"]) is None or _dec(st["keeps_days"]) < 0 or _dec(st["inventory"]) < 0
           for t, st in zip(titles, draft["states"])):
        return _("Every new state needs a name")
    if len(set(t.lower() for t in titles)) < len(titles) or any(Component.objects.filter(title__iexact=t).exists() for t in titles):
        return _("A component with this state's name already exists")
    return None


def _canonical(draft, component):
    unit = draft["unit"]
    return (draft["title"].strip(), unit, draft["category"], _int(draft["recipe"]), draft["storage"].lower(),
            _dec(draft["keeps_days"]), _dec(draft["inventory"]), _dec(draft["par"]), draft["extra_on"],
            draft["extra_label"].strip() if draft["extra_on"] else "",
            _dec(draft["extra_price"]), _int(draft["extra_max"]) if draft["offer"] and draft["extra_on"] else None,
            tuple(draft["offer"]) if draft["extra_on"] else (),
            tuple((g["id"], g["title"].strip(), g["pick"], g["required"], g["note"].strip(),
                   tuple((o["id"], o["title"].strip(), _dec(o["price"])) for o in g["options"])) for g in draft["groups"]),
            tuple((st["name"], st["storage"].lower(), _dec(st["keeps_days"]), _dec(st["inventory"])) for st in draft["states"]))


def is_dirty(draft, component):
    return _canonical(draft, component) != _canonical(draft_from_component(component), component)


@transaction.atomic
def save_draft(draft, component, copy_of=None):
    """Write the draft to `component` (a new one when it has no pk) and its dishes' parts."""
    component.title = draft["title"].strip()[:140]
    component.unit_of_measurement, component.category = draft["unit"], draft["category"]
    component.storage = StorageLocation.named(draft["storage"])
    recipe_id = _int(draft["recipe"])
    component.recipe_id = recipe_id if recipe_id and Recipe.objects.filter(pk=recipe_id).exists() else None
    component.keeps_days = float(_dec(draft["keeps_days"]) or 0)
    # Only write the stock when it was changed here, so counts made elsewhere since the page opened survive
    if _dec(draft["inventory"]) != _dec(draft["inventory_was"]) or not component.pk:
        component.inventory = float(_dec(draft["inventory"]) or 0)
    component.par = float(_dec(draft["par"]) or 0) or None
    component.extra_label = draft["extra_label"].strip()[:40] if draft["extra_on"] else ""
    component.extra_price = (_dec(draft["extra_price"]) or Decimal("0")).quantize(CENT)
    if not component.pk:
        component.crafting_option = copy_of.crafting_option if copy_of else "craft"
    component.save()
    if recipe_id:  # a recipe makes one thing
        Component.objects.filter(recipe_id=recipe_id).exclude(pk=component.pk).update(recipe=None)
        Transformation.objects.filter(recipe_id=recipe_id).update(recipe=None)
    groups = {str(g.id): g for g in component.option_groups.all()}
    keep_groups = set()
    for position, g in enumerate(draft["groups"]):
        group = groups.get(g["id"]) or OptionGroup(component=component)
        group.title, group.pick, group.required, group.note, group.position = g["title"].strip()[:80], g["pick"], g["required"], g["note"], position
        group.save()
        keep_groups.add(group.id)
        options = {str(o.id): o for o in group.options.all()}
        keep_options = set()
        for option_position, o in enumerate(g["options"]):
            option = options.get(o["id"]) or Option(group=group)
            option.title, option.position = o["title"].strip()[:80], option_position
            option.price = (_dec(o["price"]) or Decimal("0")).quantize(CENT)
            option.save()
            keep_options.add(option.id)
        group.options.exclude(pk__in=keep_options).delete()
    component.option_groups.exclude(pk__in=keep_groups).delete()
    count = min(max(_int(draft["extra_max"]) or 1, 1), MAX_EXTRA)
    for dc in dish_parts(component):
        on = draft["extra_on"] and str(dc.id) in draft["offer"]
        if on:
            DishComponent.objects.filter(pk=dc.pk).update(adjustable=True, max_extra=count)
        elif offered(dc):
            DishComponent.objects.filter(pk=dc.pk).update(max_extra=0)
    for st in draft["states"]:
        add_state(component, st, state_title(draft, st))
    return component


def add_state(component, state, title):
    """A new storage state of `component`: a component of its own that Cookery transforms move stock into and back."""
    other = Component.objects.create(
        title=title, unit_of_measurement=component.unit_of_measurement, category=component.category, crafting_option="transform",
        storage=StorageLocation.named(state["storage"]), keeps_days=float(_dec(state["keeps_days"]) or 0),
        inventory=float(_dec(state["inventory"]) or 0))
    (there, hours_there), (back, hours_back) = _state_moves(state)
    name = state["name"].strip()
    position = Transformation.objects.count()
    Transformation.objects.create(name=_("%(name)s · to %(state)s") % {"name": component.title, "state": name.lower()},
                                  from_component=component, to_component=other, scene=there,
                                  duration=datetime.timedelta(hours=hours_there), position=position)
    Transformation.objects.create(name=_("%(name)s · back from %(state)s") % {"name": component.title, "state": name.lower()},
                                  from_component=other, to_component=component, scene=back,
                                  duration=datetime.timedelta(hours=hours_back), position=position + 1)
    return other


def usage(component):
    """(dish parts, dishes offering it as an add-on) that keep it from being archived."""
    parts = dish_parts(component)
    addons = Option.objects.filter(component=component).exclude(group__dish__archived=True).count() if component.pk else 0
    return parts, addons


# ---------- list ----------

def component_list(query="", show="all"):
    archived = show == "archived"
    components = list(Component.objects.filter(archived=archived).select_related("recipe", "condiment").prefetch_related(
        *COMPONENT_PREFETCH, "dishcomponent_set__dish").order_by("title"))
    # Condiments have their own page; they're listed here only when a dish uses them as a part too
    components = [c for c in components if not hasattr(c, "condiment") or c.dishcomponent_set.all()]
    q = (query or "").strip().lower()
    if q:
        components = [c for c in components if q in c.title.lower() or (c.recipe and q in c.recipe.title.lower())]
    sold = extras_sold()
    rows = []
    for c in components:
        parts = [dc for dc in c.dishcomponent_set.all() if not dc.dish.archived]
        each = component_cost(c)
        on_hand, par = c.inventory or 0, c.par or 0
        state = stock_state(on_hand, par)
        is_extra = any(offered(dc) for dc in parts)
        if c.recipe_id:
            sub = _("Recipe: %(recipe)s · makes %(n)s") % {"recipe": c.recipe.title or str(c.recipe), "n": fmt_qty(c.recipe.yield_quantity, c.unit_of_measurement)}
        elif c.crafting_option == "buy":
            sub = _("Bought in")
        elif c.crafting_option == "transform":
            sub = _("Made by a transform in Cookery")
        else:
            sub = _("No recipe yet")
        rows.append({
            "c": c, "sub": sub, "each": each, "cost": cost_text(each, c.unit_of_measurement), "extra": is_extra,
            "extra_sub": ((_("%(pct)d%% margin") % {"pct": round((1 - each / c.extra_price) * 100)}) if c.extra_price > 0 else _("free")) if is_extra else _("not offered"),
            "stock": f"{fmt_qty(on_hand, c.unit_of_measurement)} / {fmt_qty(par, c.unit_of_measurement) if par else '—'}",
            "state": state, "tag": STATE_TAG[state], "bar": bar(on_hand, par), "value": Decimal(str(on_hand)) * each,
            "used": ngettext("%(n)d dish", "%(n)d dishes", len(parts)) % {"n": len(parts)} if parts else _("No dishes"),
            "needs_recipe": not c.recipe_id and c.crafting_option in ("craft", "auto"),
        })
    filters = [("all", _("All"), lambda r: True), ("extra", _("Sold as extras"), lambda r: r["extra"]),
               ("low", _("Under par"), lambda r: r["state"] in ("low", "under")), ("norecipe", _("No recipe yet"), lambda r: r["needs_recipe"])]
    tests = {key: test for key, _label, test in filters}
    if archived:
        chips = [{"key": "all", "label": _("All"), "count": Component.objects.filter(archived=False).count()}]
        shown = rows
    else:
        show = show if show in tests else "all"
        chips = [{"key": key, "label": label, "count": sum(1 for r in rows if test(r))} for key, label, test in filters]
        shown = [r for r in rows if tests[show](r)]
    archived_count = Component.objects.filter(archived=True).count()
    if archived_count:
        chips.append({"key": "archived", "label": _("Archived"), "count": archived_count})
    names = dict(Component.CATEGORIES)
    groups = []
    for key, _label in Component.CATEGORIES:
        members = [r for r in shown if r["c"].category == key]
        if members:
            groups.append({"name": names[key], "count": len(members), "rows": members})

    everything = [r for r in rows] if not archived else []
    under = [r for r in everything if r["state"] in ("low", "under")]
    revenue = sum((s["revenue"] for s in sold.values()), Decimal("0"))
    cost = sum((s["cost"] for s in sold.values()), Decimal("0"))
    kpis = [
        {"label": _("Components"), "value": f"{len(everything):,}",
         "sub": ngettext("%(n)d with a prep recipe", "%(n)d with a prep recipe", sum(1 for r in everything if r["c"].recipe_id)) % {"n": sum(1 for r in everything if r["c"].recipe_id)}},
        {"label": _("Stock value"), "value": money(sum((r["value"] for r in everything), Decimal("0"))), "sub": _("Everything on hand, at cost")},
        {"label": _("Under par"), "value": str(len(under)), "bad": bool(under),
         "sub": ", ".join(r["c"].title for r in under) or _("All stocked")},
        {"label": _("Extras sold · 30 days"), "value": money(revenue),
         "sub": _("%(pct)d%% margin after component cost") % {"pct": round((1 - cost / revenue) * 100)} if revenue else _("None sold yet")},
    ]
    return {"groups": groups, "filters": chips, "show": "archived" if archived else show, "query": (query or "").strip(), "kpis": kpis}


# ---------- editor ----------

def editor(component, draft, leave_ask=False):
    issue = problems(draft, component)
    dirty = is_dirty(draft, component)
    unit = draft["unit"]
    current = _as_component(draft, component)
    each = component_cost(current)
    recipe = current.recipe
    rows = recipe_rows(current)
    allergens = component_allergens(current)

    # Prep recipe card
    ingredients = []
    if rows:
        for row in rows:
            item = row.item
            item_each = (row.ingredient.cost or Decimal("0")) if row.ingredient_id else component_cost(row.component)
            alg = (row.ingredient.allergens or []) if row.ingredient_id else component_allergens(row.component)
            ingredients.append({"name": item.title, "allergens": [a for a in ALLERGENS if a in alg],
                                "qty": fmt_qty(row.quantity, item.unit_of_measurement),
                                "price": cost_text(item_each, item.unit_of_measurement) if item_each else _("free"),
                                "cost": money(Decimal(str(row.quantity)) * item_each)})
        batch = sum((Decimal(str(row.quantity)) * ((row.ingredient.cost or Decimal("0")) if row.ingredient_id else component_cost(row.component)) for row in rows), Decimal("0"))
        batch_line = _("Batch costs %(cost)s and makes %(n)s") % {"cost": money(batch), "n": fmt_qty(recipe.yield_quantity, unit)}
        source = "recipe"
        transform = None
    else:
        per_unit = list(current.componentingredient_set.all()) if current.pk else []
        for ci in per_unit:
            ingredients.append({"name": ci.ingredient.title, "allergens": [a for a in ALLERGENS if a in (ci.ingredient.allergens or [])],
                                "qty": fmt_qty(ci.quantity, ci.ingredient.unit_of_measurement),
                                "price": cost_text(ci.ingredient.cost or 0, ci.ingredient.unit_of_measurement) if ci.ingredient.cost else _("free"),
                                "cost": money3(Decimal(str(ci.quantity)) * (ci.ingredient.cost or 0))})
        batch_line = ""
        transform = None if per_unit else made_from(current)
        source = "per_unit" if per_unit else "transform" if transform else "none"
    taken = {r.id for r in Recipe.objects.filter(component__isnull=False).exclude(component=component.pk)} | \
            set(Recipe.objects.filter(transformations__isnull=False).values_list("id", flat=True)) | \
            set(Recipe.objects.filter(dish__isnull=False).values_list("id", flat=True))
    recipes = [{"id": str(r.id), "label": _("%(title)s · makes %(n)s") % {"title": r.title or str(r), "n": fmt_qty(r.yield_quantity, unit)}}
               for r in Recipe.objects.order_by("title") if r.id not in taken or str(r.id) == draft["recipe"]]

    # Stock card
    on_hand, par = _dec(draft["inventory"]) or Decimal("0"), _dec(draft["par"]) or Decimal("0")
    state = stock_state(float(on_hand), float(par))
    others = []
    for c in (family(component)[1:] if component.pk else []):
        others.append({"c": c, "kept_in": c.storage.name if c.storage_id else "—", "keeps": _num(c.keeps_days), "qty": fmt_qty(c.inventory or 0, c.unit_of_measurement)})
    if state == "none":
        stock_msg = _("No par set")
    elif state == "ok":
        stock_msg = _("At or above par")
    else:
        stock_msg = _("%(n)s short") % {"n": fmt_qty(par - on_hand, unit)}
    if not par:
        par_hint = _("Set a par level to get prep alerts in Cookery.")
    elif recipe:
        par_hint = _("Below par, Cookery suggests a batch of %(recipe)s (%(n)s).") % {"recipe": (recipe.title or str(recipe)).lower(), "n": fmt_qty(recipe.yield_quantity, unit)}
    else:
        par_hint = _("Below par, Cookery flags it for prep.")

    # Extras card
    parts = dish_parts(component)
    step_cost = each * Decimal(str(_avg_step(draft, component)))
    price = _dec(draft["extra_price"]) or Decimal("0")
    profit = price - step_cost
    pct = round(profit / price * 100) if price > 0 else 0
    margin = [{"label": _("Customer pays"), "value": money(price), "kind": ""},
              {"label": _("Component cost"), "value": money3(step_cost), "kind": ""},
              {"label": _("Profit per extra"), "value": money(profit) + (f" · {pct}%" if price > 0 else ""),
               "kind": "strong " + ("bad" if profit < 0 else "warn" if pct < 60 else "good")}]
    sold = extras_sold().get(component.pk, {}) if component.pk else {}
    if sold.get("count"):
        margin.append({"label": ngettext("%(n)d added · last 30 days", "%(n)d added · last 30 days", sold["count"]) % {"n": sold["count"]},
                       "value": _("%(money)s profit") % {"money": money(sold["count"] * profit)}, "kind": "muted"})
    warnings = []
    if draft["extra_on"] and profit < 0:
        warnings.append(_("This price is below cost, so every extra loses money."))
    if draft["extra_on"] and not draft["extra_label"].strip():
        warnings.append(_("Give the button a label staff will recognise."))
    offer_n = sum(1 for dc in parts if str(dc.id) in draft["offer"])
    if not parts:
        offer_line = _("It isn’t in any dish yet, so there’s nothing to add it to.")
    elif offer_n == len(parts):
        offer_line = _("Shows on every dish that uses it.")
    else:
        offer_line = _("Shows on %(n)d of %(m)d dishes that use it.") % {"n": offer_n, "m": len(parts)}

    # Header
    chip = {"ok": _("Stocked"), **STATE_TAG}[state]
    max_n = min(max(_int(draft["extra_max"]) or 1, 1), MAX_EXTRA)
    used_parts, addons = usage(component)
    if used_parts:
        archive_text = ngettext("Used in %(n)d dish, so it can’t be archived", "Used in %(n)d dishes, so it can’t be archived", len(used_parts)) % {"n": len(used_parts)}
    elif addons:
        archive_text = ngettext("An add-on on %(n)d dish, so it can’t be archived", "An add-on on %(n)d dishes, so it can’t be archived", addons) % {"n": addons}
    else:
        archive_text = _("Archive this component")
    shown, per = shown_cost(each, unit)
    return {
        "transform": transform if not rows else None,
        "component": component, "d": draft, "title": draft["title"].strip() or _("Untitled component"),
        "state": state, "chip": chip,
        "status": issue or (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issue else "dirty" if dirty else "", "can_save": dirty and not issue, "leave_ask": leave_ask,
        "units": UNIT_NAMES.items(), "categories": Component.CATEGORIES, "places": StorageLocation.objects.all(),
        "recipes": recipes, "recipe": recipe, "source": source, "ingredients": ingredients, "batch_line": batch_line,
        "cost_each": _("%(price)s each") % {"price": money3(each)} if unit == "ea" else cost_text(each, unit),
        "others": others, "new_states": [{"i": n, "st": st, "becomes": state_title(draft, st)} for n, st in enumerate(draft["states"])],
        "can_add_state": len(draft["states"]) < 10, "on_hand_line": _("%(qty)s on hand · worth %(money)s") % {"qty": fmt_qty(on_hand, unit), "money": money(on_hand * each)},
        "stock_msg": stock_msg, "bar": bar(float(on_hand), float(par)), "par_hint": par_hint,
        "cost_big": money3(shown) if unit == "ea" else money(shown), "per_unit": per,
        "cost_sub": {"recipe": _("Worked out from the %(recipe)s recipe.") % {"recipe": (recipe.title or "").lower() if recipe else ""},
                     "per_unit": _("Worked out from its ingredients."), "none": _("Unknown until a recipe is attached."),
                     "transform": _("Same as %(name)s, which Cookery makes it from.") % {"name": transform.from_component.title if transform else ""}}[source],
        "allergens": allergens,
        "allergen_source": _("Inherited from the recipe’s ingredients.") if source == "recipe" else _("Inherited from its ingredients.") if source == "per_unit"
        else _("Inherited from %(name)s.") % {"name": transform.from_component.title} if source == "transform" else _("Unknown until a recipe is attached."),
        "margin": margin, "warnings": warnings, "max_n": max_n, "less_off": max_n <= 1, "more_off": max_n >= MAX_EXTRA,
        "offers": [{"dc": dc, "on": str(dc.id) in draft["offer"]} for dc in parts], "offer_line": offer_line,
        "used": [{"dc": dc, "qty": fmt_qty(dc.quantity, unit), "cost": money(Decimal(str(dc.quantity)) * each)} for dc in parts],
        "archive_off": bool(used_parts or addons), "archive_text": archive_text,
        "group_dishes": len(parts),
    }
