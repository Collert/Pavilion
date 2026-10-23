"""
Ingredients (design: AdminIngredients, list + editor): what the restaurant buys. An ingredient's cost per unit is
its pack price over its pack size (Ingredient.save), and every component and dish cost is worked out from it
(pos_server/dish_info.py). Allergens and the meat / fish / animal flags decide the diets dishes fit. The editor
works like the others: one form, a draft posted back to show the sums, saved on Save.
"""
import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.translation import gettext as _, ngettext

from inventory.models import RecipeIngredient
from pos_server.models import ALLERGENS, DIETS, ComponentIngredient, Ingredient, StorageLocation, Supplier

from .units import fmt_qty

CENT = Decimal("0.01")
UNIT_NAMES = {"g": _("Grams (g)"), "ml": _("Millilitres (ml)"), "ea": _("Pieces (ea)")}
FLAGS = [("meat", _("Meat or poultry"), _("Beef, pork, chicken, game")),
         ("fish", _("Fish or seafood"), _("Fish, shellfish, fish sauce")),
         ("animal", _("Animal byproduct"), _("Dairy, eggs, honey, gelatin")),
         ("gluten", _("Contains gluten"), _("Wheat, barley, rye, spelt")),
         ("alcohol", _("Alcohol"), _("Beer, wine, spirits. Staff cards can leave these out"))]


def _dec(value):
    try:
        number = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _num(value):
    value = _dec(value) or Decimal("0")
    return f"{value.normalize():f}" if value != value.to_integral_value() else str(int(value))


def money(value):
    return f"${Decimal(value or 0).quantize(CENT):,}"


def money3(value):
    value = Decimal(value or 0)
    if 0 < value < Decimal("0.01"):
        return f"${value:.4f}"
    return f"${value:.3f}" if 0 < value < Decimal("0.1") else money(value)


def cost_text(each, unit):
    """'$15.00 / kg', '$2.00 / L', '$0.30 / piece'."""
    if each is None:
        return "—"
    if unit == "g":
        return _("%(price)s / kg") % {"price": money(each * 1000)}
    if unit == "ml":
        return _("%(price)s / L") % {"price": money(each * 1000)}
    return _("%(price)s / piece") % {"price": money3(each)}


def per_unit(unit):
    return {"g": _("per kg"), "ml": _("per L")}.get(unit, _("per piece"))


def big_cost(each, unit):
    each = Decimal(each or 0)
    return money(each * 1000) if unit in ("g", "ml") else money3(each)


def change_pct(ingredient_or_cost, previous=None):
    cost = ingredient_or_cost if previous is not None else ingredient_or_cost.cost
    previous = previous if previous is not None else ingredient_or_cost.previous_cost
    if not cost or not previous:
        return 0.0
    return float((Decimal(cost) - previous) / previous * 100)


def stock_state(on_hand, par):
    if not par:
        return "none"
    if on_hand < par * 0.5:
        return "low"
    return "under" if on_hand < par else "ok"


def bar(on_hand, par):
    top = max(on_hand, (par or 0) * 1.5, 1)
    return {"fill": round(min(100, on_hand / top * 100), 1), "par": round(par / top * 100, 1) if par else None}


def effective_flags(draft):
    """The flags with what the allergens imply (Dairy or Egg is an animal product...)."""
    allergens = set(draft["allergens"])
    return {"meat": draft["meat"], "fish": draft["fish"] or bool(allergens & {"Fish", "Shellfish"}),
            "animal": draft["animal"] or bool(allergens & {"Dairy", "Egg"}), "gluten": "Gluten" in allergens}


def diet_badges(flags):
    return [{"key": key, "label": label, "short": short, "on": test(flags)} for key, label, short, test in DIETS]


def uses(ingredient):
    """[(component or recipe target title, how: recipe title or per unit, quantity, cost)] of where it's used."""
    if not ingredient.pk:
        return []
    out = []
    for row in RecipeIngredient.objects.filter(ingredient=ingredient).select_related("recipe"):
        target = row.recipe.target()
        out.append({"name": target[1].title if target else (row.recipe.title or str(row.recipe)),
                    "how": _("%(recipe)s · makes %(n)s") % {"recipe": row.recipe.title or str(row.recipe), "n": _num(row.recipe.yield_quantity)},
                    "qty": row.quantity, "url": ("admin_component", target[1].id) if target and target[0] == "component" else ("admin_recipe", row.recipe.id)})
    for ci in ComponentIngredient.objects.filter(ingredient=ingredient).select_related("component"):
        out.append({"name": ci.component.title, "how": {"g": _("per gram"), "ml": _("per millilitre")}.get(ci.component.unit_of_measurement, _("per piece")),
                    "qty": ci.quantity, "url": ("admin_component", ci.component.id)})
    return out


# ---------- drafts ----------

def draft_from_ingredient(ingredient):
    return {
        "title": ingredient.title, "unit": ingredient.unit_of_measurement, "category": ingredient.category,
        "allergens": [a for a in ALLERGENS if a in (ingredient.allergens or [])],
        "meat": ingredient.meat, "fish": ingredient.fish, "animal": ingredient.animal, "alcohol": ingredient.alcohol,
        "supplier": ingredient.supplier.name if ingredient.supplier_id else "",
        "pack_size": _num(ingredient.pack_size), "pack_price": _num(ingredient.pack_price) if ingredient.pack_price is not None else "",
        "inventory": _num(ingredient.inventory or 0), "inventory_was": _num(ingredient.inventory or 0),
        "par": _num(ingredient.par or 0), "storage": ingredient.storage.name if ingredient.storage_id else "",
    }


def draft_from_post(post):
    allergens = [a for a in ALLERGENS if a in post.getlist("allergens")]
    # The gluten switch and the Gluten allergen are one thing: whichever was changed wins
    gluten_was = post.get("gluten_was") == "1"
    if bool(post.get("gluten")) != gluten_was:
        allergens = [a for a in allergens if a != "Gluten"] + (["Gluten"] if post.get("gluten") else [])
        allergens = [a for a in ALLERGENS if a in allergens]
    return {
        "title": post.get("title", ""), "unit": post.get("unit") if post.get("unit") in UNIT_NAMES else "g",
        "category": post.get("category") if post.get("category") in dict(Ingredient.CATEGORIES) else "other",
        "allergens": allergens, "meat": bool(post.get("meat")), "fish": bool(post.get("fish")), "animal": bool(post.get("animal")),
        "alcohol": bool(post.get("alcohol")), "supplier": post.get("supplier", "").strip()[:80], "pack_size": post.get("pack_size", ""), "pack_price": post.get("pack_price", ""),
        "inventory": post.get("inventory", "0"), "inventory_was": post.get("inventory_was", "0"), "par": post.get("par", "0"),
        "storage": post.get("storage", "").strip()[:60],
    }


def draft_cost(draft):
    size, price = _dec(draft["pack_size"]), _dec(draft["pack_price"])
    if not size or size <= 0 or price is None:
        return None
    return (price / size).quantize(Decimal("0.000001"))


def problems(draft, ingredient):
    title = draft["title"].strip()
    if not title:
        return _("Give it a name")
    if Ingredient.objects.filter(title__iexact=title, archived=False).exclude(pk=ingredient.pk).exists():
        return _("Another ingredient has this name")
    if not (_dec(draft["pack_size"]) or 0) > 0:
        return _("Set a pack size")
    if any(_dec(draft[k]) is None or _dec(draft[k]) < 0 for k in ("inventory", "par")) or (draft["pack_price"] and (_dec(draft["pack_price"]) is None or _dec(draft["pack_price"]) < 0)):
        return _("Check the highlighted numbers")
    return None


def _canonical(draft):
    return (draft["title"].strip(), draft["unit"], draft["category"], tuple(draft["allergens"]), draft["meat"], draft["fish"],
            draft["animal"], draft["alcohol"], draft["supplier"].strip().lower(), _dec(draft["pack_size"]), _dec(draft["pack_price"]) if draft["pack_price"] else None,
            _dec(draft["inventory"]), _dec(draft["par"]), draft["storage"].lower())


def is_dirty(draft, ingredient):
    return _canonical(draft) != _canonical(draft_from_ingredient(ingredient))


@transaction.atomic
def save_draft(draft, ingredient):
    old_cost = ingredient.cost if ingredient.pk else None
    ingredient.title = draft["title"].strip()[:140]
    ingredient.unit_of_measurement, ingredient.category = draft["unit"], draft["category"]
    ingredient.storage = StorageLocation.named(draft["storage"])
    ingredient.allergens = list(draft["allergens"])
    ingredient.meat, ingredient.fish, ingredient.animal = draft["meat"], draft["fish"], draft["animal"]
    ingredient.alcohol = draft["alcohol"]
    name = draft["supplier"].strip()
    ingredient.supplier = (Supplier.objects.filter(name__iexact=name).first() or Supplier.objects.create(name=name)) if name else None
    ingredient.pack_size = float(_dec(draft["pack_size"]) or 1)
    ingredient.pack_price = _dec(draft["pack_price"]).quantize(CENT) if draft["pack_price"] and _dec(draft["pack_price"]) is not None else None
    if _dec(draft["inventory"]) != _dec(draft["inventory_was"]) or not ingredient.pk:  # leave counts made elsewhere alone
        ingredient.inventory = float(_dec(draft["inventory"]) or 0)
    ingredient.par = float(_dec(draft["par"]) or 0) or None
    new_cost = draft_cost(draft)
    if old_cost is not None and new_cost is not None and new_cost != old_cost:
        ingredient.previous_cost = old_cost
    ingredient.save()
    return ingredient


# ---------- list ----------

def ingredient_list(query="", show="all"):
    archived = show == "archived"
    # Disposables (packaging) have their own page
    ingredients = list(Ingredient.objects.filter(archived=archived, disposable__isnull=True).select_related("supplier").order_by("title"))
    q = (query or "").strip().lower()
    if q:
        ingredients = [i for i in ingredients if q in i.title.lower() or (i.supplier and q in i.supplier.name.lower())]
    used = {}
    for ingredient_id in RecipeIngredient.objects.filter(ingredient__isnull=False).values_list("ingredient_id", flat=True):
        used[ingredient_id] = used.get(ingredient_id, 0) + 1
    for ingredient_id in ComponentIngredient.objects.values_list("ingredient_id", flat=True):
        used[ingredient_id] = used.get(ingredient_id, 0) + 1
    rows = []
    for i in ingredients:
        on_hand, par = i.inventory or 0, i.par or 0
        state = "none" if i.unlimited else stock_state(on_hand, par)
        pct = change_pct(i)
        flags = i.flags
        buying = []
        if i.supplier_id:
            buying.append(i.supplier.name)
        if i.pack_price is not None:
            buying.append(_("%(price)s for %(qty)s") % {"price": money(i.pack_price), "qty": fmt_qty(i.pack_size, i.unit_of_measurement)})
        rows.append({
            "i": i, "sub": " · ".join(buying) or _("No price yet"), "cost": cost_text(i.cost, i.unit_of_measurement),
            "change": pct, "change_text": ("▲ " if pct > 0 else "▼ ") + f"{abs(pct):.1f}%" if abs(pct) >= 0.5 else "",
            "allergens": [a for a in ALLERGENS if a in (i.allergens or [])], "diets": diet_badges(flags),
            "stock": _("Not counted") if i.unlimited else f"{fmt_qty(on_hand, i.unit_of_measurement)} / {fmt_qty(par, i.unit_of_measurement) if par else '—'}",
            "state": state, "tag": {"low": _("Way under par"), "under": _("Under par"), "none": "" if i.unlimited else _("No par"), "ok": ""}[state],
            "bar": bar(on_hand, par), "value": Decimal(str(on_hand)) * (i.cost or 0), "flags": flags,
            "used": ngettext("%(n)d use", "%(n)d uses", used.get(i.id, 0)) % {"n": used.get(i.id, 0)} if used.get(i.id) else _("Not used"),
        })
    filters = [("all", _("All"), lambda r: True), ("low", _("Under par"), lambda r: r["state"] in ("low", "under")),
               ("up", _("Price went up"), lambda r: r["change"] >= 0.5), ("alg", _("Has allergens"), lambda r: bool(r["allergens"])),
               ("vegan", _("Vegan"), lambda r: DIETS[1][3](r["flags"])), ("gf", _("Gluten-free"), lambda r: not r["flags"]["gluten"])]
    tests = {key: test for key, _label, test in filters}
    if archived:
        chips = [{"key": "all", "label": _("All"), "count": Ingredient.objects.filter(archived=False, disposable__isnull=True).count()}]
        shown = rows
    else:
        show = show if show in tests else "all"
        chips = [{"key": key, "label": label, "count": sum(1 for r in rows if test(r))} for key, label, test in filters]
        shown = [r for r in rows if tests[show](r)]
    archived_count = Ingredient.objects.filter(archived=True, disposable__isnull=True).count()
    if archived_count:
        chips.append({"key": "archived", "label": _("Archived"), "count": archived_count})
    names = dict(Ingredient.CATEGORIES)
    groups = [{"name": names[key], "count": len(m), "rows": m} for key, _label in Ingredient.CATEGORIES
              if (m := [r for r in shown if r["i"].category == key])]

    everything = rows if not archived else []
    under = [r for r in everything if r["state"] in ("low", "under")]
    ups = sorted((r for r in everything if r["change"] >= 0.5), key=lambda r: -r["change"])
    suppliers = {r["i"].supplier_id for r in everything if r["i"].supplier_id}
    kpis = [
        {"label": _("Ingredients"), "value": f"{len(everything):,}", "sub": ngettext("From %(n)d supplier", "From %(n)d suppliers", len(suppliers)) % {"n": len(suppliers)}},
        {"label": _("Stock value"), "value": money(sum((r["value"] for r in everything), Decimal("0"))), "sub": _("Everything on hand, at cost")},
        {"label": _("Under par"), "value": str(len(under)), "bad": bool(under), "sub": ", ".join(r["i"].title for r in under) or _("All stocked")},
        {"label": _("Price went up"), "value": str(len(ups)),
         "sub": _("Biggest: %(name)s +%(pct).1f%%") % {"name": ups[0]["i"].title, "pct": ups[0]["change"]} if ups else _("No increases")},
    ]
    return {"groups": groups, "filters": chips, "show": "archived" if archived else show, "query": (query or "").strip(),
            "kpis": kpis, "legend": [{"short": short, "label": label} for _key, label, short, _test in DIETS]}


# ---------- editor ----------

def editor(ingredient, draft, leave_ask=False):
    issue = problems(draft, ingredient)
    dirty = is_dirty(draft, ingredient)
    unit = draft["unit"]
    cost = draft_cost(draft)
    flags = effective_flags(draft)
    allergens = set(draft["allergens"])
    forced = {"fish": bool(allergens & {"Fish", "Shellfish"}), "animal": bool(allergens & {"Dairy", "Egg"})}
    why_forced = {"fish": _("On because Fish or Shellfish is ticked"), "animal": _("On because Dairy or Egg is ticked")}
    switches = []
    for key, label, hint in FLAGS:
        own = "Gluten" in allergens if key == "gluten" else draft[key]
        switches.append({"key": key, "label": label, "own": own, "on": own or forced.get(key, False), "locked": forced.get(key, False),
                         "hint": why_forced[key] if forced.get(key) else hint})
    reasons = {
        "vegetarian": _("has meat") if flags["meat"] else _("has fish") if flags["fish"] else _("no meat or fish"),
        "vegan": _("has meat") if flags["meat"] else _("has fish") if flags["fish"] else _("animal byproduct") if flags["animal"] else _("nothing from animals"),
        "pescatarian": _("has meat") if flags["meat"] else _("no meat"),
        "gluten_free": _("has gluten") if flags["gluten"] else _("no gluten"),
    }
    diets = [{**d, "why": reasons[d["key"]]} for d in diet_badges(flags)]

    size, price = _dec(draft["pack_size"]), _dec(draft["pack_price"])
    on_hand, par = float(_dec(draft["inventory"]) or 0), float(_dec(draft["par"]) or 0)
    state = stock_state(on_hand, par)
    if state == "none":
        stock_msg = _("No par set")
    elif state == "ok":
        stock_msg = _("At or above par")
    else:
        stock_msg = _("%(n)s short") % {"n": fmt_qty(par - on_hand, unit)}
    supplier = draft["supplier"].strip() or _("the supplier")
    packs = math.ceil((par - on_hand) / float(size)) if size and size > 0 and par > on_hand else 0
    if not par:
        par_hint = _("Set a par level to see what to reorder.")
    elif packs:
        par_hint = ngettext("Below par: %(n)d pack from %(supplier)s covers it.", "Below par: %(n)d packs from %(supplier)s cover it.", packs) % {"n": packs, "supplier": supplier}
    else:
        par_hint = _("Stocked. When it drops below par, reorder from %(supplier)s.") % {"supplier": supplier}

    pct = change_pct(cost, ingredient.previous_cost) if cost is not None and ingredient.previous_cost else 0.0
    if abs(pct) < 0.5:
        change = {"text": _("No price change since last delivery"), "kind": ""}
    else:
        change = {"text": (_("▲ %(pct).1f%% since last delivery") if pct > 0 else _("▼ %(pct).1f%% cheaper than last delivery")) % {"pct": abs(pct)}
                  + " " + _("(was %(price)s)") % {"price": big_cost(ingredient.previous_cost, unit)}, "kind": "up" if pct > 0 else "down"}

    used = []
    for u in uses(ingredient):
        used.append({**u, "qty_text": fmt_qty(u["qty"], unit), "cost": money(Decimal(str(u["qty"])) * (cost or 0))})
    in_use = len(used)
    chip = {"ok": _("Stocked"), "low": _("Way under par"), "under": _("Under par"), "none": _("No par set")}[state]
    return {
        "ingredient": ingredient, "d": draft, "title": draft["title"].strip() or _("Untitled ingredient"), "state": state, "chip": chip,
        "status": issue or (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issue else "dirty" if dirty else "", "can_save": dirty and not issue, "leave_ask": leave_ask,
        "units": UNIT_NAMES.items(), "categories": Ingredient.CATEGORIES, "places": StorageLocation.objects.all(),
        "suppliers": Supplier.objects.all(), "all_allergens": [{"label": a, "on": a in allergens} for a in ALLERGENS],
        "switches": switches, "gluten_was": "Gluten" in allergens, "diets": diets,
        "pack_line": _("%(price)s ÷ %(qty)s") % {"price": money(price or 0), "qty": fmt_qty(size, unit)} if size and size > 0 else _("Set a pack size to work out the cost"),
        "cost_line": "—" if cost is None else _("%(price)s %(per)s") % {"price": big_cost(cost, unit), "per": per_unit(unit)},
        "cost_big": "—" if cost is None else big_cost(cost, unit), "per_unit": per_unit(unit), "change": change,
        "on_hand_line": _("%(qty)s on hand · worth %(money)s") % {"qty": fmt_qty(on_hand, unit), "money": money(Decimal(str(on_hand)) * (cost or 0))},
        "stock_msg": stock_msg, "bar": bar(on_hand, par), "par_hint": par_hint,
        "used": used, "archive_off": bool(in_use),
        "archive_text": ngettext("In %(n)d recipe or component, so it can’t be archived", "In %(n)d recipes or components, so it can’t be archived", in_use) % {"n": in_use}
        if in_use else _("Archive this ingredient"),
    }


def component_costs_changed(ingredient):
    """How many components get a new cost from this ingredient (for the saved message)."""
    names = {u["name"] for u in uses(ingredient)}
    return len(names)

