"""
Recipes (design: AdminRecipes, list + editor). A recipe is attached to what it makes: a component (Cookery's Craft),
a transformation (Cookery's Transform) or a dish (how the plate is built); see inventory.models.Recipe.

The editor is one form with Save, like the dish editor: the draft is plain dicts and strings (`draft_from_recipe` /
`draft_from_post`), buttons post an `op` that changes it, and nothing is written until Save (`save_draft`).
"""
from django.db import transaction
from django.utils.translation import gettext as _, ngettext

from inventory.models import SCENES, Recipe, RecipeIngredient, RecipeStep, Transformation
from pos_server.models import Component, Dish, Ingredient


KINDS = [("component", "Component recipes", "Made ahead in Cookery and used in dishes"),
         ("transform", "Transformation recipes", "Change a component's state, like freezing, thawing or reheating"),
         ("dish", "Dish recipes", "How each plate is put together at the station"),
         ("none", "Not attached yet", "Attach these to a component or dish so Cookery can use them")]
FILTERS = [("all", "All"), ("component", "Components"), ("transform", "Transformations"), ("dish", "Dishes"), ("none", "Not attached")]
PROCESSES = dict(SCENES)


def human(minutes):
    """20 min, 2 h, 2 h 30 min, or — for nothing."""
    if not minutes:
        return "—"
    if minutes < 60:
        return _("%(m)d min") % {"m": minutes}
    h, m = divmod(minutes, 60)
    return (_("%(h)d h") % {"h": h}) + ((" " + _("%(m)d min") % {"m": m}) if m else "")


def unit_of(kind, obj, amount=2):
    """What the target is counted in: a component's unit, a transformation's starting component, plates for a dish."""
    if kind == "dish":
        return ngettext("plate", "plates", amount)
    component = obj.from_component if kind == "transform" else obj
    unit = component.unit_of_measurement  # amounts stay in the smallest unit here
    return ngettext("piece", "pieces", amount) if unit == "ea" else unit


def target_key(kind, obj):
    return f"{kind[0]}:{obj.pk}" if obj else ""


def targets():
    """[(group label, [(key, label, kind, obj)])] everything a recipe can be attached to."""
    comps = [(target_key("component", c), c.title, "component", c) for c in Component.objects.filter(archived=False).order_by("title")]
    tfs = [(target_key("transform", t), t.name, "transform", t) for t in Transformation.objects.select_related("from_component", "to_component")]
    dishes = [(target_key("dish", d), d.title, "dish", d) for d in Dish.objects.filter(archived=False).order_by("title")]
    return [(_("Components"), comps), (_("Transformations"), tfs), (_("Dishes"), dishes)]


def find_target(key):
    """(kind, obj) for a target key like "c:5", or None."""
    prefix, _sep, pk = (key or "").partition(":")
    model = {"c": (Component, "component"), "t": (Transformation, "transform"), "d": (Dish, "dish")}.get(prefix)
    if not model or not pk.isdigit():
        return None
    obj = model[0].objects.filter(pk=int(pk)).first()
    return (model[1], obj) if obj else None


def makes_text(recipe, target):
    if not target:
        return _("Not attached · makes %(n)s") % {"n": f"{recipe.yield_quantity:g}"}
    kind, obj = target
    n = recipe.yield_quantity
    if kind == "transform":
        return _("Batches of %(n)s %(unit)s · %(name)s") % {"n": f"{n:g}", "unit": unit_of(kind, obj, n), "name": obj.name}
    if kind == "dish" and n == 1:
        return _("Builds one %(dish)s") % {"dish": obj.title}
    return _("Makes %(n)s %(unit)s of %(name)s") % {"n": f"{n:g}", "unit": unit_of(kind, obj, n), "name": obj.title}


def recipe_list(query="", show="all"):
    recipes = Recipe.objects.prefetch_related("steps", "component", "dish", "transformations").order_by("title", "id")
    q = (query or "").strip().lower()
    rows = []
    for recipe in recipes:
        target = recipe.target()
        kind = target[0] if target else "none"
        name = getattr(target[1], "title", None) or getattr(target[1], "name", "") if target else ""
        if q and q not in (recipe.title or "").lower() and q not in name.lower():
            continue
        steps = list(recipe.steps.all())
        processes = list(dict.fromkeys(s.scene for s in steps))
        total = recipe.total_minutes or sum(s.duration_seconds for s in steps) // 60
        rows.append({"recipe": recipe, "kind": kind, "makes": makes_text(recipe, target), "title": str(recipe),
                     "processes": [{"key": p, "label": PROCESSES.get(p, p)} for p in processes],
                     "steps": ngettext("%(n)d step", "%(n)d steps", len(steps)) % {"n": len(steps)}, "time": human(total)})
    show = show if show in dict(FILTERS) else "all"
    filters = [{"key": key, "label": _(label), "count": sum(1 for r in rows if key == "all" or r["kind"] == key)} for key, label in FILTERS]
    sections = []
    for key, name, sub in KINDS:
        part = [r for r in rows if r["kind"] == key and (show == "all" or show == key)]
        if part:
            sections.append({"key": key, "name": _(name), "rows": part,
                             "sub": ngettext("%(n)d recipe", "%(n)d recipes", len(part)) % {"n": len(part)} + " · " + _(sub)})
    return {"sections": sections, "filters": filters, "show": show, "query": query.strip()}


# ---------- the editor's draft ----------

def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _num(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _plain(number):
    return f"{number:g}" if isinstance(number, float) else str(number)


def draft_from_recipe(recipe):
    target = recipe.target()
    return {
        "title": recipe.title, "target": target_key(*target) if target else "", "yield": _plain(recipe.yield_quantity),
        "total": str(recipe.total_minutes),
        "ings": [{"id": str(ri.id), "kind": "c" if ri.component_id else "i", "item": str(ri.component_id or ri.ingredient_id),
                  "qty": _plain(ri.quantity)} for ri in recipe.ingredients.all()],
        "steps": [{"id": str(s.id), "title": s.title, "text": s.text, "scene": s.scene, "timer": bool(s.duration),
                   "mins": str(s.duration_seconds // 60) if s.duration else ""} for s in recipe.steps.all()],
        "sel": 0,
    }


def draft_from_post(post):
    def rows(prefix):
        return range(min(_int(post.get(f"{prefix}count")) or 0, 200))

    hours, minutes = _int(post.get("total_h"), 0), _int(post.get("total_m"), 0)
    return {
        "title": post.get("title", "")[:140], "target": post.get("target", ""), "yield": post.get("yield", ""),
        "total": str(max(0, hours or 0) * 60 + max(0, min(59, minutes or 0))),
        "ings": [{"id": post.get(f"ing-{i}-id", ""), "kind": "c" if post.get(f"ing-{i}-kind") == "c" else "i",
                  "item": post.get(f"ing-{i}-item", ""), "qty": post.get(f"ing-{i}-qty", "")} for i in rows("ing-")],
        "steps": [{"id": post.get(f"step-{i}-id", ""), "title": post.get(f"step-{i}-title", "")[:80], "text": post.get(f"step-{i}-text", ""),
                   "scene": post.get(f"step-{i}-scene") if post.get(f"step-{i}-scene") in PROCESSES else "prep",
                   "timer": bool(post.get(f"step-{i}-timer")), "mins": post.get(f"step-{i}-mins", "")} for i in rows("step-")],
        "sel": _int(post.get("sel"), 0),
    }


def _timers(draft):
    return [max(1, min(1440, _int(s["mins"], 1) or 1)) if s["timer"] else 0 for s in draft["steps"]]


def apply_op(draft, op, post):
    """Steps (add, remove, move, timer on/off), ingredients, the selected step and "Use timers"."""
    name, _sep, arg = (op or "").partition(":")
    i = _int(arg)
    steps = draft["steps"]
    valid = i is not None and 0 <= i < len(steps)
    if name == "add_step":
        steps.append({"id": "", "title": "", "text": "", "scene": "prep", "timer": False, "mins": ""})
        draft["sel"] = len(steps) - 1
    elif name == "remove_step" and valid:
        steps.pop(i)
    elif name in ("step_up", "step_down") and valid:
        j = i - 1 if name == "step_up" else i + 1
        if 0 <= j < len(steps):
            steps[i], steps[j] = steps[j], steps[i]
            draft["sel"] = j
    elif name == "timer" and valid:
        steps[i]["timer"] = not steps[i]["timer"]
        steps[i]["mins"] = steps[i]["mins"] or "10"
        draft["sel"] = i
    elif name == "sel" and valid:
        draft["sel"] = i
    elif name == "use_timers":
        timers = _timers(draft)
        draft["total"] = str(max(sum(timers), max(timers, default=0)))
    elif name == "remove_ing" and i is not None and 0 <= i < len(draft["ings"]):
        draft["ings"].pop(i)
    # "Add an ingredient or component" joins the list whatever was pressed
    kind, _sep, pk = (post.get("add_item") or "").partition(":")
    model = {"i": Ingredient, "c": Component}.get(kind)
    if model and pk.isdigit() and model.objects.filter(pk=int(pk)).exists() and (kind, pk) not in {(g["kind"], g["item"]) for g in draft["ings"]}:
        draft["ings"].append({"id": "", "kind": kind, "item": pk, "qty": "1"})
    draft["sel"] = max(0, min(draft["sel"], len(steps) - 1))
    return draft


def problems(draft):
    """What stops a save, in the order the design shows them."""
    timers = _timers(draft)
    longest = max(timers, default=0)
    total = _int(draft["total"], 0) or 0
    untitled = sum(1 for s in draft["steps"] if not s["title"].strip())
    if not draft["title"].strip():
        return [_("Give the recipe a name")]
    if not draft["steps"]:
        return [_("Add at least one step")]
    if total <= 0:
        return [_("Set the total time")]
    if total < longest:
        return [_("Total time is shorter than a timer")]
    if untitled:
        return [ngettext("%(n)d step needs a title", "%(n)d steps need a title", untitled) % {"n": untitled}]
    if (_num(draft["yield"]) or 0) <= 0:
        return [_("Set how much it makes")]
    if any((_num(g["qty"]) or 0) <= 0 for g in draft["ings"]):
        return [_("Every ingredient needs an amount")]
    return []


def _canonical(draft):
    return (draft["title"].strip(), draft["target"], _num(draft["yield"]), _int(draft["total"], 0),
            tuple((g["id"], g["kind"], g["item"], _num(g["qty"])) for g in draft["ings"]),
            tuple((s["id"], s["title"].strip(), s["text"].strip(), s["scene"], s["timer"], _int(s["mins"]) if s["timer"] else None) for s in draft["steps"]))


def is_dirty(draft, recipe):
    return _canonical(draft) != _canonical(draft_from_recipe(recipe))


@transaction.atomic
def save_draft(draft, recipe):
    from datetime import timedelta
    recipe.title = draft["title"].strip()
    recipe.yield_quantity = _num(draft["yield"]) or 1
    recipe.total_minutes = _int(draft["total"], 0) or 0
    recipe.save()
    # Attach it: whatever pointed here lets go, and the target drops its old recipe for this one
    for model in (Component, Dish, Transformation):
        model.objects.filter(recipe=recipe).update(recipe=None)
    target = find_target(draft["target"])
    if target:
        type(target[1]).objects.filter(pk=target[1].pk).update(recipe=recipe)
    existing = {str(ri.id): ri for ri in recipe.ingredients.all()}
    keep = set()
    for position, g in enumerate(draft["ings"]):
        ri = existing.get(g["id"]) or RecipeIngredient(recipe=recipe)
        ri.ingredient_id = _int(g["item"]) if g["kind"] == "i" else None
        ri.component_id = _int(g["item"]) if g["kind"] == "c" else None
        ri.quantity, ri.position = _num(g["qty"]), position
        ri.save()
        keep.add(ri.id)
    recipe.ingredients.exclude(pk__in=keep).delete()
    existing = {str(s.id): s for s in recipe.steps.all()}
    keep = set()
    for position, d in enumerate(draft["steps"]):
        step = existing.get(d["id"]) or RecipeStep(recipe=recipe)
        step.title, step.text, step.scene, step.position = d["title"].strip(), d["text"].strip(), d["scene"], position
        step.duration = timedelta(minutes=max(1, min(1440, _int(d["mins"], 1) or 1))) if d["timer"] else None
        step.save()
        keep.add(step.id)
    recipe.steps.exclude(pk__in=keep).delete()
    return recipe


# ---------- what the editor shows ----------

def editor(recipe, draft, leave_ask=False):
    target = find_target(draft["target"])
    kind = target[0] if target else None
    yield_n = _num(draft["yield"]) or 0
    unit = unit_of(kind, target[1], yield_n) if target else _("units")
    timers = _timers(draft)
    total_timers, longest = sum(timers), max(timers, default=0)
    total = _int(draft["total"], 0) or 0
    taken = {}
    for k, related in (("c", Component.objects.exclude(recipe=None)), ("t", Transformation.objects.exclude(recipe=None)),
                       ("d", Dish.objects.exclude(recipe=None))):
        for obj in related:
            taken[f"{k}:{obj.pk}"] = obj.recipe_id
    groups = [{"label": label, "options": [{"key": key, "label": name, "taken": taken.get(key) not in (None, recipe.pk)} for key, name, _k, _o in items]}
              for label, items in targets()]
    if not target:
        kind_text, kind_hint = _("Not attached"), _("Cookery won't offer this recipe until it's attached to a component, transformation or dish.")
    elif kind == "transform":
        kind_text = _("Transformation recipe")
        kind_hint = _("Shows up under Transform in Cookery. Each batch moves %(n)s %(unit)s: %(from)s to %(to)s.") % {
            "n": f"{yield_n:g}", "unit": unit, "from": target[1].from_component.title.lower(), "to": target[1].to_component.title.lower()}
    elif kind == "component":
        kind_text = _("Component recipe")
        kind_hint = _("Shows up under Craft in Cookery. Finishing it records %(n)s %(unit)s of %(name)s.") % {"n": f"{yield_n:g}", "unit": unit, "name": target[1].title}
    else:
        kind_text = _("Dish recipe")
        kind_hint = _("How %(dish)s is put together at the station.") % {"dish": target[1].title}
        if taken.get(draft["target"]) not in (None, recipe.pk):
            kind_hint += " " + _("Another recipe is attached here too; this one will replace it.")
    if total and total < longest:
        total_hint = _("Can't be shorter than the longest timer (%(t)s).") % {"t": human(longest)}
    elif not total and not longest:
        total_hint = _("Set how long the whole recipe takes.")
    else:
        total_hint = _("Longest timer %(a)s · timers add up to %(b)s.") % {"a": human(longest), "b": human(total_timers)}

    ingredients = {str(i.id): i for i in Ingredient.objects.order_by("title")}
    components = {str(c.id): c for c in Component.objects.order_by("title")}
    used = {(g["kind"], g["item"]) for g in draft["ings"]}
    ings = []
    for n, g in enumerate(draft["ings"]):
        item = (ingredients if g["kind"] == "i" else components).get(g["item"])
        if item:
            ings.append({"i": n, "g": g, "item": item, "unit": item.unit_of_measurement, "component": g["kind"] == "c"})
    steps = []
    for n, s in enumerate(draft["steps"]):
        steps.append({"i": n, "s": s, "n": n + 1, "on": n == draft["sel"], "human": human(timers[n]) if s["timer"] else "",
                      "first": n == 0, "last": n == len(draft["steps"]) - 1})
    sel = draft["steps"][draft["sel"]] if draft["steps"] else None
    issues = problems(draft)
    dirty = is_dirty(draft, recipe)
    mins = timers[draft["sel"]] if sel and sel["timer"] else 0
    return {
        "recipe": recipe, "d": draft, "title": draft["title"].strip() or _("Untitled recipe"),
        "status": issues[0] if issues else (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issues else "dirty" if dirty else "", "can_save": dirty and not issues, "leave_ask": leave_ask,
        "groups": groups, "kind": kind or "none", "kind_text": kind_text, "kind_hint": kind_hint, "unit": unit,
        "yield_label": _("Batch size") if kind == "transform" else _("Makes"),
        "total_h": total // 60, "total_m": total % 60, "total_bad": bool(total and total < longest), "total_hint": total_hint,
        "use_timers": _("Use timers (%(t)s)") % {"t": human(total_timers)},
        "ings": ings, "ing_sub": (_("What goes in for one batch of %(n)s %(unit)s") if kind == "transform" else _("For one batch · makes %(n)s %(unit)s"))
        % {"n": f"{yield_n:g}", "unit": unit},
        "ingredient_choices": [i for k, i in ingredients.items() if ("i", k) not in used and not i.archived],
        "component_choices": [c for k, c in components.items() if ("c", k) not in used and not c.archived],
        "steps": steps, "processes": SCENES,
        "steps_sub": ngettext("%(n)d step", "%(n)d steps", len(steps)) % {"n": len(steps)} + " · "
        + ngettext("%(n)d with a timer", "%(n)d with a timer", sum(1 for t in timers if t)) % {"n": sum(1 for t in timers if t)} + " · "
        + ((_("%(t)s of timers") % {"t": human(total_timers)}) if total_timers else _("no timed steps")),
        "timeline": [{"title": f"{n + 1}: {s['title'] or _('Untitled')}" + (f" · {human(timers[n])}" if s["timer"] else ""),
                      "grow": max(1, round(timers[n] ** .5, 2)) if s["timer"] else 1, "timed": s["timer"], "on": n == draft["sel"]}
                     for n, s in enumerate(draft["steps"])],
        "pv": {"pos": _("%(a)d of %(b)d") % {"a": draft["sel"] + 1, "b": len(steps)} if steps else "—",
               "scene": sel["scene"] if sel else "prep", "process": PROCESSES.get(sel["scene"] if sel else "prep"),
               "title": (sel["title"] if sel else "") or _("Untitled step"),
               "text": (sel["text"] if sel else "") or _("Add a description so the cook knows what to do."),
               "timer": bool(mins), "clock": f"{mins // 60}:{mins % 60:02d}:00" if mins >= 60 else f"{mins:02d}:00",
               "prev": draft["sel"] - 1 if draft["sel"] > 0 else None,
               "next": draft["sel"] + 1 if draft["sel"] < len(steps) - 1 else None},
    }
