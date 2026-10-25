from django.shortcuts import render
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse, HttpResponseForbidden, HttpResponseBadRequest, HttpResponseNotAllowed
from django.db import transaction
from decimal import Decimal
from django.urls import reverse
from django.core import serializers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import *
import json
from functools import wraps
from users.access import can

from .devices import DEVICE_COOKIE, api_app_required, app_required, superuser_required, device_required, is_registered_device, set_device_cookie, token_is_registered, safe_next_url
from .orders import OrderError, collect_order, create_order, orders_for_screens, refund_gift_cards, station_list
from django.utils import timezone

import datetime
import math
from payments import square
from payments.models import SquareTerminal
from pavilion.realtime import publish
import os
from square.utilities.webhooks_helper import is_valid_webhook_event_signature
from django.conf import settings
from misc_tools import funcs
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from inventory.views import craft_component
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Max, Q
from django.utils.timezone import make_aware, now
from django.core.cache import cache
from gift_cards.models import GiftCard
from online_store.models import RejectedOrder
from django.utils.translation import gettext_lazy as _
import logging

logger = logging.getLogger(__name__)


def index(request):
    return HttpResponseRedirect(reverse("pos"))

# Quick reasons on the KDS reject sheet: code -> what the customer is told.
REJECT_REASONS = [
    ("stock", _("Item out of stock")),
    ("capacity", _("Kitchen at capacity")),
    ("closing", _("Closing soon")),
    ("change", _("Can't make a requested change")),
    ("allergy", _("Can't accommodate the allergy")),
    ("duplicate", _("Duplicate order")),
]
REJECT_TEXT_LIMIT = 200


def station_filters(values, codes):
    """The stations a KDS covers, from ?filter=kitchen&filter=bar or ?filter=kitchen,bar. None given = all."""
    picked = {code.strip() for value in values for code in value.split(",")}
    return [code for code in codes if code in picked] or list(codes)


@app_required("app.marking")
@device_required
def order_marking(request):
    """
    The kitchen display (KDS). ?filter=<station code> (repeatable, or comma-separated) picks the stations this
    screen works for; without it the screen covers every station. ?auto-ready=<min>&auto-pickup=<min> turn on the
    screen's auto steps (run by the screen, see order-marking.js), timed from OrderStation.confirmed_at/ready_at.

    GET renders the screen with every active order (the screen filters them and refetches `active_orders` on
    each SSE "orders" event). POST {orderId, action, filters} acts for the stations in `filters`:
      confirm  pending -> being made. Once no station is pending, an online card payment is captured.
      reject   {reasons: [codes], text}: voids/refunds payments, records a RejectedOrder, deletes the order.
      ready    being made -> done. Only once every station has confirmed.
      pickup   marks the order picked up. Only once every station is done; delivery orders go to the courier.
    """
    if request.method == "GET":
        stations = station_list()
        codes = [station["code"] for station in stations]
        return render(request, "pos_server/order-marking.html", {
            "orders": [collect_order(order) for order in orders_for_screens(active_orders_queryset().order_by("start_time"))],
            "filters": station_filters(request.GET.getlist("filter"), codes),
            "stations": stations,
            "reasons": [{"code": code, "label": str(label)} for code, label in REJECT_REASONS],
            "can_reject": can(request.user, "act.reject"),  # users/access.py: a group gives it
            "server_now": now().isoformat(),  # the screen corrects its clock with this for the auto timers
        })
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    try:
        body = json.loads(request.body)
        order = Order.objects.get(id=body["orderId"])
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Expected a JSON body with orderId")
    except Order.DoesNotExist:
        return JsonResponse({"error": _("That order is gone. It was picked up or rejected on another screen.")}, status=404)
    filters = body.get("filters") or []
    if not isinstance(filters, list):
        return HttpResponseBadRequest("filters must be a list of station codes")
    action = body.get("action")
    statuses = order.statuses()

    if action == "confirm":
        order.set_station_status(filters, OrderStation.STATUS_APPROVED, only_from=[OrderStation.STATUS_PENDING])
        all_confirmed = OrderStation.STATUS_PENDING not in order.statuses()
        payment_error = None
        if all_confirmed and order.authorization:
            try:
                square.capture_authorization(order.authorization)
            except Exception as e:
                logger.exception("Capturing payment for order %s failed", order.id)
                payment_error = str(e)
        return JsonResponse({"all_confirmed": all_confirmed, "payment_error": payment_error})

    if action == "ready":
        if OrderStation.STATUS_PENDING in statuses:
            return JsonResponse({"error": _("Every station has to confirm the order first.")}, status=409)
        changed = order.set_station_status(filters, OrderStation.STATUS_DONE, only_from=[OrderStation.STATUS_APPROVED])
        all_ready = order.all_stations_done()
        if all_ready and changed:  # this press finished it (not a repeat)
            from . import order_emails
            order_emails.ready(order)
        return JsonResponse({"all_ready": all_ready})

    if action == "pickup":
        if not order.all_stations_done():
            return JsonResponse({"error": _("The order isn't ready at every station yet.")}, status=409)
        if order.channel == "delivery":
            return JsonResponse({"error": _("The courier picks up delivery orders.")}, status=409)
        order.picked_up = True
        order.save()
        return JsonResponse({"picked_up": True})

    if action == "reject":
        if not can(request.user, "act.reject"):
            return JsonResponse({"error": _("Rejecting orders isn’t in your groups. Ask a manager.")}, status=403)
        labels = dict(REJECT_REASONS)
        codes = body.get("reasons") or []
        text = str(body.get("text") or "").strip()[:REJECT_TEXT_LIMIT]
        if not isinstance(codes, list) or any(code not in labels for code in codes):
            return HttpResponseBadRequest("Unknown rejection reason")
        reason = "; ".join(str(labels[code]) for code in dict.fromkeys(codes))
        reason = f"{reason}: {text}" if reason and text else reason or text
        if not reason:
            return JsonResponse({"error": _("Pick a reason or write one.")}, status=400)
        if order.authorization:
            try:
                square.void_authorization(order.authorization)
            except Exception as e:
                logger.exception("Voiding payment for order %s failed", order.id)
                return JsonResponse({"error": _("Couldn't refund the card payment, so the order was kept: %(error)s") % {"error": e}}, status=502)
        with transaction.atomic():
            RejectedOrder.objects.create(order_id=order.id, reason=reason, timestamp=order.timestamp)
            refund_gift_cards(order)
            order.delete()
        return JsonResponse({"rejected": True, "reason": reason})

    return HttpResponseBadRequest("Unknown action")

@app_required("app.register")
def menu_select(request):
    """POST: ring up from another live menu on this register (kept in the session). Back to the register."""
    if request.method == "POST":
        menu = next((m for m in Menu.live_menus() if str(m.id) == request.POST.get("menu")), None)
        if menu:
            request.session["register_menu"] = menu.id
    return redirect(reverse("pos"))

def can_add_devices(user) -> bool:
    """Who may make a browser into a device: owners, and groups with the admin's Devices page (edit)."""
    from users.access import can_page
    return bool(user and user.is_authenticated and (user.is_superuser or can_page(user, "devices", "edit")))


def device_elig(request):
    """
    Set up this device (pos_server/templates/pos_server/ineligible-device.html; laid out like the sign-in page).
    Staff screens send unregistered browsers here. A browser that remembers its device (its token in
    localStorage) reconnects by itself (PUT). Otherwise:
      POST op=add   a new device named here: by someone who may add devices (signed in already, or signing in now)
      POST op=join  this browser becomes a device that exists (people who may add devices)
      POST op=code  a device code pasted from the admin's Devices page
    Afterwards the page keeps the token in localStorage and goes on to `next`.
    """
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next")) or reverse("pos")
    if request.method == "PUT":
        try:
            token = json.loads(request.body)["token"]
        except (ValueError, KeyError, TypeError):
            return HttpResponseForbidden("Forbidden: Device not recognized.")
        if token_is_registered(token):
            return set_device_cookie(JsonResponse({"status": "device ok"}, status=200), token)
        return HttpResponseForbidden("Forbidden: Device not recognized.")
    if request.method == "GET" and token_is_registered(request.get_signed_cookie(DEVICE_COOKIE, default=None)):
        return redirect(next_url)  # already one of the store's devices (e.g. it just opened a kiosk link)
    context = {"next_url": next_url, "error": "", "view": "add", "name": "", "identifier": ""}
    user = request.user if can_add_devices(request.user) else None
    if request.method == "POST":
        op = request.POST.get("op", "add")
        device = None
        if op == "code":
            code = request.POST.get("code", "").strip()
            device = EligibleDevice.objects.filter(token=code).first() if token_is_registered(code) else None
            if device is None:
                context.update(error=_("That device code isn’t one of this store’s devices."), view="code")
        elif op == "join" and user:
            device = EligibleDevice.objects.filter(pk=request.POST.get("device") or 0).first()
        elif op == "add":
            name = request.POST.get("name", "").strip()[:140]
            context["name"] = name
            if user is None:  # sign in now, as someone who may add devices
                identifier = request.POST.get("username", "").strip()
                context["identifier"] = identifier
                username = User.objects.filter(email__iexact=identifier).values_list("username", flat=True).first() if "@" in identifier else identifier
                signed = authenticate(request, username=username or identifier, password=request.POST.get("password", ""))
                user = signed if can_add_devices(signed) else None
                if signed is None:
                    context["error"] = _("That email or username and password don’t match.")
                elif user is None:
                    context["error"] = _("Your groups don’t let you add devices. Ask an owner.")
            if user and not name:
                context["error"] = _("Name this device, e.g. Front register.")
            elif user:
                device = EligibleDevice.objects.create(name=name)
        if device is not None:
            response = render(request, "pos_server/ineligible-device.html", {**context, "view": "done", "device": device})
            return set_device_cookie(response, device.token)
    context.update(signed_in=user, devices=EligibleDevice.objects.all() if user else [])
    return render(request, "pos_server/ineligible-device.html", context)


def group_by_station(dishes):
    """[(Station, [dishes...]), ...] in station order, skipping empty stations."""
    sections = {}
    for dish in dishes:
        sections.setdefault(dish.station, []).append(dish)
    return sorted(sections.items(), key=lambda pair: (pair[0].position, pair[0].id))

@app_required("app.register")
def pos_out_display(request):
    """
    The customer-facing display: the active menu, and the cart while the cashier rings it up. The cart
    arrives from the register over BroadcastChannel, so this must run in the same browser as the register.
    """
    menu = Menu.active()
    return render(request, "pos_server/pos-output-display.html", {
        "menu": menu,
        "compiled_menu": compile_menu(menu) if menu else [],
    })

def compile_menu(menu):
    """[(Station, [prettified dishes and combos])] in station order, for the customer display."""
    dishes = menu.dishes.select_related("station").prefetch_related("dishcomponent_set__component").order_by("station__position", "id")
    categories = {station: [prettify_dish(dish) for dish in items] for station, items in group_by_station(dishes)}
    for combo in menu.combos.prefetch_related("slots__options__dish__station").filter(active=True, archived=False):
        station = combo.section_station()
        if station is not None:
            categories.setdefault(station, []).append(prettify_combo(combo))
    return sorted(categories.items(), key=lambda pair: (pair[0].position, pair[0].id))

def prettify_combo(combo):
    """A combo for menu boards: its default picks as the description."""
    return {
        "title": combo.title,
        "components": ", ".join(option.dish.title.lower() for option in combo.default_picks()),
        "price": format_float(combo.standard_price()),
        "available": combo.available,
    }

def prettify_dish(dish):
    """
    A dish for menu boards and the online dish page: title, price, availability, and its components as
    readable text ("8 perogies, a cup of coffee").
    """
    final_dish = {
        "title":dish.title,
        "components":"",
        "price":format_float(dish.price),
        "available":dish.available and dish.visible_in_menu,
    }
    dcs = dish.dishcomponent_set.all()
    for index, dc in enumerate(dcs):
        if dc.component.category != "drink":
            if dc.component.unit_of_measurement == "ml":
                quantity_str = _("a bowl of ")
            elif dc.component.unit_of_measurement == "g":
                    quantity_str = f"{int(dc.quantity)}{dc.component.unit_of_measurement} of "
            else:
                if dc.quantity == 1:
                    quantity_str = ""
                elif dc.quantity < 1:
                    quantity_str = _("a piece of ")
                else:
                    quantity_str = f"{int(dc.quantity)} "
        else:
            if dc.quantity == 1:
                quantity_str = _("a cup of ")
            else:
                quantity_str = _("%(count)s cups of ") % {"count": int(dc.quantity)}
        final_dish["components"] += f"{quantity_str}"
        final_dish["components"] += f"{dc.component.title.lower()}"
        if dc.quantity > 1 and dc.component.category != "drink" and not (dc.component.unit_of_measurement == "g" or dc.component.unit_of_measurement == "kg"):
            final_dish["components"] += "s"
        if index != len(dcs) - 1:
            final_dish["components"] += ", "
    return final_dish

def format_float(num:float) -> str:
    """
    Format a floating-point number to a string, removing unnecessary trailing zeros.

    If the number is an integer, it will be formatted without any decimal point.
    Otherwise, it will be formatted to a maximum of 10 decimal places, with trailing
    zeros and the decimal point removed if they are not needed.

    Args:
        num (float): The floating-point number to format.

    Returns:
        str: The formatted number as a string.
    """
    num = float(num)
    if num.is_integer():
        return f"{int(num)}"
    # Otherwise, format the number to remove trailing zeros.
    else:
        return f"{num:.10f}".rstrip("0").rstrip(".")

@login_required
@user_passes_test(lambda u: u.is_superuser)
def pair_square_terminal(request):
    """
    Every Square Terminal and the register it takes card payments for. Registers pair their own (the register's
    Screen & terminal sheet); here a superuser can pair one for any register, check a code, or free a Terminal.
    """
    error = None
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "new":
                register = EligibleDevice.objects.filter(pk=request.POST.get("register") or 0).first()
                square.pair_terminal(register, request.POST.get("name", ""))
            elif action == "check":
                square.refresh_device_code(SquareTerminal.objects.get(pk=request.POST.get("id")))
            elif action == "free":
                SquareTerminal.objects.filter(pk=request.POST.get("id")).update(register=None)
            return redirect(reverse("pair-terminal"))
        except Exception as e:
            logger.exception("Square terminal pairing failed")
            error = str(e)
    return render(request, "pos_server/pair_pos.html", {
        "route": "terminal-setup",
        "terminals": SquareTerminal.objects.select_related("register")[:30],
        "registers": EligibleDevice.objects.order_by("name"),
        "error": error,
    })

@superuser_required
def create_menu(request):
    """
    Handle the creation of a menu via GET and POST requests.

    If the request method is GET, render the menu creation page.
    If the request method is POST, process the form data to create a new menu.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The HTTP response object with the rendered template.

    POST Parameters:
        title (str): The title of the menu.
        header (UploadedFile): The header image file.
        footer (UploadedFile): The footer image file.

    Template:
        pos_server/create-menu.html

    Context:
        done (bool): Indicates whether the menu creation was successful (only for POST requests).
    """
    if request.method == "GET":
        return render(request, "pos_server/create-menu.html")
    elif request.method == "POST":
        num_colors = 4
        header_image = request.FILES['header']
        footer_image = request.FILES['footer']
        color_dict = funcs.get_image_colors(header_image.read(), num_colors)
        header_image.seek(0)  # the model save below needs the file from the start
        title = request.POST["title"]
        Menu.objects.create(
            title=title,
            header_image=header_image,
            footer_image=footer_image,
            background_color=color_dict['Color 1'],
            accent_1=color_dict['Color 2'],
            accent_2=color_dict['Color 3'],
            accent_3=color_dict['Color 4'],
        )
        return render(request, "pos_server/create-menu.html", {"done":True})
    
@login_required
@user_passes_test(lambda u: u.is_superuser)
def register_staff(request):
    if request.method == "GET":
        form = UserCreationForm()
        return render(request, "pos_server/register-staff.html", {
            "route":"register_staff",
            "form":form
        })
    elif request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.email = request.POST["email"]
            user.first_name = request.POST["first_name"]
            user.last_name = request.POST["last_name"]
            user.is_staff = True
            user.save()
        return render(request, "pos_server/register-staff.html", {
            "route":"register_staff",
            "form":form
        })
    
def order_progress(request):
    """
    View function to display the progress of orders for the current day.

    This function retrieves orders that are currently in progress and those that are ready but not yet picked up.
    It also fetches the active menu and weather API key to be used in the template.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML page displaying the order status.

    Context:
        route (str): The route name for the template.
        menu (Menu): The active menu object.
        weather_API_key (str): The weather API key from the settings.
    """
    return render(request, "pos_server/orders-status.html", {
        "route":"order-status",
        "menu":Menu.active(),
        "weather_API_key":settings.WEATHER_API_KEY
    })
        
@require_POST
@csrf_exempt  # Square calls it; the signature proves it's Square
def square_webhook(request):
    """
    Square's webhooks, checked against SQUARE_WEBHOOK_SIGNATURE_KEY. terminal.checkout.updated updates that
    checkout's row and device.code.paired finishes a register's Terminal pairing (payments/square.py). Both are kept
    in the database and announced on "checkout", so every worker and register sees the same state.
    """
    body = request.body.decode("utf-8")
    signature = request.headers.get("x-square-hmacsha256-signature")
    key = settings.SQUARE_WEBHOOK_SIGNATURE_KEY
    if not (signature and key and is_valid_webhook_event_signature(body, signature, key, request.build_absolute_uri())):
        logger.warning("Square webhook with an invalid signature")
        return HttpResponseForbidden("Invalid signature")
    try:
        event = json.loads(body)
        kind, obj = event.get("type", ""), event.get("data", {}).get("object", {})
    except (ValueError, AttributeError):
        return HttpResponse("Not JSON", status=400)
    if kind.startswith("terminal.checkout") and "checkout" in obj:
        square.checkout_event(obj["checkout"])
    elif kind.startswith("device.code") and "device_code" in obj:
        square.device_code_event(obj["device_code"])
    else:
        logger.info("Ignoring Square webhook %s", kind)
    return HttpResponse("Webhook processed", status=200)


def active_orders(request):
    """
    Handle the request to retrieve active orders.
    This function attempts to retrieve the list of active order IDs from the cache.
    If the cache is empty or expired, it updates the cache with the latest active orders.
    It then fetches the active orders from the database using the IDs from the cache,
    serializes them, and returns them as a JSON response.
    Args:
        request (HttpRequest): The HTTP request object.
    Returns:
        JsonResponse: A JSON response containing the serialized active orders.
    """
    # Attempt to get cached active orders
    active_order_ids = cache.get('active_orders')

    if active_order_ids is None:
        # Cache miss; update the cache and get the latest list
        update_active_orders_cache()
        active_order_ids = cache.get('active_orders')

    # Fetch active orders from database using IDs from the cache
    active_orders = orders_for_screens(Order.objects.filter(id__in=active_order_ids))
    serialized_orders = [collect_order(order) for order in active_orders]  # Adjust serialization as needed
    if not (request.user.is_authenticated and request.user.is_staff and is_registered_device(request)):
        # The public status board polls this too: only expose what it displays
        public_keys = ("order_id", "name", "to_go_order", "channel", "timestamp", "timestamp_pretty",
                       "start_time", "stations", "done", "picked_up")
        serialized_orders = [
            {**{key: order[key] for key in public_keys},
             "dishes": [{"name": d["name"], "quantity": d["quantity"]} for d in order["dishes"]]}
            for order in serialized_orders
        ]
    return JsonResponse(serialized_orders, safe=False)


@api_app_required("app.marking")
def orders_attention(request):
    """
    For the rail's Orders dot (staff-page.js): the newest order on the kitchen screens for ?stations=<code>,<code>
    and how many of those wait for a station to accept them (online orders). The screen keeps which stations it
    watches and the newest order it has shown in the browser (order-marking.js), so the dot means "new since you
    last looked" or "waiting for approval". Cheap: two queries, no serializing.
    """
    codes = [c for c in request.GET.get("stations", "").split(",") if c][:50]
    rows = OrderStation.objects.filter(order__in=active_orders_queryset(), station__code__in=codes,
                                       status__in=[OrderStation.STATUS_PENDING, OrderStation.STATUS_APPROVED])
    newest = rows.aggregate(newest=Max("order_id"))["newest"] or 0
    waiting = rows.filter(status=OrderStation.STATUS_PENDING).values("order_id").distinct().count()
    return JsonResponse({"newest": newest, "waiting": waiting})
