"""
The register (POS), its lock screen and its cash drawer.

- register: the sales screen. GET renders it with the menu catalog; POST places an order.
- register_quote: prices the cart while it is rung up (pos_server.pricing), so the screen never computes
  discounts or tax itself.
- register_lock: staff pick their name and type a 4-digit PIN to sign in on a registered device.
- cash_drawer: open the drawer with a starting amount, pay cash in/out, count it and close it.

A device's drawer is the open CashDrawerSession for its EligibleDevice (or for "no device" when the device
check is off, as in dev). Cash payments need an open drawer.
"""
import json
import logging
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib.auth import login, logout
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _

from gift_cards.models import GiftCard
from payments import square
from .devices import DEVICE_COOKIE, device_required, safe_next_url, token_is_registered
from .models import (CashDrawerSession, CashMovement, EligibleDevice, Menu, Order, OrderPayment, OrderStation,
                     RegisterSignIn, Station)
from .orders import OrderError, create_order
from .pricing import (ALLERGENS, DishKit, combo_queryset, dish_queryset, offers_for_register, price_cart, tax_rate,
                      to_cents, to_money)

logger = logging.getLogger(__name__)

PIN_ATTEMPTS = 5          # wrong PINs allowed per staff member and device ...
PIN_LOCKOUT_SECONDS = 60  # ... before they have to wait this long
RECENT_ON_LOCK_SCREEN = 3  # the lock screen offers PIN sign-in to this many of the device's latest users


def current_device(request):
    """The registered device making the request, or None (e.g. in dev with the device check off)."""
    token = request.get_signed_cookie(DEVICE_COOKIE, default=None)
    if token and token_is_registered(token):
        return EligibleDevice.objects.filter(token=token).first()
    return None


def open_drawer(device):
    return CashDrawerSession.objects.filter(device=device, closed_at__isnull=True).first()


def register_staff_required(view):
    """Staff only. On the register, signing in happens on the lock screen, so send people there."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            return view(request, *args, **kwargs)
        if request.method == "GET" and "text/html" in request.headers.get("Accept", "text/html"):
            return redirect(f'{reverse("register_lock")}?{urlencode({"next": request.get_full_path()})}')
        return JsonResponse({"message": _("Sign in on the register first.")}, status=401)
    return wrapper


def can_manual_discount(user) -> bool:
    return user.has_perm("pos_server.apply_manual_discount")


def staff_name(user) -> str:
    return user.first_name or user.username


def money_text(amount) -> str:
    return f"${amount:,.2f}"


# ---------- catalog ----------

def register_catalog(menu):
    """Everything the register needs to show and customize the active menu. Money is in cents."""
    if menu is None:
        return {"sections": [], "items": [], "dishes": {}, "combos": {}}
    stations = list(Station.objects.all())
    dishes = list(dish_queryset().filter(menu=menu, visible_in_menu=True).order_by("station__position", "id"))
    combos = list(combo_queryset().filter(menu=menu, active=True, archived=False).order_by("id"))
    in_combos = {option.dish_id for combo in combos for slot in combo.slots.all() for option in slot.options.all()}
    all_dishes = {dish.id: dish for dish in dishes}
    all_dishes.update(dish_queryset().in_bulk(in_combos - set(all_dishes)))

    items = [{"key": f"d{dish.id}", "type": "dish", "id": dish.id, "section": dish.station.code, "title": dish.title,
              "price": to_cents(dish.price), "available": dish.available} for dish in dishes]
    for combo in combos:
        station = combo.section_station() or (stations[0] if stations else None)
        # Each choice with something to choose is a step when the combo is added
        ask = next((slot for slot in combo.slots.all() if len(slot.options.all()) > (1 if slot.required else 0)), None)
        items.append({"key": f"c{combo.id}", "type": "combo", "id": combo.id, "section": station.code if station else "",
                      "title": combo.title, "price": to_cents(combo.standard_price()), "available": combo.available,
                      "ask": _("Choose a %(slot)s") % {"slot": ask.title.lower()} if ask else None})
    position = {station.code: index for index, station in enumerate(stations)}
    items.sort(key=lambda item: (position.get(item["section"], 99), item["type"] == "combo"))
    used = {item["section"] for item in items}

    def dish_json(dish):
        return {"id": dish.id, "title": dish.title, "price": to_cents(dish.price), "available": dish.available,
                "station": dish.station.code, "kit": DishKit(dish).as_json()}

    def condiments(combo):
        return [{"id": e.id, "title": e.label, "price": to_cents(e.price), "max": e.max_count, "available": e.available}
                for e in combo.extras.all()]

    return {
        "sections": [{"code": s.code, "name": s.heading} for s in stations if s.code in used],
        "items": items,
        "dishes": {dish_id: dish_json(dish) for dish_id, dish in all_dishes.items()},
        "combos": {combo.id: {
            "id": combo.id, "title": combo.title, "pricing": combo.pricing, "price": to_cents(combo.price),
            "percentOff": float(combo.percent_off),
            "slots": [{
                "id": slot.id, "title": slot.title, "picks": slot.picks, "required": slot.required,
                "options": [{"dish": o.dish_id, "price": to_cents(o.price), "default": o.is_default} for o in slot.options.all()],
            } for slot in combo.slots.all()],
            "condiments": condiments(combo),
        } for combo in combos},
    }


def drawer_line(device, user, drawer):
    parts = [device.name if device else _("Register"), staff_name(user)]
    parts.append(_("Drawer opened with %(amount)s") % {"amount": money_text(drawer.opening_amount)} if drawer else _("Drawer closed"))
    return " · ".join(parts)


@register_staff_required
@device_required
def register(request):
    """GET: the register. POST: place the order in the body (see place_order)."""
    if request.method == "POST":
        return place_order(request)
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    live = Menu.live_menus()
    menu = register_menu(request, live)
    device = current_device(request)
    RegisterSignIn.record(device, request.user)  # puts them on this register's lock screen
    drawer = open_drawer(device)
    next_number = (Order.objects.aggregate(last=Max("id"))["last"] or 0) + 1
    return render(request, "pos_server/register.html", {
        "nav": "register",
        "menu": menu,
        "menus": live,
        "can_switch_menu": len(live) > 1,
        "drawer_line": drawer_line(device, request.user, drawer),
        "boot": {
            "catalog": register_catalog(menu),
            "offers": offers_for_register(),
            "allergens": ALLERGENS,
            "taxRate": str(tax_rate()),
            "canManual": can_manual_discount(request.user),
            "drawerOpen": drawer is not None,
            "orderNumber": next_number,
            "urls": {
                "quote": reverse("register_quote"),
                "register": reverse("pos"),
                "card": reverse("register_card"),
                "catalog": reverse("register_catalog"),
                "drawer": reverse("cash_drawer"),
                "cardStatus": reverse("check_card_status"),
                "giftCard": "/gift-cards/card-api/",
            },
        },
    })


@register_staff_required
@device_required
def catalog(request):
    """The catalog again, refetched by the register when stock changes."""
    return JsonResponse(register_catalog(register_menu(request)))


def register_menu(request, live=None):
    """
    The menu this register rings up from: the one picked on it (kept in the session) while it's live, else the
    top live menu. None when no menu is live.
    """
    live = Menu.live_menus() if live is None else live
    picked = request.session.get("register_menu")
    return next((m for m in live if m.id == picked), live[0] if live else None)


def _read_cart(request):
    body = json.loads(request.body)
    return body, body.get("lines"), body.get("orderDiscount") or None


@register_staff_required
@device_required
def quote(request):
    """Price the cart being rung up. 400 with a message if a line can't be sold."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        _body, lines, order_discount = _read_cart(request)
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"message": _("The cart is malformed.")}, status=400)
    try:
        cart = price_cart(lines or [], order_discount=order_discount, channel="store", to_go=bool(_body.get("toGo")),
                          can_manual=can_manual_discount(request.user), strict=False)
    except OrderError as e:
        return JsonResponse({"message": str(e)}, status=400)
    return JsonResponse(cart.as_json())


def place_order(request):
    """
    Body: {"lines": [...], "orderDiscount": spec, "payments": [{"method": "cash"|"card"|"gift", "amount": "12.50",
    "tendered": "20.00", "number": "<gift card>"}], "total": "<what the cashier saw>", "name": ..., "toGo": bool,
    "note": ..., "noteForKitchen": bool}
    """
    try:
        body, lines, order_discount = _read_cart(request)
        payments = body.get("payments") or []
        gift = [(p["number"], p["amount"]) for p in payments if p.get("method") == "gift"]
        tenders = [p for p in payments if p.get("method") != "gift"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({"message": _("The cart is malformed.")}, status=400)
    if not payments:
        return JsonResponse({"message": _("Take a payment first.")}, status=400)
    device = current_device(request)
    note = (body.get("note") or "").strip()[:140]
    try:
        order = create_order(
            lines=lines or [],
            channel="store",
            initial_status=OrderStation.STATUS_APPROVED,
            order_discount=order_discount,
            payments=tenders,
            gift_card_payments=gift,
            can_manual_discount=can_manual_discount(request.user),
            expected_total=body.get("total"),
            drawer=open_drawer(device),
            cashier=request.user,
            device=device,
            special_instructions=note or None,
            note_for_kitchen=bool(body.get("noteForKitchen", True)),
            to_go_order=bool(body.get("toGo")),
            name=(body.get("name") or "").strip()[:140] or None,
        )
    except OrderError as e:
        return JsonResponse({"message": str(e)}, status=400)
    for payment in order.payments.filter(method=OrderPayment.CARD).exclude(square_checkout_id=""):
        try:  # for the receipt and refunds; the order stands either way
            square.fill_card_details(payment)
        except Exception:
            logger.warning("Couldn't get the card details of checkout %s", payment.square_checkout_id, exc_info=True)
    change = sum((p.change for p in order.payments.filter(method=OrderPayment.CASH)), Decimal("0"))
    return JsonResponse({"orderId": order.id, "total": to_cents(order.total), "change": to_cents(change),
                         "nextNumber": order.id + 1})


@register_staff_required
@device_required
def card_checkout(request):
    """
    Start a Square Terminal checkout for part or all of the order. Body: the order as for place_order, plus
    {"amount": "12.50"}. The cart is checked first (stock, discounts, the total the cashier saw, gift card
    balances), so the card isn't charged for an order that would then be refused.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        body, lines, order_discount = _read_cart(request)
        amount = to_money(body["amount"])
        paid = sum((to_money(p["amount"]) for p in body.get("payments", [])), Decimal("0"))
        gift = [(p["number"], to_money(p["amount"])) for p in body.get("payments", []) if p.get("method") == "gift"]
    except (ValueError, KeyError, TypeError, AttributeError, OrderError):
        return JsonResponse({"message": _("The amount is invalid.")}, status=400)
    try:
        cart = price_cart(lines or [], order_discount=order_discount, channel="store", to_go=bool(body.get("toGo")),
                          can_manual=can_manual_discount(request.user))
    except OrderError as e:
        return JsonResponse({"message": str(e)}, status=400)
    if body.get("total") is not None and to_money(body["total"]) != cart.total:
        return JsonResponse({"message": _("The total changed to $%(total)s. Please check the order and try again.") % {"total": cart.total}}, status=400)
    if amount <= 0 or amount > cart.total - paid:
        return JsonResponse({"message": _("The amount is invalid.")}, status=400)
    for number, part in gift:
        card = GiftCard.objects.filter(number=number).first()
        if card is None or card.available_balance < part:
            return JsonResponse({"message": _("Card x%(last4)s no longer has enough balance for $%(amount)s.") % {
                "last4": str(number)[-4:], "amount": part}}, status=402)
    try:
        response = square.gather_terminal_checkout(amount)
    except Exception as e:
        logger.exception("Starting a Terminal checkout failed")
        return JsonResponse({"message": str(e)}, status=502)
    if not response.is_success():
        return JsonResponse({"message": "; ".join(str(err.get("detail", err)) for err in response.errors or [])}, status=502)
    checkout = response.body["checkout"]
    cache.set("current_checkout_id", checkout["id"], timeout=600)
    cache.set("checkout_card_status", checkout["status"], timeout=600)
    return JsonResponse({"checkoutId": checkout["id"], "status": checkout["status"]})


# ---------- lock screen ----------

def recent_staff(device):
    """The staff with a PIN who used this register most recently (at most RECENT_ON_LOCK_SCREEN)."""
    recent = (RegisterSignIn.objects.filter(device=device, user__is_active=True, user__is_staff=True)
              .exclude(user__staff_profile__pin="").exclude(user__staff_profile__isnull=True)
              .select_related("user__staff_profile")[:RECENT_ON_LOCK_SCREEN])
    return [entry.user.staff_profile for entry in recent]


def _pin_key(device, staff_id):
    return f"pin-fails:{device.id if device else 0}:{staff_id}"


@device_required
def lock(request):
    """
    GET: the lock screen. It lists the few staff who used this register last (recent_staff); anyone else
    signs in with their password, which lists them next time.
    POST (form) action=lock: sign out and show the lock screen.
    POST {"staff": <Staff id>, "pin": "1234"}: sign that staff member in (only someone the screen lists).
    """
    device = current_device(request)
    if request.method == "GET":
        drawer = open_drawer(device)
        last_sale = Order.objects.filter(channel="store").order_by("-timestamp").values_list("timestamp", flat=True).first()
        staff = recent_staff(device)
        next_url = safe_next_url(request, request.GET.get("next")) or reverse("pos")
        return render(request, "pos_server/lock.html", {
            "device_name": device.name if device else _("The register"),
            "drawer": drawer,
            "last_sale": timezone.localtime(last_sale) if last_sale and timezone.localdate(last_sale) == timezone.localdate() else None,
            "staff": [{"id": s.id, "name": s.display_name} for s in staff],
            "next_url": next_url,
            "password_url": f'{reverse("login_view")}?{urlencode({"next": next_url})}',
            "signed_in": staff_name(request.user) if request.user.is_authenticated and request.user.is_staff else None,
        })
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.POST.get("action") == "lock":
        logout(request)
        return redirect(reverse("register_lock"))
    try:
        body = json.loads(request.body)
        staff_id, pin = int(body["staff"]), str(body["pin"])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"message": _("Pick your name and type your PIN.")}, status=400)
    key = _pin_key(device, staff_id)
    if cache.get(key, 0) >= PIN_ATTEMPTS:
        return JsonResponse({"message": _("Too many tries. Wait a minute, then try again.")}, status=429)
    staff = next((s for s in recent_staff(device) if s.id == staff_id), None)
    if staff is None or not staff.check_pin(pin):
        cache.set(key, cache.get(key, 0) + 1, timeout=PIN_LOCKOUT_SECONDS)
        return JsonResponse({"message": _("That PIN didn't match. Try again.")}, status=403)
    cache.delete(key)
    login(request, staff.user, backend="django.contrib.auth.backends.ModelBackend")
    RegisterSignIn.record(device, staff.user)
    drawer = open_drawer(device)
    return JsonResponse({
        "name": staff.display_name,
        "drawer": money_text(drawer.opening_amount) if drawer else None,
        "next": safe_next_url(request, body.get("next")) or reverse("pos"),
    })


# ---------- cash drawer ----------

def drawer_summary(drawer):
    """Totals for an open (or just closed) drawer. Money in cents."""
    payments = OrderPayment.objects.filter(drawer=drawer)
    cash = payments.filter(method=OrderPayment.CASH).aggregate(total=Sum("amount"), count=Count("id"))
    card = payments.filter(method=OrderPayment.CARD).aggregate(total=Sum("amount"), count=Count("id"))
    orders = Order.objects.filter(payments__drawer=drawer).distinct()
    order_stats = Order.objects.filter(pk__in=orders.values("pk")).aggregate(count=Count("id"), total=Sum("total"))
    moves = drawer.movements.aggregate(paid_in=Sum("amount", filter=Q(amount__gt=0)), paid_out=Sum("amount", filter=Q(amount__lt=0)))
    zero = Decimal("0")
    cash_total = cash["total"] or zero
    expected = drawer.opening_amount + cash_total + (moves["paid_in"] or zero) + (moves["paid_out"] or zero)
    count = order_stats["count"] or 0
    return {
        "opening": to_cents(drawer.opening_amount),
        "cashSales": to_cents(cash_total), "cashCount": cash["count"],
        "cardSales": to_cents(card["total"] or zero), "cardCount": card["count"],
        "paidIn": to_cents(moves["paid_in"] or zero), "paidOut": to_cents(-(moves["paid_out"] or zero)),
        "orders": count, "average": to_cents((order_stats["total"] or zero) / count) if count else 0,
        "expected": to_cents(expected),
        "openedAt": timezone.localtime(drawer.opened_at).isoformat(),
        "openedBy": staff_name(drawer.opened_by) if drawer.opened_by else "",
    }


@register_staff_required
@device_required
def cash_drawer(request):
    """
    GET: the drawer screen.
    POST {"action": "open", "amount": "200.00"} | {"action": "move", "amount": "-20.00", "reason": ...} |
         {"action": "close", "counted": "512.40"}
    """
    device = current_device(request)
    if request.method == "GET":
        drawer = open_drawer(device)
        last = CashDrawerSession.objects.filter(device=device, closed_at__isnull=False).order_by("-closed_at").first()
        return render(request, "pos_server/drawer.html", {
            "nav": "drawer",
            "device_name": device.name if device else _("Register"),
            "boot": {
                "drawer": drawer_summary(drawer) if drawer else None,
                "last": {"closedAt": timezone.localtime(last.closed_at).isoformat(), "left": to_cents(last.counted_amount)} if last else None,
                "next": safe_next_url(request, request.GET.get("next")),
                "user": staff_name(request.user),
                "device": device.name if device else _("Register"),
            },
        })
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        body = json.loads(request.body)
        action = body["action"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"message": _("Malformed request")}, status=400)
    with transaction.atomic():
        drawer = CashDrawerSession.objects.select_for_update().filter(device=device, closed_at__isnull=True).first()
        try:
            if action == "open":
                if drawer:
                    return JsonResponse({"message": _("The drawer is already open.")}, status=409)
                drawer = CashDrawerSession.objects.create(device=device, opened_by=request.user,
                                                          opening_amount=to_money(body.get("amount")))
                return JsonResponse({"drawer": drawer_summary(drawer)})
            if drawer is None:
                return JsonResponse({"message": _("The drawer isn't open.")}, status=409)
            if action == "move":
                raw = Decimal(str(body.get("amount")))
                amount = to_money(abs(raw)) * (1 if raw > 0 else -1)
                if amount == 0:
                    return JsonResponse({"message": _("Enter an amount.")}, status=400)
                CashMovement.objects.create(drawer=drawer, amount=amount, reason=str(body.get("reason") or "")[:140], user=request.user)
                return JsonResponse({"drawer": drawer_summary(drawer)})
            if action == "close":
                counted = to_money(body.get("counted"))
                summary = drawer_summary(drawer)
                drawer.counted_amount = counted
                drawer.expected_amount = Decimal(summary["expected"]) / 100
                drawer.closed_at = timezone.now()
                drawer.closed_by = request.user
                drawer.save()
                summary.update(counted=to_cents(counted), closedAt=timezone.localtime(drawer.closed_at).isoformat())
                return JsonResponse({"closed": summary})
        except (OrderError, ArithmeticError, ValueError, TypeError):
            return JsonResponse({"message": _("Enter a valid amount.")}, status=400)
    return JsonResponse({"message": _("Unknown action")}, status=400)
