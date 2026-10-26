"""
Cookery (docs/mockups/cookery, inventory/static/inventory/cookery.js): making components in batches and moving
them between states (freezing, thawing).

- Components with crafting_option "craft" are made here. With a Recipe they cook in batches: the recipe's
  ingredients (times the batch size) are reserved for the batch when it starts (out of inventory, recorded as
  IngredientReservations), guided cooking walks through the steps with timers, then what was made is counted. If it
  comes out short of the recipe's yield, that share of the reserved ingredients is recorded as WasteIngredient. Without a recipe they're made one at a time and counted as they go;
  their ComponentIngredient amounts leave inventory per unit when it's finished.
- A Transformation turns one component into another over time: the amount leaves `from_component` when it starts
  and arrives in `to_component` (times the ratio) when it's marked done.
- Batches in progress are CookingSessions, shared: anyone on shift sees them and can pick them up.

act() performs one action, all or nothing; the pages are inventory/cookery_views.py.
"""
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from pos_server.models import Component, ComponentIngredient, Ingredient
from .models import CookingSession, IngredientReservation, Transformation, WasteIngredient, YieldEntry


class CookeryError(Exception):
    """A request that can't be done; the message is shown to staff."""


def _now_ms():
    return int(time.time() * 1000)


def _number(body, key, positive=True):
    try:
        value = float(body.get(key))
    except (TypeError, ValueError):
        raise CookeryError(_("That amount isn't a number."))
    if positive and not value > 0:
        raise CookeryError(_("The amount has to be more than zero."))
    return round(value, 3)


def _session(body, kind=None):
    session = CookingSession.objects.select_for_update().filter(pk=body.get("session"), finished_at=None, cancelled=False).first()
    if session is None:
        raise CookeryError(_("That batch is already finished."))
    if kind and session.kind != kind:
        raise CookeryError(_("That isn't a batch of that kind."))
    return session


def _adjust(model, pk, delta):
    """Add `delta` to an Ingredient's or Component's inventory (saved, so stock and screens follow)."""
    obj = model.objects.select_for_update().get(pk=pk)
    obj.inventory = round((obj.inventory or 0) + delta, 4)
    obj.save()
    return obj


def act(user, body):
    """
    Perform one Cookery action, all or nothing. Returns a dict merged into the response (e.g. {"session": id}).
    Raises CookeryError with a message for staff.
    """
    action = body.get("action")
    handler = ACTIONS.get(action)
    if handler is None:
        raise CookeryError(_("Unknown action."))
    with transaction.atomic():
        return handler(user, body) or {}


def start_cook(user, body):
    """Start making a component: with its recipe (ingredients x batch leave inventory now) or one at a time."""
    component = Component.objects.filter(pk=body.get("component"), crafting_option="craft").first()
    if component is None:
        raise CookeryError(_("That can't be made here."))
    recipe = component.recipe
    batch = 1
    if recipe:
        try:
            batch = max(1, min(int(body.get("batch") or 1), 20))
        except (TypeError, ValueError):
            raise CookeryError(_("The batch size isn't a number."))
    phase = "yield" if not recipe or body.get("phase") == "yield" else "guided"
    steps = recipe.steps.count() if recipe else 0
    session = CookingSession.objects.create(
        kind=CookingSession.COOK, component=component, recipe=recipe, batch=batch, started_by=user, holder=user,
        phase=phase, step=max(0, steps - 1) if phase == "yield" else 0)
    if recipe:
        # Reserve the ingredients: out of inventory now, so nothing else counts on them (components a recipe uses,
        # like a sauce in a build, just leave their stock)
        for ri in recipe.ingredients.select_related("ingredient"):
            if ri.component_id:
                _adjust(Component, ri.component_id, -ri.quantity * batch)
            elif not ri.ingredient.unlimited:
                _adjust(Ingredient, ri.ingredient_id, -ri.quantity * batch)
                IngredientReservation.objects.create(session=session, ingredient=ri.ingredient, quantity=ri.quantity * batch)
    return {"session": session.id}


def take(user, body):
    """Pick up a batch someone else started (or carry on with your own)."""
    session = _session(body)
    if session.holder_id != user.id:
        session.holder = user
        session.save(update_fields=["holder"])


def set_step(user, body):
    session = _session(body, CookingSession.COOK)
    steps = session.recipe.steps.count() if session.recipe else 0
    try:
        step = int(body.get("step"))
    except (TypeError, ValueError):
        raise CookeryError(_("Unknown step."))
    if not 0 <= step < max(steps, 1):
        raise CookeryError(_("Unknown step."))
    session.step = step
    session.save(update_fields=["step"])


def set_phase(user, body):
    session = _session(body, CookingSession.COOK)
    if body.get("phase") not in ("guided", "yield") or (body.get("phase") == "guided" and not session.recipe):
        raise CookeryError(_("Unknown phase."))
    session.phase = body["phase"]
    session.save(update_fields=["phase"])


def timer(user, body):
    """A step's timer: start, pause, resume, add (a minute) or reset."""
    session = _session(body, CookingSession.COOK)
    steps = list(session.recipe.steps.all()) if session.recipe else []
    try:
        step = steps[int(body.get("step"))]
    except (TypeError, ValueError, IndexError):
        raise CookeryError(_("Unknown step."))
    if not step.duration_seconds:
        raise CookeryError(_("That step has no timer."))
    key, now, op = str(body.get("step")), _now_ms(), body.get("op")
    timers = dict(session.timers)
    current = timers.get(key)
    if op == "start":
        timers[key] = {"end": now + step.duration_seconds * 1000, "dur": step.duration_seconds, "paused": None}
    elif op == "reset":
        timers.pop(key, None)
    elif current is None:
        raise CookeryError(_("Start the timer first."))
    elif op == "pause" and current["paused"] is None:
        timers[key] = {**current, "end": None, "paused": max(0, current["end"] - now) // 1000}
    elif op == "resume" and current["paused"] is not None:
        timers[key] = {**current, "end": now + current["paused"] * 1000, "paused": None}
    elif op == "add":
        if current["paused"] is not None:
            timers[key] = {**current, "dur": current["dur"] + 60, "paused": current["paused"] + 60}
        else:
            timers[key] = {**current, "dur": current["dur"] + 60, "end": max(current["end"], now) + 60000}
    elif op not in ("pause", "resume"):
        raise CookeryError(_("Unknown timer action."))
    session.timers = timers
    session.save(update_fields=["timers"])


def count(user, body):
    """Add an amount to the yield being counted."""
    session = _session(body, CookingSession.COOK)
    YieldEntry.objects.create(session=session, amount=_number(body, "amount"))
    if session.phase != "yield":
        session.phase = "yield"
        session.save(update_fields=["phase"])


def uncount(user, body):
    """Undo the last amount counted."""
    session = _session(body, CookingSession.COOK)
    last = session.log.order_by("-at", "-id").first()
    if last:
        last.delete()


def finish(user, body):
    """Done counting: what was made goes into inventory (one-at-a-time components take their ingredients now)."""
    session = _session(body, CookingSession.COOK)
    made = round(sum(entry.amount for entry in session.log.all()), 3)
    if made <= 0:
        raise CookeryError(_("Count what you made first."))
    component = _adjust(Component, session.component_id, made)
    if not session.recipe:
        for ci in ComponentIngredient.objects.filter(component_id=session.component_id).select_related("ingredient"):
            if not ci.ingredient.unlimited:
                _adjust(Ingredient, ci.ingredient_id, -ci.quantity * made)
    session.finished_at = timezone.now()
    session.made = made
    session.expected = session.recipe.yield_quantity * session.batch if session.recipe else None
    session.save()
    # Made less than the recipe says: the unused share of what was reserved is waste
    waste = []
    if session.expected and made < session.expected:
        share = (session.expected - made) / session.expected
        for reservation in session.reservations.select_related("ingredient"):
            amount = round(reservation.quantity * share, 3)
            if amount > 0:
                WasteIngredient.objects.create(ingredient=reservation.ingredient, quantity=amount, session=session)
                waste.append({"ingredient": reservation.ingredient_id, "qty": amount})
    waste.sort(key=lambda w: -w["qty"])
    minutes = max(1, round((session.finished_at - session.started_at).total_seconds() / 60))
    return {"done": {"component": component.id, "made": made, "expected": session.expected, "minutes": minutes,
                     "batch": session.batch, "recipe": session.recipe_id, "have": component.inventory, "waste": waste}}


def start_transform(user, body):
    """Start a transformation: the amount leaves the starting component right away."""
    transform = Transformation.objects.select_related("recipe").filter(pk=body.get("transform")).first()
    if transform is None:
        raise CookeryError(_("Unknown transform."))
    qty = _number(body, "qty")
    source = Component.objects.select_for_update().get(pk=transform.from_component_id)
    if qty > (source.inventory or 0) + 1e-9:
        raise CookeryError(_("Only %(qty)s on hand.") % {"qty": round(source.inventory or 0, 3)})
    _adjust(Component, source.id, -qty)
    now = timezone.now()
    session = CookingSession.objects.create(
        kind=CookingSession.TRANSFORM, transformation=transform, component=transform.from_component, quantity=qty,
        started_by=user, holder=user, started_at=now, ends_at=now + transform.takes)
    return {"session": session.id}


def finish_transform(user, body):
    """Mark a transformation done: it lands in the target component."""
    session = _session(body, CookingSession.TRANSFORM)
    transform = session.transformation
    _adjust(Component, transform.to_component_id, session.quantity * transform.ratio)
    session.finished_at = timezone.now()
    session.made = session.quantity * transform.ratio
    session.save()


def cancel_transform(user, body):
    """Call a transformation off: it goes back where it came from."""
    session = _session(body, CookingSession.TRANSFORM)
    _adjust(Component, session.transformation.from_component_id, session.quantity)
    session.cancelled = True
    session.finished_at = timezone.now()
    session.save()


ACTIONS = {
    "start": start_cook, "take": take, "step": set_step, "phase": set_phase, "timer": timer, "count": count,
    "uncount": uncount, "finish": finish, "transform": start_transform, "transform_done": finish_transform,
    "transform_cancel": cancel_transform,
}
