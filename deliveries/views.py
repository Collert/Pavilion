"""
The courier app (design: the "Courier" screens of the Pavilion POS Register canvas). Server-rendered and kept
current by static/js/live-page.js (SSE "orders"); deliveries/static/deliveries/courier.js only does what HTML
can't (the photo preview, push sign-up).

  /deliveries/              start a shift (pick how you travel), then the orders looking for a courier
  /deliveries/order/<id>    one order: accept it or pass it to another courier
  /deliveries/current       your delivery: waiting for the restaurant, preparing, ready, on the way, drop-off
  /deliveries/done/<id>     delivered
  /deliveries/shift         POST start / end

Steps are Delivery.phase(). Delivery orders reach the kitchen display only once a courier accepts them
(deliveries.models.hidden_order_ids).
"""
import io
import json
import statistics
import time
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponseBadRequest, HttpResponseNotAllowed, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy, ngettext

from pos_server.devices import app_required
from pos_server.models import Order
from . import routes
from .models import CourierShift, Delivery, active_delivery
from .notifications import PushSubscription
from .signals import couriers_changed

MAX_PHOTO_BYTES = 15 * 1024 * 1024
PHOTO_SIZE = 1600  # longest side, px

PHASE_TEXT = {
    "confirm": gettext_lazy("Waiting for the restaurant to accept"), "prep": gettext_lazy("Kitchen is preparing it"),
    "ready": gettext_lazy("Ready for pickup"), "enroute": gettext_lazy("On the way to the customer"),
    "proof": gettext_lazy("Confirming drop-off"), "declined": gettext_lazy("Declined by the restaurant"),
}
STEPS = ("confirm", "prep", "ready", "enroute", "proof")
TRAVEL_TEXT = {CourierShift.WALKING: gettext_lazy("%(m)s min walk"), CourierShift.BICYCLING: gettext_lazy("%(m)s min ride"),
               CourierShift.DRIVING: gettext_lazy("%(m)s min drive")}


# ---------- helpers ----------

def _restaurant():
    from admin_portal.models import StoreSettings
    return StoreSettings.load().name or _("the restaurant")


def _minutes_since(dt):
    return max(0, int((timezone.now() - dt).total_seconds() // 60))


def _duration(minutes):
    """72 -> "1 h 12 m", 9 -> "9 min"."""
    if minutes >= 60:
        return _("%(h)s h %(m)s m") % {"h": minutes // 60, "m": minutes % 60}
    return _("%(m)s min") % {"m": minutes}


def _km(delivery):
    return f"{delivery.distance_m / 1000:.1f}".rstrip("0").rstrip(".") if delivery.distance_m is not None else None


def _lines(order):
    """[(quantity, title)] of what was sold, and the number of items."""
    lines = [(item.quantity, item.title) for item in order.items.all() if not item.parent_id]
    return lines, sum(quantity for quantity, _title in lines)


def _typical_prep_minutes():
    """How long the kitchen usually takes (median of recent orders), 3 to 45 minutes, 10 if there's no history."""
    value = cache.get("deliveries:typical-prep")
    if value is None:
        recent = [d.total_seconds() / 60 for d in Order.objects.exclude(prep_time=None).order_by("-id").values_list("prep_time", flat=True)[:30]]
        value = min(45, max(3, round(statistics.median(recent)))) if recent else 10
        cache.set("deliveries:typical-prep", value, 600)
    return value


def _page(request, template, context):
    shift = _shift_of(request.user)
    current = active_delivery(request.user) if shift else None
    return render(request, f"deliveries/{template}", {
        "now_ms": int(time.time() * 1000), "shift": shift, "current": current, "restaurant": _restaurant(),
        **context,
    })


def _shift_of(user):
    return CourierShift.objects.filter(user=user).first()


# ---------- screens ----------

@app_required("app.courier")
def home(request):
    """Start a shift, or (on shift) the delivery orders looking for a courier. ?end=1 opens the end-shift sheet."""
    shift = _shift_of(request.user)
    if shift is None:
        return _page(request, "start.html", {
            "day": date_format(timezone.localtime(), "l, F j"),
            "modes": [{"value": value, "label": label, "short": CourierShift.SHORT[value], "sub": sub} for (value, label), sub in zip(
                CourierShift.MODES,
                (_("Best for orders under 1.5 km"), _("Quick in traffic, up to about 5 km"), _("Longer trips and big orders")))],
        })
    open_deliveries = list(Delivery.objects.filter(order__isnull=False, courier=None).exclude(passed_by=request.user)
                           .select_related("order").prefetch_related("order__items").order_by("timestamp"))
    routes.fill_travel_times(open_deliveries)
    now = timezone.now()
    cards = []
    for delivery in open_deliveries:
        lines, count = _lines(delivery.order)
        ago = _minutes_since(delivery.order.timestamp)
        chips = []
        if (km := _km(delivery)) is not None:
            chips.append({"text": _("%(km)s km") % {"km": km}})
        chips.append({"text": ngettext("%(n)s item", "%(n)s items", count) % {"n": count}})
        if count >= 5:
            chips.append({"text": _("Large order"), "kind": "warn"})
        if delivery.order.start_time > now + timedelta(minutes=5):
            chips.append({"text": _("For %(time)s") % {"time": date_format(timezone.localtime(delivery.order.start_time), "TIME_FORMAT")}, "kind": "mint"})
        chips.append({"text": _("Just in") if ago <= 1 else _("%(m)s min ago") % {"m": ago}, "kind": "new" if ago <= 1 else "muted"})
        cards.append({"delivery": delivery, "eta": routes.minutes(delivery, shift.mode), "chips": chips})
    current = active_delivery(request.user)
    minutes = _minutes_since(shift.started_at)
    delivered = shift.delivered_count()
    context = {
        "cards": cards, "nav": "orders",
        "shift_line": _("%(time)s on shift · %(n)s delivered") % {"time": _duration(minutes), "n": delivered},
        "current_text": PHASE_TEXT.get(current.phase()) if current else "",
    }
    if request.GET.get("end"):
        busy = current is not None and current.phase() != "declined"
        context["end"] = {
            "stats": [(_duration(minutes), _("On shift")), (delivered, _("Delivered")), (shift.short_mode(), _("Transport"))],
            "blocked": _("Finish delivery #%(n)s before ending your shift. If something's wrong, call the restaurant.") % {"n": current.order_number} if busy else "",
        }
    return _page(request, "list.html", context)


@app_required("app.courier")
def shift(request):
    """POST action=start (mode) or action=end."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    action = request.POST.get("action")
    if action == "start":
        mode = request.POST.get("mode")
        if mode not in dict(CourierShift.MODES):
            messages.error(request, _("Pick how you're travelling first."))
            return redirect("delivery_orders")
        CourierShift.objects.update_or_create(user=request.user, defaults={"mode": mode, "started_at": timezone.now()})
        return redirect("delivery_orders")
    if action == "end":
        shift = _shift_of(request.user)
        if shift is None:
            return redirect("delivery_orders")
        current = active_delivery(request.user)
        if current and current.phase() != "declined":
            messages.error(request, _("Finish delivery #%(n)s before ending your shift.") % {"n": current.order_number})
            return redirect("delivery_orders")
        if current:
            current.delete()  # a declined order they hadn't dismissed
        n = shift.delivered_count()
        shift.delete()
        messages.success(request, ngettext("Shift ended · %(n)s delivery. Thanks, %(name)s!", "Shift ended · %(n)s deliveries. Thanks, %(name)s!", n)
                         % {"n": n, "name": request.user.first_name or request.user.username})
        return redirect("delivery_orders")
    return HttpResponseBadRequest("Unknown action")


@app_required("app.courier")
def order(request, id):
    """One delivery order. POST action=accept takes it (one delivery at a time), action=pass leaves it to others."""
    delivery = Delivery.objects.filter(pk=id).select_related("order", "courier").prefetch_related("order__items").first()
    shift = _shift_of(request.user)
    if shift is None:
        return redirect("delivery_orders")
    if delivery is None or delivery.order_id is None or delivery.delivered_at:
        messages.info(request, _("That order isn't available any more."))
        return redirect("delivery_orders")
    if delivery.courier_id == request.user.id:
        return redirect("delivery_current")
    if delivery.courier_id:
        messages.info(request, _("%(name)s already took order #%(n)s.") % {
            "name": delivery.courier.first_name or delivery.courier.username, "n": delivery.order_number})
        return redirect("delivery_orders")
    current = active_delivery(request.user)
    busy = current is not None and current.phase() != "declined"

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "pass":
            delivery.passed_by.add(request.user)
            messages.info(request, _("Order #%(n)s passed to another courier") % {"n": delivery.order_number})
            return redirect("delivery_orders")
        if action != "accept":
            return HttpResponseBadRequest("Unknown action")
        if busy:
            messages.error(request, _("Finish order #%(n)s first.") % {"n": current.order_number})
            return redirect("delivery_order", id=id)
        with transaction.atomic():
            taken = Delivery.objects.select_for_update().filter(pk=id, courier=None, order__isnull=False).update(
                courier=request.user, accepted_at=timezone.now())
            if current:
                current.delete()  # the declined one they hadn't dismissed yet
        if not taken:
            messages.info(request, _("Someone else just took order #%(n)s.") % {"n": delivery.order_number})
            return redirect("delivery_orders")
        couriers_changed(Delivery)  # update() sends no signals: the KDS shows the order now
        return redirect("delivery_current")

    routes.fill_travel_times([delivery])
    lines, count = _lines(delivery.order)
    ago = _minutes_since(delivery.order.timestamp)
    km = _km(delivery)
    return _page(request, "detail.html", {
        "delivery": delivery, "nav": "orders", "busy": busy, "current_number": current.order_number if busy else None,
        "lines": lines, "count": ngettext("%(n)s item", "%(n)s items", count) % {"n": count},
        "ago": _("Just in") if ago <= 1 else _("Placed %(m)s min ago") % {"m": ago},
        "distance": _("%(km)s km away") % {"km": km} if km is not None else None,
        "etas": [{"mode": mode, "minutes": routes.minutes(delivery, mode), "mine": mode == shift.mode} for mode, _label in CourierShift.MODES],
    })


@app_required("app.courier")
def current(request):
    """Your delivery, step by step. POST action = pickup | arrive | complete (handoff, photo) | dismiss (declined)."""
    shift = _shift_of(request.user)
    delivery = active_delivery(request.user)
    if shift is None or delivery is None:
        return redirect("delivery_orders")
    phase = delivery.phase()

    if request.method == "POST":
        return _current_post(request, delivery, phase)

    if phase == "declined":
        return _page(request, "declined.html", {"delivery": delivery, "nav": "delivery"})
    order = delivery.order
    routes.fill_travel_times([delivery])
    lines, count = _lines(order)
    eta = routes.minutes(delivery, shift.mode)
    index = STEPS.index(phase)
    context = {
        "delivery": delivery, "phase": phase, "nav": "delivery",
        "steps": [{"label": label, "state": "done" if n < index else "now" if n == index else "next"}
                  for n, label in enumerate((_("Accepted"), _("Preparing"), _("Ready"), _("Picked up"), _("Delivered")))],
        "count": ngettext("%(n)s item", "%(n)s items", count) % {"n": count}, "lines": lines,
        "eta": eta, "eta_text": TRAVEL_TEXT[shift.mode] % {"m": eta} if eta else "",
        "navigate": routes.maps_link(delivery.destination, shift.mode),
        "tel": f"tel:{delivery.phone}",
    }
    if phase == "prep":
        typical = _typical_prep_minutes()
        started = order.in_progress_since() or timezone.now()
        ends = started + timedelta(minutes=typical)
        left = max(0, (ends - timezone.now()).total_seconds())
        context["ready_ms"] = int(ends.timestamp() * 1000)
        context["prep_ms"] = typical * 60 * 1000
        # What live-page.js would show, so the first paint isn't blank: "5:42" and how full the bar is
        context["ready_left"] = f"{int(left // 60)}:{int(left % 60):02}" if left else _("a moment")
        context["prep_done"] = round(100 * (1 - left / (typical * 60)))
    return _page(request, "current.html", context)


def _current_post(request, delivery, phase):
    action = request.POST.get("action")
    now = timezone.now()
    if action == "dismiss" and phase == "declined":
        delivery.delete()
        return redirect("delivery_orders")
    if action == "pickup" and phase == "ready":
        with transaction.atomic():
            delivery.picked_up_at = now
            delivery.save(update_fields=["picked_up_at"])
            delivery.order.picked_up = True  # it leaves the kitchen display
            delivery.order.save()
        return redirect("delivery_current")
    if action == "arrive" and phase == "enroute":
        delivery.arrived_at = now
        delivery.save(update_fields=["arrived_at"])
        return redirect("delivery_current")
    if action == "complete" and phase == "proof":
        handoff = request.POST.get("handoff")
        if handoff not in dict(Delivery.HANDOFFS):
            messages.error(request, _("Say how you handed it over."))
            return redirect("delivery_current")
        upload = request.FILES.get("photo")
        if upload:
            try:
                photo = _proof_photo(upload)
            except ValueError as e:
                messages.error(request, str(e))
                return redirect("delivery_current")
            delivery.photo.save("proof.jpg", photo, save=False)
        elif handoff == Delivery.DOOR:
            messages.error(request, _("Take a photo of where you left it."))
            return redirect("delivery_current")
        delivery.handoff = handoff
        delivery.delivered_at = now
        delivery.save()
        return redirect("delivery_done", id=delivery.id)
    if action in ("dismiss", "pickup", "arrive", "complete"):
        messages.info(request, _("That step is already done."))
        return redirect("delivery_current")
    return HttpResponseBadRequest("Unknown action")


def _proof_photo(upload):
    """The uploaded photo as a JPEG, turned upright and at most PHOTO_SIZE px (location and other metadata dropped)."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if upload.size > MAX_PHOTO_BYTES:
        raise ValueError(_("That photo is too big. Try again."))
    try:
        image = ImageOps.exif_transpose(Image.open(upload)).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ValueError(_("That file isn't a photo."))
    image.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=82)
    return ContentFile(out.getvalue())


@app_required("app.courier")
def done(request, id):
    """Delivered: how it went, and the way back."""
    delivery = Delivery.objects.filter(pk=id, courier=request.user).exclude(delivered_at=None).first()
    if delivery is None:
        return redirect("delivery_orders")
    shift = _shift_of(request.user)
    mode = shift.mode if shift else CourierShift.DRIVING
    start = delivery.picked_up_at or delivery.accepted_at or delivery.timestamp
    trip = max(1, round((delivery.delivered_at - start).total_seconds() / 60))
    km = _km(delivery)
    delivered = shift.delivered_count() if shift else Delivery.objects.filter(
        courier=request.user, delivered_at__date=timezone.localdate()).count()
    colors = ("amber", "brand", "mint", "gold", "ink")
    return _page(request, "done.html", {
        "delivery": delivery, "nav": "delivery",
        "stats": [(_duration(trip), _("Pickup to door")),
                  (_("%(km)s km") % {"km": km} if km is not None else "–", _("Distance")),
                  (delivered, _("Delivered today"))],
        "home": routes.maps_link(routes.origin(), mode),
        "confetti": [{"left": (k * 41) % 100, "w": 7 + (k % 3) * 3, "h": 10 + (k % 4) * 3, "round": k % 2 == 0,
                      "color": colors[k % len(colors)], "delay": (k % 7) * 0.12} for k in range(22)],
    })


# ---------- web push ----------

def vapid_public_key(request):
    return JsonResponse({"vapidPublicKey": settings.VAPID_PUBLIC_KEY})


@app_required("app.courier")
def save_subscription(request):
    """POST a PushSubscription (JSON, as the browser gives it) for the signed-in courier."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
        endpoint, keys = data["endpoint"], data["keys"]
        p256dh, auth = keys["p256dh"], keys["auth"]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Expected a push subscription")
    PushSubscription.objects.update_or_create(user=request.user, endpoint=endpoint, defaults={"p256dh": p256dh, "auth": auth})
    return JsonResponse({"message": "Subscription saved."})
