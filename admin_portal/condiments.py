"""
Condiments (design: AdminCondiments, list + editor). A Condiment sits on a component counted in servings, which
holds its stock. House-made ones are made from the component's prep recipe; bought ones use one ingredient whose
pack is the case, so the supplier, servings per case, case price and allergens edited here are that ingredient's
(costs always come from ingredients). "Suggest with" makes the per-dish Extra rows the register shows. Pricing per
order (free servings, then the price each) is in pos_server/pricing.py.
"""
import datetime
import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from inventory.models import Recipe, Transformation
from pos_server.dish_info import component_cost, recipe_rows
from pos_server.models import (ALLERGENS, DIETS, Component, ComponentIngredient, Condiment, Dish, Extra, Ingredient,
                               OrderItem, StorageLocation, Supplier)

from . import ingredients as ingredient_page
from .units import fmt_qty

CENT = Decimal("0.01")
MAX_FREE = 5  # the stepper goes 0..5, then "no limit"


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
    value = _dec(value) or Decimal("0")
    return f"{value.normalize():f}" if value != value.to_integral_value() else str(int(value))


def money(value):
    return f"${Decimal(value or 0).quantize(CENT):,}"


def money3(value):
    value = Decimal(value or 0)
    return f"${value:.3f}" if 0 < value < Decimal("0.1") else money(value)


def bought_ingredient(component):
    """The ingredient a bought condiment is (its first per-unit ingredient), or None."""
    if not component.pk:
        return None
    ci = component.componentingredient_set.select_related("ingredient__supplier").first()
    return ci.ingredient if ci else None


def rule_text(free, price):
    if free >= Condiment.NO_LIMIT or price <= 0:
        return _("Free, no limit")
    if free == 0:
        return _("%(price)s each") % {"price": money(price)}
    return _("%(n)d free, then %(price)s") % {"n": free, "price": money(price)}


def stats(since=None):
    """{component id: {"given": servings handed out, "charged": servings charged}} over the last 30 days."""
    since = since or timezone.now() - datetime.timedelta(days=30)
    component_of = dict(Extra.objects.values_list("id", "component_id"))
    out = {}
    items = OrderItem.objects.filter(order__timestamp__gte=since, order__voided_at__isnull=True).only("selection", "quantity")
    for item in items:
        selection = item.selection or {}
        counts = dict(selection.get("condiments") or {})
        counts.update((selection.get("extras") or {}).get("condiments") or {})
        for extra_id, n in counts.items():
            component_id = component_of.get(_int(extra_id))
            if component_id:
                out.setdefault(component_id, {"given": 0, "charged": 0})["given"] += (_int(n) or 0) * item.quantity
        for component_id, n in (selection.get("charged") or {}).items():
            out.setdefault(_int(component_id), {"given": 0, "charged": 0})["charged"] += (_int(n) or 0) * item.quantity
    return out


# ---------- drafts ----------

def new_condiment():
    """An unsaved condiment for the New condiment editor (Save creates it and its component)."""
    component = Component(title="", unit_of_measurement="ea", category="sauce", crafting_option="craft")
    return Condiment(component=component, served_as="cup", serving=_("2 oz cup"), price=Decimal("0.50"))


def draft_from_condiment(condiment):
    component = condiment.component
    ingredient = bought_ingredient(component)
    return {
        "title": component.title, "served_as": condiment.served_as, "serving": condiment.serving,
        "source": "bought" if component.crafting_option == "buy" else "made", "recipe": str(component.recipe_id or ""),
        "supplier": ingredient.supplier.name if ingredient and ingredient.supplier_id else "",
        "case_size": _num(ingredient.pack_size) if ingredient else "100",
        "case_price": f"{ingredient.pack_price:.2f}" if ingredient and ingredient.pack_price is not None else "",
        "allergens": [a for a in ALLERGENS if a in ((ingredient.allergens or []) if ingredient else [])],
        "meat": bool(ingredient and ingredient.meat), "fish": bool(ingredient and ingredient.fish), "animal": bool(ingredient and ingredient.animal),
        "inventory": _num(component.inventory or 0), "inventory_was": _num(component.inventory or 0), "par": _num(component.par or 0),
        "storage": component.storage.name if component.storage_id else "", "keeps_days": _num(component.keeps_days),
        "free": str(condiment.free_per_order), "price": f"{condiment.price:.2f}", "dine_in": condiment.dine_in,
        "ask": condiment.ask_on_takeout, "pairs": sorted(str(d) for d in component.extra_uses.filter(dish__isnull=False).values_list("dish_id", flat=True))
                                                    if component.pk else [],
    }


def draft_from_post(post):
    flags = ingredient_page.draft_from_post(post)  # allergens and the switches work like the ingredient editor
    free = _int(post.get("free"))
    return {
        "title": post.get("title", ""),
        "served_as": post.get("served_as") if post.get("served_as") in dict(Condiment.SERVED_AS) else "packet",
        "serving": post.get("serving", "").strip()[:40], "source": "bought" if post.get("source") == "bought" else "made",
        "recipe": post.get("recipe", ""), "supplier": post.get("supplier", "").strip()[:80],
        "case_size": post.get("case_size", ""), "case_price": post.get("case_price", ""),
        "allergens": flags["allergens"], "meat": flags["meat"], "fish": flags["fish"], "animal": flags["animal"],
        "inventory": post.get("inventory", "0"), "inventory_was": post.get("inventory_was", "0"), "par": post.get("par", "0"),
        "storage": post.get("storage", "").strip()[:60], "keeps_days": post.get("keeps_days", "0"),
        "free": str(Condiment.NO_LIMIT if free is None or free >= Condiment.NO_LIMIT else max(free, 0)),
        "price": post.get("price", "0"), "dine_in": post.get("dine_in") if post.get("dine_in") in dict(Condiment.DINE_IN) else "ask",
        "ask": bool(post.get("ask")), "pairs": sorted({v for v in post.getlist("pairs") if _int(v)}),
    }


def apply_op(draft, op):
    """free_less / free_more step through 0..5 free per order, then no limit."""
    free = int(draft["free"])
    if op == "free_less":
        free = MAX_FREE if free >= Condiment.NO_LIMIT else max(0, free - 1)
    elif op == "free_more":
        free = Condiment.NO_LIMIT if free >= MAX_FREE else free + 1
    draft["free"] = str(free)
    return draft


def _recipe(draft):
    return Recipe.objects.prefetch_related("ingredients__ingredient", "ingredients__component").filter(pk=_int(draft["recipe"])).first() if _int(draft["recipe"]) else None


def cost_per_serving(draft, component):
    """None when it can't be worked out yet (no recipe, no case price)."""
    if draft["source"] == "bought":
        size, price = _dec(draft["case_size"]), _dec(draft["case_price"])
        return (price / size) if size and size > 0 and price is not None else None
    copy = Component(pk=component.pk, title=draft["title"], unit_of_measurement="ea")
    copy.recipe = _recipe(draft)
    return component_cost(copy) if recipe_rows(copy) else None


def facts(draft, component):
    """(allergens, flags or None when unknown) for the draft."""
    if draft["source"] == "bought":
        flags = ingredient_page.effective_flags(draft)
        return list(draft["allergens"]), flags
    copy = Component(pk=component.pk, unit_of_measurement="ea")
    copy.recipe = _recipe(draft)
    if not recipe_rows(copy):
        return [], None
    from pos_server.dish_info import component_ingredients
    found = component_ingredients(copy)
    allergens = {a for i in found for a in (i.allergens or [])}
    flags = {k: any(i.flags[k] for i in found) for k in ("meat", "fish", "animal", "gluten")}
    return [a for a in ALLERGENS if a in allergens], flags


def problems(draft, condiment):
    title = draft["title"].strip()
    if not title:
        return _("Give it a name")
    others = Condiment.objects.filter(component__title__iexact=title, component__archived=False)
    if condiment.pk:
        others = others.exclude(pk=condiment.pk)
    if others.exists():
        return _("Another condiment has this name")
    if draft["source"] == "bought" and not (_dec(draft["case_size"]) or 0) > 0:
        return _("Set servings per case")
    numbers = [draft["inventory"], draft["par"], draft["keeps_days"], draft["price"]] + ([draft["case_price"]] if draft["case_price"] else [])
    if any(_dec(v) is None or _dec(v) < 0 for v in numbers):
        return _("Check the highlighted numbers")
    return None


def _canonical(draft):
    bought = draft["source"] == "bought"
    return (draft["title"].strip(), draft["served_as"], draft["serving"].strip(), draft["source"],
            None if bought else _int(draft["recipe"]),
            (draft["supplier"].strip().lower(), _dec(draft["case_size"]), _dec(draft["case_price"]) if draft["case_price"] else None,
             tuple(draft["allergens"]), draft["meat"], draft["fish"], draft["animal"]) if bought else None,
            _dec(draft["inventory"]), _dec(draft["par"]), draft["storage"].lower(), _dec(draft["keeps_days"]),
            int(draft["free"]), _dec(draft["price"]), draft["dine_in"], draft["ask"], tuple(draft["pairs"]))


def is_dirty(draft, condiment):
    return _canonical(draft) != _canonical(draft_from_condiment(condiment))


@transaction.atomic
def save_draft(draft, condiment):
    """Write the draft to the condiment, its component, its ingredient (bought) and its dishes' Extra rows."""
    component = condiment.component if condiment.pk else Component(unit_of_measurement="ea", category="sauce")
    bought = draft["source"] == "bought"
    component.title = draft["title"].strip()[:140]
    component.crafting_option = "buy" if bought else "craft"
    recipe_id = _int(draft["recipe"]) if not bought else None
    component.recipe_id = recipe_id if recipe_id and Recipe.objects.filter(pk=recipe_id).exists() else None
    if _dec(draft["inventory"]) != _dec(draft["inventory_was"]) or not component.pk:  # leave counts made elsewhere alone
        component.inventory = float(_dec(draft["inventory"]) or 0)
    component.par = float(_dec(draft["par"]) or 0) or None
    component.storage = StorageLocation.named(draft["storage"])
    component.keeps_days = float(_dec(draft["keeps_days"]) or 0)
    component.save()
    if recipe_id:  # a recipe makes one thing
        Component.objects.filter(recipe_id=recipe_id).exclude(pk=component.pk).update(recipe=None)
        Transformation.objects.filter(recipe_id=recipe_id).update(recipe=None)
    if bought:
        ingredient = bought_ingredient(component) or Ingredient(unit_of_measurement="ea", category="pantry")
        old_cost = ingredient.cost if ingredient.pk else None
        ingredient.title = component.title
        name = draft["supplier"].strip()
        ingredient.supplier = (Supplier.objects.filter(name__iexact=name).first() or Supplier.objects.create(name=name)) if name else None
        ingredient.pack_size = float(_dec(draft["case_size"]) or 1)
        ingredient.pack_price = _dec(draft["case_price"]).quantize(CENT) if draft["case_price"] and _dec(draft["case_price"]) is not None else None
        ingredient.allergens = list(draft["allergens"])
        ingredient.meat, ingredient.fish, ingredient.animal = draft["meat"], draft["fish"], draft["animal"]
        ingredient.storage = component.storage
        new_cost = (ingredient.pack_price / Decimal(str(ingredient.pack_size))).quantize(Decimal("0.000001")) if ingredient.pack_price is not None else None
        if old_cost is not None and new_cost is not None and new_cost != old_cost:
            ingredient.previous_cost = old_cost
        ingredient.save()
        if not component.componentingredient_set.filter(ingredient=ingredient).exists():
            ComponentIngredient.objects.create(component=component, ingredient=ingredient, quantity=1)
    condiment.component = component
    condiment.served_as, condiment.serving, condiment.dine_in = draft["served_as"], draft["serving"].strip(), draft["dine_in"]
    condiment.free_per_order = int(draft["free"])
    condiment.price = (_dec(draft["price"]) or Decimal("0")).quantize(CENT)
    condiment.ask_on_takeout = draft["ask"]
    condiment.save()
    # "Suggest with": the dishes that offer it
    wanted = {int(d) for d in draft["pairs"]}
    Extra.objects.filter(component=component, dish__isnull=False).exclude(dish_id__in=wanted).delete()
    have = set(Extra.objects.filter(component=component, dish_id__in=wanted).values_list("dish_id", flat=True))
    for position, dish in enumerate(Dish.objects.filter(pk__in=wanted - have, archived=False)):
        Extra.objects.create(dish=dish, component=component, quantity=1, max_count=10, position=50 + position)
    return condiment


# ---------- list ----------

def condiment_list(query="", show="all"):
    archived = show == "archived"
    condiments = list(Condiment.objects.filter(component__archived=archived).select_related("component__recipe", "component__storage")
                      .prefetch_related("component__componentingredient_set__ingredient__supplier", "component__recipe__ingredients__ingredient",
                                        "component__recipe__ingredients__component").order_by("component__title"))
    q = (query or "").strip().lower()
    if q:
        condiments = [c for c in condiments if q in c.component.title.lower()
                      or any(ci.ingredient.supplier and q in ci.ingredient.supplier.name.lower() for ci in c.component.componentingredient_set.all())]
    numbers = stats()
    served = dict(Condiment.SERVED_AS)
    dine = dict(Condiment.DINE_IN)
    rows = []
    for c in condiments:
        component = c.component
        draft = draft_from_condiment(c)
        each = cost_per_serving(draft, component)
        allergens, flags = facts(draft, component)
        on_hand, par = component.inventory or 0, component.par or 0
        state = ingredient_page.stock_state(on_hand, par)
        if draft["source"] == "bought":
            sub = " · ".join(filter(None, [draft["supplier"], _("%(n)s per case") % {"n": draft["case_size"]}]))
        elif component.recipe_id:
            sub = _("Recipe: %(recipe)s · makes %(n)s") % {"recipe": component.recipe.title or str(component.recipe), "n": _num(component.recipe.yield_quantity)}
        else:
            sub = _("No recipe yet")
        n = numbers.get(component.id, {"given": 0, "charged": 0})
        rows.append({
            "c": c, "component": component, "source": draft["source"], "sub": sub, "served": served[c.served_as], "cost": money3(each) if each is not None else "—",
            "each": each or Decimal("0"), "rule": rule_text(c.free_per_order, c.price),
            "rule_sub": dine[c.dine_in] + (_(" · asked on takeout") if c.ask_on_takeout else ""),
            "allergens": allergens, "diets": [d for d in ingredient_page.diet_badges(flags) if d["on"] and d["key"] in ("vegan", "gluten_free")] if flags else [],
            "stock": f"{_num(on_hand)} / {_num(par)}" if par else f"{_num(on_hand)} / —", "state": state,
            "tag": {"low": _("Way under par"), "under": _("Under par"), "none": _("No par"), "ok": ""}[state],
            "bar": ingredient_page.bar(on_hand, par), "given": n["given"], "charged": n["charged"],
        })
    paid = lambda r: not r["c"].unlimited and r["c"].price > 0  # noqa: E731
    filters = [("all", _("All"), lambda r: True), ("made", _("House-made"), lambda r: r["source"] == "made"),
               ("bought", _("Bought"), lambda r: r["source"] == "bought"), ("paid", _("Charged extras"), paid),
               ("low", _("Under par"), lambda r: r["state"] in ("low", "under"))]
    tests = {key: test for key, _label, test in filters}
    if archived:
        chips = [{"key": "all", "label": _("All"), "count": Condiment.objects.filter(component__archived=False).count()}]
        shown = rows
    else:
        show = show if show in tests else "all"
        chips = [{"key": key, "label": label, "count": sum(1 for r in rows if test(r))} for key, label, test in filters]
        shown = [r for r in rows if tests[show](r)]
    archived_count = Condiment.objects.filter(component__archived=True).count()
    if archived_count:
        chips.append({"key": "archived", "label": _("Archived"), "count": archived_count})
    groups = []
    for key, name, about, color in (("made", _("House-made"), _("Prep makes these from a recipe"), "#12875A"),
                                     ("bought", _("Bought"), _("Ordered ready from a supplier"), "#4E79B5")):
        members = [r for r in shown if r["source"] == key]
        if members:
            groups.append({"name": name, "sub": f"{len(members)} · {about}", "color": color, "rows": members})

    everything = rows if not archived else []
    given = sum(r["given"] for r in everything)
    under = [r for r in everything if r["state"] in ("low", "under")]
    made = sum(1 for r in everything if r["source"] == "made")
    kpis = [
        {"label": _("Condiments"), "value": str(len(everything)), "sub": _("%(made)d house-made · %(bought)d bought") % {"made": made, "bought": len(everything) - made}},
        {"label": _("Handed out · 30 days"), "value": f"{given:,}",
         "sub": _("Servings, costing %(money)s") % {"money": money(sum((r["each"] * r["given"] for r in everything), Decimal("0")))}},
        {"label": _("Extra sauce sales · 30 days"), "value": money(sum((r["c"].price * r["charged"] for r in everything), Decimal("0"))),
         "sub": _("Charged beyond the free allowance")},
        {"label": _("Under par"), "value": str(len(under)), "bad": bool(under), "sub": ", ".join(r["component"].title for r in under) or _("All stocked")},
    ]
    return {"groups": groups, "filters": chips, "show": "archived" if archived else show, "query": (query or "").strip(), "kpis": kpis}


# ---------- editor ----------

def editor(condiment, draft, leave_ask=False):
    component = condiment.component
    issue = problems(draft, condiment)
    dirty = is_dirty(draft, condiment)
    bought = draft["source"] == "bought"
    each = cost_per_serving(draft, component)
    allergens, flags = facts(draft, component)
    recipe = _recipe(draft) if not bought else None
    rows = []
    batch_line = ""
    if recipe:
        copy = Component(pk=component.pk, unit_of_measurement="ea")
        copy.recipe = recipe
        for row in recipe_rows(copy) or []:
            item = row.item
            item_each = (row.ingredient.cost or Decimal("0")) if row.ingredient_id else component_cost(row.component)
            alg = (row.ingredient.allergens or []) if row.ingredient_id else []
            rows.append({"name": item.title, "allergens": [a for a in ALLERGENS if a in alg], "qty": fmt_qty(row.quantity, item.unit_of_measurement),
                         "price": ingredient_page.cost_text(item_each, item.unit_of_measurement) if item_each else _("free"),
                         "cost": money(Decimal(str(row.quantity)) * item_each)})
        batch = sum((Decimal(str(r.quantity)) * ((r.ingredient.cost or Decimal("0")) if r.ingredient_id else component_cost(r.component))
                     for r in recipe.ingredients.all()), Decimal("0"))
        batch_line = _("Batch costs %(cost)s and makes %(n)s servings") % {"cost": money(batch), "n": _num(recipe.yield_quantity)}
    taken = set(Recipe.objects.filter(component__isnull=False).exclude(component=component.pk).values_list("id", flat=True)) | \
        set(Recipe.objects.filter(transformations__isnull=False).values_list("id", flat=True)) | \
        set(Recipe.objects.filter(dish__isnull=False).values_list("id", flat=True))
    recipes = [{"id": str(r.id), "label": _("%(title)s · makes %(n)s") % {"title": r.title or str(r), "n": _num(r.yield_quantity)}}
               for r in Recipe.objects.order_by("title") if r.id not in taken or str(r.id) == draft["recipe"]]

    # Allergen switches (bought): the ingredient editor's rules
    flag_draft = {"allergens": draft["allergens"], "meat": draft["meat"], "fish": draft["fish"], "animal": draft["animal"]}
    allergen_set = set(draft["allergens"])
    forced = {"fish": bool(allergen_set & {"Fish", "Shellfish"}), "animal": bool(allergen_set & {"Dairy", "Egg"})}
    why = {"fish": _("On because Fish or Shellfish is ticked"), "animal": _("On because Dairy or Egg is ticked")}
    switches = []
    for key, label, hint in [f for f in ingredient_page.FLAGS if f[0] != "alcohol"]:  # condiments are never alcoholic
        own = "Gluten" in allergen_set if key == "gluten" else flag_draft[key]
        switches.append({"key": key, "label": label, "own": own, "on": own or forced.get(key, False), "locked": forced.get(key, False),
                         "hint": why[key] if forced.get(key) else (_("Wheat, barley, rye, malt") if key == "gluten" else hint)})

    on_hand, par = float(_dec(draft["inventory"]) or 0), float(_dec(draft["par"]) or 0)
    state = ingredient_page.stock_state(on_hand, par)
    if state == "none":
        stock_msg = _("No par set")
    elif state == "ok":
        stock_msg = _("At or above par")
    else:
        stock_msg = _("%(n)s servings short") % {"n": _num(par - on_hand)}
    supplier = draft["supplier"].strip() or _("the supplier")
    case = _dec(draft["case_size"]) or Decimal("0")
    if not par:
        par_hint = _("Set a par level to get alerts.")
    elif not bought:
        par_hint = (_("Below par, Cookery suggests a batch of %(recipe)s (%(n)s servings).") % {"recipe": (recipe.title or str(recipe)).lower(), "n": _num(recipe.yield_quantity)}
                    if recipe else _("Below par, Cookery flags it for prep."))
    elif par > on_hand and case > 0:
        cases = math.ceil((par - on_hand) / float(case))
        par_hint = ngettext("Below par: %(n)d case from %(supplier)s covers it.", "Below par: %(n)d cases from %(supplier)s cover it.", cases) % {"n": cases, "supplier": supplier}
    else:
        par_hint = _("Stocked. When it drops below par, reorder from %(supplier)s.") % {"supplier": supplier}

    free, price = int(draft["free"]), _dec(draft["price"]) or Decimal("0")
    unlimited = free >= Condiment.NO_LIMIT
    if unlimited:
        rule_line = _("Free, as many as they like.")
    elif free == 0:
        rule_line = _("Every one is charged at %(price)s.") % {"price": money(price)}
    else:
        rule_line = ngettext("The first one is free, then %(price)s each.", "The first %(n)d are free, then %(price)s each.", free) % {"n": free, "price": money(price)}
    warnings = []
    if not unlimited and price > 0 and each is not None and price < each:
        warnings.append(_("The extra price is below cost (%(cost)s).") % {"cost": money3(each)})
    if not unlimited and price == 0:
        warnings.append(_("Extras are free anyway, so set free per order to no limit."))
    if bought:
        cost_sub = _("From the case price at %(supplier)s.") % {"supplier": supplier}
    else:
        cost_sub = _("Worked out from the %(recipe)s recipe.") % {"recipe": (recipe.title or "").lower()} if recipe else _("Pick a recipe to work this out.")
    n = stats().get(component.pk, {"given": 0, "charged": 0}) if component.pk else {"given": 0, "charged": 0}
    return {
        "condiment": condiment, "component": component, "d": draft, "title": draft["title"].strip() or _("Untitled condiment"),
        "bought": bought, "state": state, "chip": {"ok": _("Stocked"), "low": _("Way under par"), "under": _("Under par"), "none": _("No par set")}[state],
        "status": issue or (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issue else "dirty" if dirty else "", "can_save": dirty and not issue, "leave_ask": leave_ask,
        "served": Condiment.SERVED_AS, "dine": Condiment.DINE_IN, "recipes": recipes, "recipe": recipe, "rows": rows, "batch_line": batch_line,
        "cost_each": _("%(price)s a serving") % {"price": money3(each)} if each is not None else _("No cost yet"),
        "pack_line": _("%(price)s ÷ %(n)s servings") % {"price": money(_dec(draft["case_price"]) or 0), "n": _num(case)} if case > 0 else _("Set servings per case"),
        "suppliers": Supplier.objects.all(), "places": StorageLocation.objects.all(),
        "all_allergens": [{"label": a, "on": a in allergen_set} for a in ALLERGENS], "switches": switches, "gluten_was": "Gluten" in allergen_set,
        "life_label": _("Shelf life") if bought else _("Keeps for, once made"),
        "on_hand_line": _("%(n)s servings on hand · worth %(money)s") % {"n": _num(on_hand), "money": money(Decimal(str(on_hand)) * (each or 0))},
        "stock_msg": stock_msg, "bar": ingredient_page.bar(on_hand, par), "par_hint": par_hint,
        "cost_big": money3(each) if each is not None else "—", "per_serving": _("per %(serving)s") % {"serving": draft["serving"].strip() or _("serving")},
        "cost_sub": cost_sub, "allergens": allergens, "known": flags is not None,
        "diets": [{"label": label, "on": flags is not None and test(flags)} for _key, label, _short, test in DIETS],
        "free_text": "∞" if unlimited else str(free), "less_off": free <= 0, "more_off": unlimited,
        "price_label": _("Price each") if free == 0 else _("Each extra"), "rule_line": rule_line, "warnings": warnings,
        "pairs": [{"dish": d, "on": str(d.id) in draft["pairs"]} for d in Dish.objects.filter(archived=False).order_by("title")],
        "stats": [{"value": f"{n['given']:,}", "label": _("Handed out")}, {"value": money((each or 0) * n["given"]), "label": _("Cost of those")},
                  {"value": money(condiment.price * n["charged"]) if condiment.pk else "$0.00", "label": _("Charged as extras")}],
    }
