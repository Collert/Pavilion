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
from collections import Counter
from .models import *
import json
from functools import wraps
from .devices import api_staff_required, staff_required, superuser_required, device_required, is_registered_device, set_device_cookie, token_is_registered, safe_next_url
from .orders import OrderError, collect_order, create_order, orders_for_screens, refund_gift_cards, station_list
from django.utils import timezone

import datetime
from collections import defaultdict
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

@staff_required
@device_required
def order_marking(request):
    """
    Handles order marking operations based on the HTTP request method.
    GET:
        Fetches and filters orders based on the provided filters and current date.
        Renders the 'order-marking' template with the filtered orders and filters.
    DELETE:
        Marks an order as picked up based on the provided order ID.
    PUT:
        Marks an order as completed based on the provided order ID and filters.
    POST:
        Approves or rejects an order based on the provided order ID, action, and filters.
        If approved, updates the order status for the specified stations.
        If rejected, creates a RejectedOrder entry and deletes the order.
    Args:
        request (HttpRequest): The HTTP request object containing method, body, and GET parameters.
    Returns:
        HttpResponse: The response object with the appropriate status and data based on the request method.
    """
    if request.method == "GET":
        filters = request.GET.getlist('filter')
        orders = orders_for_screens(
            Order.objects.filter(picked_up=False, start_time__lte=now(), timestamp__date=timezone.localdate(),
                                 station_statuses__station__code__in=filters,
                                 station_statuses__status__lt=OrderStation.STATUS_REJECTED)
            .distinct().order_by("start_time")
        )
        return render(request, "pos_server/order-marking.html", {
            "route":"markings",
            "orders": [collect_order(order) for order in orders],
            "filters": filters,
            "stations": station_list(),
        })

    try:
        body = json.loads(request.body)
        order = Order.objects.get(id=body["orderId"])
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Expected a JSON body with orderId")
    except Order.DoesNotExist:
        return JsonResponse({"status": "Order not found"}, status=404)
    filters = body.get("filters", [])

    if request.method == "DELETE":
        # Mark order as picked up
        order.picked_up = True
        order.save()
        return JsonResponse({"status":"Order marked picked up"}, status=200)
    elif request.method == "PUT":
        # Mark this screen's stations as completed
        order.set_station_status(filters, OrderStation.STATUS_DONE, only_from=[OrderStation.STATUS_APPROVED])
        return JsonResponse({"status":"Order marked done"}, status=200)
    elif request.method == "POST":
        # Approve or reject the order
        action = body.get("action")
        if action == "approve":
            order.set_station_status(filters, OrderStation.STATUS_APPROVED, only_from=[OrderStation.STATUS_PENDING])
            all_approved = OrderStation.STATUS_PENDING not in order.statuses()
            payment_error = None
            if all_approved and order.authorization:
                try:
                    square.capture_authorization(order.authorization)
                except Exception as e:
                    logger.exception("Capturing payment for order %s failed", order.id)
                    payment_error = str(e)
            return JsonResponse({
                "status":"Order marked approved",
                "action":action,
                "all_approved":all_approved,
                "payment_error":payment_error,
            }, status=200)
        elif action == "delete":
            rejection_obj = body.get("rejection") or {}
            reasons = rejection_obj.get("reasons", [])
            rejection_reason = _("We are sorry but we could not complete your order for the following reasons: ")
            if 'out-of-stock' in reasons:
                rejection_reason += _("Some of the products you requested are currently out of stock. We will update our menu in a few minutes to reflect the accurate stock levels. ")
            if 'no-containers' in reasons:
                rejection_reason += _("We are currently out of containers to package your order. ")
            if rejection_obj.get("reasonExtra"):
                rejection_reason += rejection_obj["reasonExtra"] + " "
            rejection_reason += _("We apologize for the inconvenience. Please try placing your order again later or contact us for more information.")
            if order.authorization:
                try:
                    square.void_authorization(order.authorization)
                except Exception as e:
                    logger.exception("Voiding payment for order %s failed", order.id)
                    return JsonResponse({"status": "Could not refund the card payment, order kept", "error": str(e)}, status=502)
            with transaction.atomic():
                RejectedOrder.objects.create(order_id=order.id, reason=rejection_reason, timestamp=order.timestamp)
                refund_gift_cards(order)
                order.delete()
            return JsonResponse({"status":"Order deleted", "action":action}, status=200)
        return HttpResponseBadRequest("Unknown action")
    return HttpResponseNotAllowed(["GET", "POST", "PUT", "DELETE"])

@superuser_required
def menu_select(request):
    """
    Handles the selection of a menu via a POST request.

    This view function processes a POST request to change the active menu.
    It deactivates the currently active menu and activates the menu specified
    in the POST request.

    Args:
        request (HttpRequest): The HTTP request object containing metadata about the request.

    Returns:
        HttpResponseRedirect: A redirect response to the "pos" view.
    """
    if request.method == "POST":
        menu = Menu.objects.filter(title=request.POST.get("menu-title")).first()
        if menu:
            menu.is_active = True  # Menu.save() deactivates the others
            menu.save()
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

@staff_required
@device_required
def pos(request):
    """
    Handles POS (Point of Sale) operations for GET, POST, and PUT requests.
    GET:
    - Retrieves the active menu and groups dishes by station.
    - Renders the order page with the menu and other relevant data.
    POST:
    - Processes an order from the cart.
    - Handles partial payments, including gift card charges.
    - Creates a new order with special instructions and dish quantities.
    - Updates inventory and order statuses based on dish components.
    PUT:
    - Validates partial payments, including gift card balances.
    - Initiates a terminal checkout process.
    Args:
        request (HttpRequest): The HTTP request object.
    Returns:
        HttpResponse: The rendered order page for GET requests.
        JsonResponse: A JSON response indicating the result of POST and PUT requests.
    """
    if request.method == "GET":
        menu = Menu.active()
        if menu is None:
            return render(request, "pos_server/order.html", {"route": "pos", "sections": [], "json": [],
                                                             "menu_title": _("No active menu"), "menus": Menu.objects.all()})
        dishes = menu.dishes.select_related("station").prefetch_related("components__child_dishes").order_by('station__position', 'id')
        return render(request, "pos_server/order.html", {
            "route": "pos",
            "sections": group_by_station(dishes),
            "menu_title": menu.title,
            "json": [dish.serialize_with_options() for dish in dishes],
            "menus": Menu.objects.all()
        })
    try:
        body = json.loads(request.body)
        cart = body["cart"]
        gift_payments = [(p["number"], p["amount"]) for p in cart.get("partialPayments", []) if p.get("type") == "gift"]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Malformed cart")
    if request.method == "POST":
        try:
            create_order(
                items=cart["items"],
                channel="store",
                initial_status=OrderStation.STATUS_APPROVED,
                gift_card_payments=gift_payments,
                discount_amount=(cart.get("discounts") or {}).get("amount") or 0,
                discount_percent=(cart.get("discounts") or {}).get("percent") or 0,
                special_instructions=body.get("instructions") or None,
                to_go_order=bool(body.get("toGo")),
                name=(body.get("name") or "").strip() or None,
            )
        except OrderError as e:
            return JsonResponse({"message": str(e)}, status=400)
        return JsonResponse({"message":"Sent to kitchen"}, status=200)
    elif request.method == "PUT":
        # Before charging the card terminal, make sure the gift card part can still be charged
        for number, amount in gift_payments:
            card = GiftCard.objects.filter(number=number).first()
            if card is None or card.available_balance < Decimal(str(amount)):
                return JsonResponse({
                        "message":_("Card x%(last4)s no longer has a sufficient balance to charge $%(amount)s") % {"last4": str(number)[-4:], "amount": amount},
                        "status":402
                    }, status=402)
        return square.terminal_checkout(request)
    return HttpResponseNotAllowed(["GET", "POST", "PUT"])

def group_by_station(dishes):
    """[(Station, [dishes...]), ...] in station order, skipping empty stations."""
    sections = {}
    for dish in dishes:
        sections.setdefault(dish.station, []).append(dish)
    return sorted(sections.items(), key=lambda pair: (pair[0].position, pair[0].id))

def check_if_only_choice_dish(dish:Dish):
    """
    Checks whether the dish only consists of components that point to other dishes.

    Args:
        dish (Dish): The dish to check.

    Returns:
        bool: True if all components of the dish point to other dishes, False otherwise.
    """
    if not dish.components.all():
        return False
    for component in dish.components.all():
        if not component.child_dishes.all():
            return False
    return True

def component_choice(request):
    """
    Handles the selection of components for a given dish.

    This view function retrieves the dish ID from the request's GET parameters,
    fetches the corresponding dish from the database, and constructs a list of
    choice components. Each choice component includes the parent component and
    its child dishes, along with their titles, IDs, stock status, and forced stock status.

    Args:
        request (HttpRequest): The HTTP request object containing GET parameters.

    Returns:
        HttpResponse: Renders the 'component-choice.html' template with the choice components
                      if a valid dish ID is provided.
        HttpResponseBadRequest: Returns a bad request response if the dish ID is not provided
                                or invalid.
    """
    dish_id = request.GET.get('dish_id')
    if dish_id:
        dish = Dish.objects.filter(pk=dish_id).first()
        choice_components = []
        for component in dish.components.all():
            if component.child_dishes.all():
                choices = {
                    "parent":{
                        "title":component.title,
                        "id":component.id
                    },
                    "children":[]
                }
                for child in component.child_dishes.all():
                    choices["children"].append({
                        "title":child.title,
                        "id":child.id,
                        "in_stock":child.in_stock,
                        "force_in_stock":child.force_in_stock
                    })
                choice_components.append(choices)
        return render(request, "pos_server/component-choice.html", {"choices":choice_components})
    else:
        return HttpResponseBadRequest("Please include a valid dish ID in the request")

@login_required
@user_passes_test(lambda u: u.is_superuser)
def dashboard(request):
    """Old stats dashboard. Superseded by the new admin's history calendar."""
    return redirect(reverse("admin-dashboard-history"))

def parse_stats_date(date_str):
    """Accept ISO (2025-03-01) and the older 'March 1, 2025' / 'Mar. 1, 2025' formats."""
    for fmt in ('%Y-%m-%d', '%B %d, %Y', '%b. %d, %Y', '%b %d, %Y'):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

@login_required
@user_passes_test(lambda u: u.is_superuser)
def day_stats(request):
    """
    Sales statistics for one day (?date=, defaults to today): dishes sold, station load, component and
    ingredient usage, and order counts / revenue / average prep time per 15-minute window.
    Embedded in an iframe by new_admin's history calendar.
    """
    date_str = request.GET.get('date')
    day = parse_stats_date(date_str) if date_str else timezone.localdate()
    if day is None:
        return HttpResponseBadRequest("Invalid date. Use YYYY-MM-DD.")

    orders = (Order.objects.filter(timestamp__date=day).order_by('timestamp')
              .prefetch_related('orderdish_set__dish__station', 'orderdish_set__dish__dishcomponent_set__component__componentingredient_set__ingredient'))

    item_stats = Counter()
    stations = Counter()
    components = {}
    ingredients = {}
    occasions = defaultdict(lambda: {'count': 0, 'total_earnings': Decimal('0.00')})
    prep_totals = defaultdict(lambda: {'total': datetime.timedelta(0), 'count': 0})

    def get_15_min_window(dt):
        dt = timezone.localtime(dt)
        start_time = dt.replace(minute=15 * (dt.minute // 15), second=0, microsecond=0)
        end_time = start_time + datetime.timedelta(minutes=15)
        return f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"

    for order in orders:
        window = get_15_min_window(order.timestamp)
        order_dishes = order.orderdish_set.all()
        occasions[window]['count'] += 1
        occasions[window]['total_earnings'] += sum(od.quantity * od.dish.price for od in order_dishes)
        if order.prep_time:
            prep_totals[window]['total'] += order.prep_time
            prep_totals[window]['count'] += 1

        for od in order_dishes:
            dish = od.dish
            item_stats[dish.title] += od.quantity
            stations[dish.station.name] += od.quantity
            for dc in dish.dishcomponent_set.all():
                entry = components.setdefault(dc.component.title, [0, dc.component.unit_of_measurement])
                entry[0] += dc.quantity * od.quantity
                for ci in dc.component.componentingredient_set.all():
                    entry = ingredients.setdefault(ci.ingredient.title, [0, ci.ingredient.unit_of_measurement])
                    entry[0] += ci.quantity * dc.quantity * od.quantity

    stats = {
        "item_stats": dict(item_stats.most_common()),
        "stations": dict(stations),
        "order_occasions": dict(occasions),
        "prep_times": {w: d['total'] / d['count'] for w, d in prep_totals.items() if d['count']},
        "components": components,
        "ingredients": ingredients,
    }
    return render(request, "pos_server/day_stats.html", {
        "day": day,
        "stats": stats,
    })

@staff_required
def pos_out_display(request):
    """
    Handles the display of the POS output screen.

    This view retrieves the active menu and its associated dishes, compiles the menu,
    and renders the 'pos_server/pos-output-display.html' template with the necessary context.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML response for the POS output display.

    Context:
        dishes (list): Serialized dishes of the active menu (rendered with json_script).
        menu (Menu): The active menu object.
        compiled_menu (dict): The compiled menu data.
        components_out (list): List of components for the output display.
    """
    menu = Menu.active()
    dishes = Dish.objects.filter(menu=menu).order_by("id")
    comp_menu, components_out = compile_menu(menu)
    return render(request, "pos_server/pos-output-display.html", {
        "dishes": json.loads(serializers.serialize('json', dishes)),
        "menu":menu,
        "compiled_menu":comp_menu,
        "components_out":components_out
    })

def compile_menu(menu):
    """
    Compiles the menu into categorized dishes and determines if components are out.

    Args:
        menu (Menu): The menu object containing dishes to be compiled.

    Returns:
        tuple: A tuple containing:
            - categories (list): [(Station, [prettified dishes])] in station order.
            - components_out (bool): A boolean indicating if components are out (always False in this implementation).
    """
    components_out = False
    dishes = menu.dishes.select_related("station").order_by("station__position", "id")
    categories = [(station, [prettify_dish(dish) for dish in items]) for station, items in group_by_station(dishes)]
    return categories, components_out

def prettify_dish(dish):
    """
    Converts a dish object into a dictionary with a human-readable format.

    Args:
        dish (Dish): The dish object to be converted.

    Returns:
        dict: A dictionary containing the prettified dish information with the following keys:
            - title (str): The title of the dish.
            - components (str): A string describing the components of the dish.
            - price (str): The formatted price of the dish.
            - available (bool): Availability status of the dish.
    """
    final_dish = {
        "title":dish.title,
        "components":"",
        "price":format_float(dish.price),
        "available":(dish.in_stock or dish.force_in_stock) and dish.visible_in_menu,
    }
    dcs = dish.dishcomponent_set.all()
    for index, dc in enumerate(dcs):
        if dc.component.type == "food":
            if dc.component.unit_of_measurement == "l" or dc.component.unit_of_measurement == "ml":
                quantity_str = _("a bowl of ")
            elif dc.component.unit_of_measurement == "g" or dc.component.unit_of_measurement == "kg":
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
                quantity_str = _(f"{int(dc.quantity)} cups of ")
        final_dish["components"] += f"{quantity_str}"
        final_dish["components"] += f"{dc.component.title.lower()}"
        if dc.quantity > 1 and not dc.component.type == 'beverage' and not (dc.component.unit_of_measurement == "g" or dc.component.unit_of_measurement == "kg"):
            final_dish["components"] += "s"
        if dc.component.child_dishes.all():
            final_dish["components"] += _(" (choice of: ")
            for choice in dc.component.child_dishes.all():
                final_dish["components"] += f"{choice.title}/"
            final_dish["components"] = final_dish["components"][:-1]
            final_dish["components"] += ")"
        if dc.component.inventory < dc.quantity:
            # final_dish["components"] += "*"
            components_out = True
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
        
@api_staff_required
@require_POST
def check_superuser_status(request):
    """
    Check if the authenticated user is a superuser.
    Args:
        request (HttpRequest): The HTTP request object containing the user's credentials in the body.
    Returns:
        JsonResponse: A JSON response indicating whether the user is a superuser or not, or an error message if the request is invalid or authentication fails.
    Possible JSON responses:
        - {'superuser': True} if the user is authenticated and is a superuser.
        - {'superuser': False} if the user is authenticated but is not a superuser.
        - {'error': 'Invalid request'} if the request body is invalid.
        - {'error': 'Authentication failed'} if the authentication fails.
    """
    try:
        data = json.loads(request.body)
        username = data.get('username')
        password = data.get('password')
    except (ValueError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    user = authenticate(username=username, password=password)

    if user is not None and user.is_superuser:
        return JsonResponse({'superuser': True})
    elif user is not None:
        return JsonResponse({'superuser': False})
    else:
        return JsonResponse({'error': 'Authentication failed'}, status=401)

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
    
@api_staff_required
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

@api_staff_required
@device_required
def check_inventory(request):
    """
    Handles the request to check the inventory of active dishes.

    This view function retrieves the first active menu and then fetches all dishes
    associated with that menu. The list of dishes is then serialized to JSON format
    and returned as a JsonResponse.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        JsonResponse: A JSON response containing the serialized list of dishes.
    """
    menu = Menu.active()
    dishes = Dish.objects.filter(menu=menu)
    return JsonResponse(serializers.serialize('json', dishes), safe=False)
