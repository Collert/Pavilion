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

from .devices import api_app_required, app_required, superuser_required, device_required, is_registered_device, set_device_cookie, token_is_registered, safe_next_url
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
from django.db.models import Q
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
        order.set_station_status(filters, OrderStation.STATUS_DONE, only_from=[OrderStation.STATUS_APPROVED])
        return JsonResponse({"all_ready": order.all_stations_done()})

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

@login_required
def device_elig(request):
    """
    Handle device eligibility requests.

    This view handles three types of HTTP requests: PUT, GET, and POST.

    - PUT: Checks if the device token provided in the request body is eligible.
      Returns a JSON response with status "device ok" if the device is recognized,
      otherwise returns a 403 Forbidden response.

    - GET: Renders the ineligible device page with a login route and unauthorized status.

    - POST: Authenticates the user with provided username and password. If the user is a superuser,
      creates a new eligible device and redirects to the next URL if provided, otherwise renders
      the ineligible device page with the new device's UUID and authorized status. If authentication
      fails, renders the ineligible device page with a failed login status.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The HTTP response object.
    """
    if request.method == "PUT":
        try:
            token = json.loads(request.body)["token"]
        except (ValueError, KeyError, TypeError):
            return HttpResponseForbidden("Forbidden: Device not recognized.")
        if token_is_registered(token):
            # Hand the device a signed cookie so the server can check it on later requests
            return set_device_cookie(JsonResponse({"status": "device ok"}, status=200), token)
        return HttpResponseForbidden("Forbidden: Device not recognized.")
    elif request.method == "GET":
        return render(request, "pos_server/ineligible-device.html", {
                "route":"login",
                "authorized":False,
                "next_url":safe_next_url(request, request.GET.get("next")) or "/",
            })
    elif request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        device_name = request.POST["device"]
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_superuser:
            new_device = EligibleDevice.objects.create(name=device_name)
            # The page stores the token in localStorage, then continues to "next"
            response = render(request, "pos_server/ineligible-device.html", {
                "route":"login",
                "uuid":new_device.token,
                "authorized":True,
                "next_url":safe_next_url(request, request.POST.get('next')) or "/",
            })
            return set_device_cookie(response, new_device.token)
        else:
            return render(request, "pos_server/ineligible-device.html", {
                "route":"login",
                "failed_login":True,
            })

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

# @local_network_only
@login_required
@user_passes_test(lambda u: u.is_superuser)
def pair_square_terminal(request):
    """
    Pair a Square Terminal. POST action=new creates a pairing code to enter on the Terminal; GET shows
    the codes and refreshes any that are still waiting, so reloading the page picks up a finished pairing.
    """
    error = None
    if request.method == "POST":
        try:
            if request.POST.get("action") == "new":
                square.create_device_code(request.POST.get("name") or "Square Terminal")
            elif request.POST.get("action") == "activate":
                terminal = SquareTerminal.objects.get(pk=request.POST.get("id"), status="PAIRED")
                terminal.is_active = True
                terminal.save()
                SquareTerminal.objects.exclude(pk=terminal.pk).update(is_active=False)
            return redirect(reverse("pair-terminal"))
        except Exception as e:
            logger.exception("Square terminal pairing failed")
            error = str(e)
    for terminal in SquareTerminal.objects.filter(status="UNPAIRED"):
        try:
            square.refresh_device_code(terminal)
        except Exception as e:
            logger.warning("Could not refresh device code %s: %s", terminal.code, e)
    return render(request, "pos_server/pair_pos.html", {
        "route":"terminal-setup",
        "terminals": SquareTerminal.objects.all()[:10],
        "fallback_device_id": settings.SQUARE_DEVICE_ID,
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
@csrf_exempt  # Disable CSRF for this view as it's an external API
def square_webhook(request):
    """
    Handle Square webhook events.
    This function processes incoming webhook events from Square, verifies the 
    event signature to ensure it is from Square, and updates the checkout card 
    status in the cache and global variables.
    Args:
        request (HttpRequest): The HTTP request object containing the webhook 
        event data.
    Returns:
        HttpResponse: A response indicating whether the webhook was processed 
        successfully or if the signature was invalid.
    Raises:
        json.JSONDecodeError: If the request body cannot be decoded as JSON.
    Notes:
        - The Square webhook signing secret is retrieved from the Django 
          settings.
        - The notification URL is built from the request object.
        - The signature is validated using the `is_valid_webhook_event_signature` 
          function.
        - If the signature is valid, the checkout card status is updated in the 
          cache and global variables.
        - If the signature is invalid, a forbidden response is returned.
    """
    # Your Square webhook signing secret
    signature_key = settings.SQUARE_WEBHOOK_SIGNATURE_KEY

    # The URL where event notifications are sent (your webhook endpoint)
    # notification_url = "https://be1e-2605-8d80-481-f962-cd85-83bc-b51-d999.ngrok-free.app/pos/webhook/square"
    notification_url = request.build_absolute_uri()

    # Read the raw request body and signature
    body = request.body.decode('utf-8')
    square_signature = request.headers.get('x-square-hmacsha256-signature')

    # Validate the webhook event signature
    is_from_square = is_valid_webhook_event_signature(body, square_signature, signature_key, notification_url)

    # Verify the signature
    if is_from_square:
        # Process the webhook data
        webhook_data = json.loads(request.body.decode('utf8'))
        checkout_id = webhook_data["data"]["object"]["checkout"]["id"]
        current_checkout_id = cache.get('current_checkout_id')
        
        # Only update status if this matches our current checkout
        if current_checkout_id and checkout_id == current_checkout_id:
            status = webhook_data["data"]["object"]["checkout"]["status"]
            cache.set('checkout_card_status', status, timeout=180)
            publish("checkout")
            logger.info("Square checkout %s is now %s", checkout_id, status)
        else:
            logger.info("Ignoring webhook for checkout %s (current: %s)", checkout_id, current_checkout_id)
        
        return HttpResponse('Webhook processed', status=200)
    else:
        logger.warning("Square webhook with an invalid signature")
        # Invalid signature, return forbidden
        return HttpResponseForbidden('Invalid signature')
    
@api_app_required("app.register")
@device_required
def check_card_status(request):
    """
    Handle GET and DELETE requests to check or delete the card status.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        JsonResponse: A JSON response containing the card status for GET requests,
                      or a confirmation of deletion for DELETE requests.
    """
    status = cache.get('checkout_card_status')
    if request.method == "GET":
        # Return explicit null status if not found in cache
        return JsonResponse({"status": status if status is not None else None})
    elif request.method == "DELETE":
        cache.delete('checkout_card_status')
        cache.delete('current_checkout_id')  # Clean up checkout ID as well
        return JsonResponse({"status": status if status is not None else None})

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
