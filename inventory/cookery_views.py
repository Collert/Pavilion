"""
Cookery pages (docs/mockups/cookery). Server-rendered: every screen is a Django template, every button a form that
POSTs here, does its work through inventory.cookery.act() and redirects back (post/redirect/get). static/js/
live-page.js submits those forms in the background and swaps in the parts of the page that changed, and refetches
the page when the SSE "cookery" or "inventory" topics say something changed elsewhere.

Amounts typed or tapped on screen are in the friendly unit (kg, L, pcs); cookery_tags.display_unit converts.
"""
import time
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy

from pos_server.devices import staff_required
from pos_server.models import Component
from . import cookery
from .models import CookingSession, IngredientReservation, Transformation, WasteIngredient
from .templatetags.cookery_tags import amount, display_unit, duration as _duration, shown, trim

LOW = 0.35  # below this share of par, a component is "Low"
SCENE_VERBS = {
    "boil": gettext_lazy("Boiling"), "simmer": gettext_lazy("Simmering"), "fry": gettext_lazy("Cooking"),
    "bake": gettext_lazy("Baking"), "rise": gettext_lazy("Proofing"), "rest": gettext_lazy("Resting"),
    "chill": gettext_lazy("Cooling"), "freeze": gettext_lazy("Freezing"), "thaw": gettext_lazy("Thawing"),
    "prep": gettext_lazy("Working"),
}


def _now_ms():
    return int(time.time() * 1000)


def _act(request, body):
    """Run a Cookery action; a problem becomes an error toast. Returns the action's result, or None if it failed."""
    try:
        return cookery.act(request.user, body)
    except cookery.CookeryError as e:
        messages.error(request, str(e))
        return None


def _in_page(request):
    """
    A form sent in the background by live-page.js (fetch), not a browser navigation (the browser says which in the
    standard Sec-Fetch-Mode header). Such a post that stays on the same page gets the page back directly instead of a
    redirect, saving the extra request.
    """
    return request.headers.get("Sec-Fetch-Mode") not in (None, "navigate")


def _page(request, template, context, mode="make"):
    return render(request, f"inventory/cookery/{template}", {"nav": "cookery", "mode": mode, "now_ms": _now_ms(), **context})


def _who(session, user):
    if session.holder_id == user.id:
        return _("You")
    return (session.holder.first_name or session.holder.username) if session.holder else _("Someone")


def _ago(dt):
    minutes = max(0, round((time.time() - dt.timestamp()) / 60))
    if minutes < 1:
        return _("just now")
    return _("%(m)s min ago") % {"m": minutes} if minutes < 60 else _("%(h)s h ago") % {"h": minutes // 60}


def _timer(timer, now):
    """A stored step timer as {"ends" (ms, running only), "left" (ms), "total" (ms), "paused", "done"}."""
    if not timer:
        return None
    if timer.get("paused") is not None:
        return {"ends": None, "left": timer["paused"] * 1000, "total": timer["dur"] * 1000, "paused": True, "done": False}
    left = max(0, timer["end"] - now)
    return {"ends": timer["end"], "left": left, "total": timer["dur"] * 1000, "paused": False, "done": left <= 0}


def _active_sessions():
    return (CookingSession.objects.filter(finished_at=None, cancelled=False)
            .select_related("holder", "started_by", "component", "recipe", "transformation__from_component", "transformation__to_component")
            .prefetch_related("log", "recipe__steps"))


def _ongoing_card(session, user, now):
    """One batch in progress, for the Ongoing strip."""
    card = {"id": session.id, "mine": session.holder_id == user.id, "sub": ""}
    if session.kind == CookingSession.TRANSFORM:
        tf = session.transformation
        card.update(name=tf.name, scene=tf.scene, line=f"{SCENE_VERBS.get(tf.scene, tf.name)} {shown(session.quantity, tf.from_component.unit_of_measurement)}",
                    ends=int(session.ends_at.timestamp() * 1000), ready_at=session.ends_at)
        card.update(running=card["ends"] > now, done=card["ends"] <= now, left=max(0, card["ends"] - now), has_timer=True)
    else:
        steps = list(session.recipe.steps.all()) if session.recipe else []
        timers = [(int(key), _timer(value, now)) for key, value in session.timers.items() if int(key) < len(steps)]
        best = min(timers, key=lambda kv: kv[1]["left"], default=None)
        card["name"] = session.component.title
        if session.phase == "yield" or not steps:
            made = sum(entry.amount for entry in session.log.all())
            card.update(line=_("Counting · %(qty)s so far") % {"qty": shown(made, session.component.unit_of_measurement)}, scene="prep")
        else:
            card.update(line=steps[session.step].title, scene=steps[best[0]].scene if best else steps[session.step].scene,
                        sub=_("Step %(n)s of %(of)s") % {"n": session.step + 1, "of": len(steps)} + " · ")
        timer = best[1] if best else None
        card.update(ends=timer and timer["ends"], left=timer and timer["left"], running=bool(timer and timer["ends"] and not timer["done"]),
                    done=bool(timer and timer["done"]), has_timer=bool(timer))
    card["sub"] += f"{_who(session, user)} · {_ago(session.started_at)}"
    return card


# ---------- home ----------

@staff_required
def home(request, mode="make"):
    """Craft (components to make) or Transform (transforms), with the batches in progress on top."""
    q = request.GET.get("q", "").strip()
    now = _now_ms()
    sessions = [_ongoing_card(s, request.user, now) for s in _active_sessions()]
    context = {"q": q, "ongoing": sessions, "home": True}
    if mode == "make":
        tiles = []
        components = Component.objects.filter(crafting_option="craft").select_related("recipe").prefetch_related("recipe__steps", "transforms_from")
        for c in components.order_by("title"):
            if q and q.lower() not in c.title.lower():
                continue
            recipe = c.recipe
            steps = list(recipe.steps.all()) if recipe else []
            pct = min(100, round(100 * (c.inventory or 0) / c.par)) if c.par else None
            tiles.append({
                "c": c, "recipe": recipe, "pct": pct or 0, "low": pct is not None and pct < LOW * 100,
                "makes": shown(recipe.yield_quantity, c.unit_of_measurement).replace(" " + display_unit("ea")[0], "") if recipe else "",
                "have": shown(c.inventory, c.unit_of_measurement), "par": trim((c.par or 0) / display_unit(c.unit_of_measurement)[1]) if c.par else "",
                "foot": _("Can be frozen") if c.transforms_from.exists() else (
                    _("%(n)s steps · %(time)s") % {"n": len(steps), "time": _duration(sum(s.duration_seconds for s in steps))} if recipe else _("Count as you make them")),
            })
        context["tiles"] = tiles
    else:
        transforms = Transformation.objects.select_related("from_component", "to_component")
        context["transforms"] = [tf for tf in transforms
                                 if not q or q.lower() in f"{tf.name} {tf.from_component.title} {tf.to_component.title}".lower()]
    return _page(request, "home.html", context, mode=mode)


@staff_required
def start(request, component_id):
    """POST: start making a component. With a recipe (batch, phase from the form) or one at a time."""
    if request.method != "POST":
        return redirect("crafting")
    result = _act(request, {"action": "start", "component": component_id, "batch": request.POST.get("batch"), "phase": request.POST.get("phase")})
    if not result:
        return redirect("cookery_recipe", component_id) if Component.objects.filter(pk=component_id, recipe__isnull=False).exists() else redirect("crafting")
    if Component.objects.filter(pk=component_id, recipe__isnull=False).exists():
        messages.success(request, _("Ingredients taken out of inventory"))
    return redirect("cookery_batch", result["session"])


@staff_required
def take(request, session_id):
    """POST: pick up a batch (yours or someone else's) and open it."""
    session = get_object_or_404(CookingSession, pk=session_id)
    if request.method == "POST" and session.holder_id != request.user.id and not session.finished_at:
        name = _who(session, request.user)
        if _act(request, {"action": "take", "session": session_id}) is not None:
            what = session.transformation.name if session.transformation else session.component.title
            messages.success(request, _("You picked up %(who)s's %(what)s") % {"who": name, "what": what.lower()})
    return redirect("cookery_transforming" if session.kind == CookingSession.TRANSFORM else "cookery_batch", session_id)


# ---------- recipe ----------

@staff_required
def recipe(request, component_id):
    """A component's recipe: batch size, ingredients (with what's on hand and held by other batches), steps."""
    c = get_object_or_404(Component.objects.select_related("recipe"), pk=component_id, crafting_option="craft")
    if not c.recipe:
        return redirect("crafting")
    try:
        batch = max(1, min(int(request.GET.get("batch", 1)), 10))
    except ValueError:
        batch = 1
    rec = c.recipe
    reserved = dict(IngredientReservation.objects.filter(session__finished_at=None, session__cancelled=False)
                    .values_list("ingredient").annotate(total=Sum("quantity")))
    rows, shorts = [], []
    for ri in rec.ingredients.select_related("ingredient", "component"):
        ing = ri.item  # an ingredient, or a component the recipe uses
        need = ri.quantity * batch
        short = not getattr(ing, "unlimited", False) and (ing.inventory or 0) < need
        if short:
            shorts.append(_("%(name)s (short %(qty)s)") % {"name": ing.title, "qty": amount(need - (ing.inventory or 0), ing.unit_of_measurement)})
        held = reserved.get(ing.id) if ri.ingredient_id else None
        rows.append({"ing": ing, "need": amount(need, ing.unit_of_measurement), "have": amount(ing.inventory, ing.unit_of_measurement),
                     "reserved": amount(held, ing.unit_of_measurement) if held else "", "short": short})
    steps = list(rec.steps.all())
    return _page(request, "recipe.html", {
        "c": c, "recipe": rec, "batch": batch, "rows": rows, "steps": steps,
        "shorts": ", ".join(shorts), "makes": shown(rec.yield_quantity * batch, c.unit_of_measurement),
        "makes_one": shown(rec.yield_quantity, c.unit_of_measurement),
        "total": sum(s.duration_seconds for s in steps), "timers": sum(1 for s in steps if s.duration),
    })


# ---------- a batch: guided cooking, then counting ----------

@staff_required
def batch(request, session_id):
    """GET: the batch (guided steps, or counting the yield). POST: step, timer, phase, count, uncount, finish."""
    session = get_object_or_404(CookingSession.objects.select_related("component", "recipe", "holder", "started_by"),
                                pk=session_id, kind=CookingSession.COOK)
    if session.finished_at:
        if session.cancelled or session.made is None:
            messages.info(request, _("That batch is finished."))
            return redirect("crafting")
        return redirect("cookery_done", session_id)
    if request.method == "POST":
        response = _batch_post(request, session)
        if response.url != request.path or not _in_page(request):
            return response
        session.refresh_from_db()  # stayed on this page: answer with it rather than a redirect
    if session.phase == "guided" and session.recipe:
        return _page(request, "guided.html", _guided_context(request, session))
    return _page(request, "yield.html", _yield_context(request, session))


def _batch_post(request, session):
    # A button sends action=verb or verb:argument (e.g. "step:3", "timer:pause", "phase:yield"); the argument can
    # also come in its own field (step=, op=, phase=), e.g. from a row of step buttons sharing one form.
    action, _sep, arg = request.POST.get("action", "").partition(":")
    body = {"action": action, "session": session.id}
    if action == "step":
        body["step"] = arg or request.POST.get("step")
    elif action == "timer":
        body.update(step=request.POST.get("step"), op=arg or request.POST.get("op"))
    elif action == "phase":
        body["phase"] = arg or request.POST.get("phase")
    elif action == "count":
        try:
            body["amount"] = float(request.POST.get("amount")) * display_unit(session.component.unit_of_measurement)[1]
        except (TypeError, ValueError):
            messages.error(request, _("That amount isn't a number."))
            return redirect("cookery_batch", session.id)
    elif action not in ("uncount", "finish"):
        messages.error(request, _("Unknown action."))
        return redirect("cookery_batch", session.id)
    result = _act(request, body)
    if action == "finish" and result:
        return redirect("cookery_done", session.id)
    return redirect("cookery_batch", session.id)


def _guided_context(request, session):
    now = _now_ms()
    steps = list(session.recipe.steps.prefetch_related("uses__ingredient", "uses__component"))
    i = min(session.step, len(steps) - 1)
    step = steps[i]
    timers = {int(k): _timer(v, now) for k, v in session.timers.items() if int(k) < len(steps)}
    segments = [{"n": k, "title": s.title, "state": "past" if k < i else "now" if k == i else
                 "running" if timers.get(k) and timers[k]["ends"] and not timers[k]["done"] else ""} for k, s in enumerate(steps)]
    others = [{"step": k, "title": steps[k].title, **t} for k, t in sorted(timers.items()) if k != i]
    uses = [{"amount": amount(ri.quantity * session.batch, ri.unit), "name": ri.item.title} for ri in step.uses.all()]
    timer = timers.get(i)
    card = None
    if step.duration:
        state = "idle" if not timer else "paused" if timer["paused"] else "done" if timer["done"] else "running"
        card = {"state": state, "running": state == "running", "timer": timer, "dur": step.duration_seconds, "total": timer["total"] if timer else step.duration_seconds * 1000,
                "left": timer["left"] if timer else step.duration_seconds * 1000, "verb": SCENE_VERBS.get(step.scene, _("Running")),
                "pct": round(100 * (1 - timer["left"] / timer["total"]), 1) if timer and timer["total"] else 0}
    return {"s": session, "c": session.component, "steps": steps, "i": i, "step": step, "last": i == len(steps) - 1,
            "segments": segments, "others": others, "uses": uses, "card": card, "next_steps": list(enumerate(steps))[i + 1:i + 4],
            "near_end": len(steps) - i <= 3, "who": _who(session, request.user)}


QUICK = {"ea": [(5, gettext_lazy("Handful")), (10, gettext_lazy("Half tray")), (20, gettext_lazy("Full tray"))],
         "g": [(0.5, gettext_lazy("Half kilo")), (1, gettext_lazy("One kilo")), (2, gettext_lazy("Two kilos"))],
         "ml": [(0.5, gettext_lazy("Half litre")), (1, gettext_lazy("One litre")), (5, gettext_lazy("Five litres"))]}


def _yield_context(request, session):
    c = session.component
    unit, factor = display_unit(c.unit_of_measurement)
    log = list(session.log.all())
    total = sum(entry.amount for entry in log) / factor
    expected = session.recipe.yield_quantity * session.batch / factor if session.recipe else 0
    per_unit = ""
    if not session.recipe:
        parts = [f"{amount(ci.quantity * factor, ci.ingredient.unit_of_measurement)} {ci.ingredient.title.lower()}"
                 for ci in c.componentingredient_set.select_related("ingredient")]
        if parts:
            per_unit = _("Each one takes %(parts)s out of inventory.") % {"parts": ", ".join(parts)}
    return {"s": session, "c": c, "unit": unit, "total": trim(total), "has_total": total > 0, "expected": trim(expected),
            "reached": session.recipe and total >= expected, "pct": min(100, round(100 * total / expected)) if expected else 0,
            "each": c.unit_of_measurement == "ea", "quick": QUICK.get(c.unit_of_measurement, QUICK["ea"]),
            "per_unit": per_unit, "decimal": c.unit_of_measurement != "ea",
            "log": [{"amount": trim(entry.amount / factor), "at": entry.at} for entry in reversed(log[-14:])],
            "who": _who(session, request.user), "mine": session.holder_id == request.user.id}


@staff_required
def done(request, session_id):
    """What a finished batch made, against the recipe, with the waste it logged and what to do next."""
    session = get_object_or_404(CookingSession.objects.select_related("component", "recipe"), pk=session_id,
                                kind=CookingSession.COOK, finished_at__isnull=False, cancelled=False)
    c = session.component
    unit, factor = display_unit(c.unit_of_measurement)
    made = (session.made or 0) / factor
    stats = [{"label": _("Made"), "value": f"{trim(made)} {unit}",
              "sub": _("batch ×%(n)s") % {"n": session.batch} if session.recipe else _("one at a time")}]
    if session.expected:
        expected = session.expected / factor
        over = made > expected
        waste = max(0, expected - made)
        pct = round(100 * waste / expected) if expected else 0
        logged = [f"{amount(w.quantity, w.ingredient.unit_of_measurement)} {w.ingredient.title.lower()}"
                  for w in session.waste.select_related("ingredient").order_by("-quantity")[:2]]
        stats.append({"label": _("Expected"), "value": f"{trim(expected)} {unit}", "sub": _("from the recipe")})
        stats.append({"label": _("Extra") if over else _("Waste"), "warn": not over and pct >= 10,
                      "value": f"+{trim(made - expected)} {unit}" if over else f"{trim(waste)} {unit}",
                      "sub": _("more than the recipe says") if over else (f"{pct}%" + (" · " + _("logged as waste: %(what)s") % {"what": ", ".join(logged)} if logged else "")) if waste else _("none, nice work")})
    minutes = max(1, round((session.finished_at - session.started_at).total_seconds() / 60))
    stats.append({"label": _("Time"), "value": _duration(minutes * 60), "sub": _("from start to finish")})
    title = (_("Yay! You made %(n)s %(what)s") % {"n": trim(made), "what": c.title.lower()} if c.unit_of_measurement == "ea"
             else _("Yay! You made %(n)s %(unit)s of %(what)s") % {"n": trim(made), "unit": unit, "what": c.title.lower()})
    colors = ["#F4A93B", "#1F4A3A", "#DCEBE2", "#F4C46B", "#14261F", "#E8862B"]
    confetti = [{"left": (k * 37) % 100, "w": 8 + (k % 3) * 3, "h": 12 + (k % 4) * 3, "radius": "3px" if k % 2 else "50%",
                 "color": colors[k % len(colors)], "delay": (k % 9) * 120} for k in range(36)]
    return _page(request, "done.html", {"s": session, "c": c, "title": title, "stats": stats, "confetti": confetti,
                                        "next": c.transforms_from.select_related("to_component")})


# ---------- transforms ----------

@staff_required
def transform(request, transform_id):
    """GET: how many to move (?qty in the friendly unit). POST: start it."""
    tf = get_object_or_404(Transformation.objects.select_related("from_component", "to_component", "recipe"), pk=transform_id)
    source = tf.from_component
    unit, factor = display_unit(source.unit_of_measurement)
    available = (source.inventory or 0) / factor
    if request.method == "POST":
        try:
            qty = float(request.POST.get("qty")) * factor
        except (TypeError, ValueError):
            messages.error(request, _("That amount isn't a number."))
            return redirect("cookery_transform", transform_id)
        result = _act(request, {"action": "transform", "transform": tf.id, "qty": min(qty, source.inventory or 0)})
        if not result:
            return redirect("cookery_transform", transform_id)
        messages.success(request, _("%(verb)s %(qty)s. It's in Ongoing now.") % {"verb": SCENE_VERBS.get(tf.scene, tf.name), "qty": shown(min(qty, source.inventory or 0), source.unit_of_measurement)})
        return redirect("cookery_transforming", result["session"])
    try:
        typed = float(request.GET["qty"])
    except (KeyError, ValueError):
        typed = tf.recipe.yield_quantity / factor if tf.recipe else available  # a recipe's batch, else everything
    qty = max(0, min(typed, available))
    step = lambda n: f"?{urlencode({'qty': trim(n)})}"
    quick = [(n, str(n)) for n in (10, 20) if n < available] + [(available, _("All %(n)s") % {"n": trim(available)})]
    return _page(request, "transform.html", {
        "tf": tf, "unit": unit, "qty": trim(qty), "qty_value": qty, "available": trim(available), "over": typed > available,
        "less": step(max(0, qty - 1)), "more": step(min(available, qty + 1)), "can_less": qty > 0, "can_more": qty < available,
        "quick": [{"label": label, "url": step(n), "on": abs(n - qty) < 1e-9} for n, label in quick],
        "verb": SCENE_VERBS.get(tf.scene, tf.name), "ready_at": timezone.now() + tf.takes,
        "dur": int(tf.takes.total_seconds()), "steps": list(tf.recipe.steps.all()) if tf.recipe else [],
    }, mode="transform")


@staff_required
def transforming(request, session_id):
    """GET: a transform under way. POST action=done (it lands in the target) or cancel (it goes back)."""
    session = get_object_or_404(CookingSession.objects.select_related("transformation__from_component", "transformation__to_component", "holder", "started_by"),
                                pk=session_id, kind=CookingSession.TRANSFORM)
    tf = session.transformation
    if session.finished_at:
        messages.info(request, _("That transform is finished."))
        return redirect("cookery_transforms")
    if request.method == "POST":
        action = request.POST.get("action")
        qty = shown(session.quantity, tf.from_component.unit_of_measurement)
        if action == "done" and _act(request, {"action": "transform_done", "session": session.id}) is not None:
            messages.success(request, _("%(qty)s moved to %(to)s") % {"qty": qty, "to": tf.to_component.title.lower()})
        elif action == "cancel" and _act(request, {"action": "transform_cancel", "session": session.id}) is not None:
            messages.success(request, _("Cancelled. %(qty)s back in %(from)s") % {"qty": qty, "from": tf.from_component.title.lower()})
        return redirect("cookery_transforms")
    now = _now_ms()
    ends = int(session.ends_at.timestamp() * 1000)
    dur = (session.ends_at - session.started_at).total_seconds() * 1000 if session.ends_at and session.started_at else tf.takes.total_seconds() * 1000
    return _page(request, "transforming.html", {
        "s": session, "tf": tf, "ends": ends, "left": max(0, ends - now), "done": ends <= now, "running": ends > now, "total": int(dur),
        "pct": min(100, round(100 * (dur - max(0, ends - now)) / dur)) if dur else 100, "verb": SCENE_VERBS.get(tf.scene, tf.name),
        "mine": session.holder_id == request.user.id, "who": _who(session, request.user),
        "on_the_way": shown(session.quantity * tf.ratio, tf.to_component.unit_of_measurement),
    }, mode="transform")
