from django.shortcuts import render
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.urls import reverse
from django.core import serializers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from collections import Counter
from .models import *
import json
from django.utils import timezone
from . import globals
import datetime
from collections import defaultdict
import math
from payments import square
import configparser
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

# Create a configparser object
file_dir = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config.read(os.path.join(file_dir, 'config.cfg'))

def index(request):
    return HttpResponseRedirect(reverse("pos"))

def login_view(request):
    """
    Handle the login view for the POS server.

    This view supports both GET and POST requests:
    - GET: Renders the login page.
    - POST: Authenticates the user and logs them in if credentials are valid.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The HTTP response object. If the request method is GET, 
                      it returns the login page. If the request method is POST 
                      and authentication is successful, it redirects to the 
                      next URL or the main POS page. If authentication fails, 
                      it re-renders the login page with a failure message.
    """
    if request.method == "GET":
        return render(request, "pos_server/login.html", {
            "route":"login"
        })
    elif request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.POST.get('next')  # Get the next parameter
            if next_url:
                return redirect(next_url)
            return HttpResponseRedirect(reverse("pos"))
        else:
            return render(request, "pos_server/login.html", {
                "route":"login",
                "failed_login":True
            })

@login_required
def logout_view(request):
    """
    Logs out the user and redirects to the login view.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponseRedirect: A redirect to the login view.
    """
    logout(request)
    return redirect(reverse("login_view"))

# @local_network_only
@login_required
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
        today = timezone.localdate()
        stations = ["kitchen", "bar", "gng"]
        filters = request.GET.getlist('filter')
        print(filters)
        # Fetch all orders
        conditions = Q(picked_up=False) & Q(start_time__lte=now()) & Q(timestamp__date=today)
        status_conditions = Q()
        for station in stations:
            if station in filters:
                status_conditions |= ~Q(**{f"{station}_status__in": [3, 4]})
            else:
                status_conditions |= ~Q(**{f"{station}_status__in": [0, 1, 2, 3, 4]})
        conditions &= status_conditions
        orders = Order.objects.filter(conditions)
        print(orders)

        # Prepare data for each order
        orders_data = []
        for order in orders:
            orders_data.append(collect_order(order))
        return render(request, "pos_server/order-marking.html", {
            "route":"markings",
            'orders': orders_data,
            "filters":json.dumps(filters),
            "stations":json.dumps(stations)
        })
    elif request.method == "DELETE":
        # Mark order as picked up
        order_id = json.loads(request.body)["orderId"]
        order = Order.objects.get(id=order_id)
        station_mappings = {
            "kitchen": order.kitchen_status,
            "bar": order.bar_status,
            "gng": order.gng_status,
        }
        order.picked_up = True
        order.save()
        return JsonResponse({"status":"Order marked picked up"}, status=200)
    elif request.method == "PUT":
        # Mark order as completed
        order_id = json.loads(request.body)["orderId"]
        filters = json.loads(request.body)["filters"]
        order = Order.objects.get(id=order_id)

        station_mappings = {
            "kitchen": "kitchen_status",
            "bar": "bar_status",
            "gng": "gng_status",
        }

        for station in filters:
            if station in station_mappings:
                field_name = station_mappings[station]
                current_value = getattr(order, field_name)  # Get the current value of the attribute
                if current_value != 4:  # Only update if the current value is not 4
                    setattr(order, field_name, 2)
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    elif request.method == "POST":
        # Approve or reject the order
        body = json.loads(request.body)
        filters = body["filters"]
        order_id = body["orderId"]
        action = body["action"]
        order = Order.objects.get(id=order_id)
        station_mappings = {
            "kitchen": "kitchen_status",
            "bar": "bar_status",
            "gng": "gng_status",
        }
        payment_id = order.authorization.payment_id if order.authorization else None
        if action == "approve":
            for station in filters:
                if station in station_mappings:
                    field_name = station_mappings[station]
                    current_value = getattr(order, field_name)  # Get the current value of the attribute
                    if current_value != 4:  # Only update if the current value is not 4
                        setattr(order, field_name, 1)
            order.save()
            all_approved = order.kitchen_status in [1,4] and order.bar_status in [1,4] and order.gng_status in [1,4]
            return JsonResponse({
                "status":"Order marked approved", 
                "action":action, 
                "payment_id":payment_id,
                "all_approved":all_approved
            }, status=200)
        elif action == "delete":
            rejection_obj = body["rejection"]
            print(rejection_obj)
            rejection_reason = _("We are sorry but we could not complete your order for the following reasons: ")
            if 'out-of-stock' in rejection_obj["reasons"]:
                rejection_reason += _("Some of the products you requested are currently out of stock. We will update our menu in a few minutes to reflect the accurate stock levels. ")
            if 'no-containers' in rejection_obj["reasons"]:
                rejection_reason += _("We are currently out of containers to package your order. ")
            if rejection_obj["reasonExtra"]:
                rejection_reason += rejection_obj["reasonExtra"]
            rejection_reason += _("We apologize for the inconvenience. Please try placing your order again later or contact us for more information.")
            RejectedOrder.objects.create(order_id=order_id, reason=rejection_reason, timestamp=order.timestamp)
            for card_auth in order.gift_card_auth.all():
                print(card_auth.charged_balance)
                print(card_auth.card)
                print(card_auth.order)
                card_auth.card.load_card(card_auth.charged_balance)
            order.delete()
            return JsonResponse({"status":"Order deleted", "action":action, "payment_id":payment_id}, status=200)
    
@login_required
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
        cur_menu = Menu.objects.get(is_active = True)
        cur_menu.is_active = False
        cur_menu.save()
        menu = Menu.objects.get(title=request.POST["menu-title"])
        menu.is_active = True 
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
            EligibleDevice.objects.filter(token=json.loads(request.body)["token"])
        except:
            return HttpResponseForbidden("Forbidden: Device not recognized.")
        if EligibleDevice.objects.filter(token=json.loads(request.body)["token"]).exists():
            return JsonResponse({"status": "device ok"}, status=200)
    elif request.method == "GET":
        return render(request, "pos_server/ineligible-device.html", {
                "route":"login",
                "authorized":False
            })
    elif request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        device_name = request.POST["device"]
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_superuser:
            new_device = EligibleDevice.objects.create(name=device_name)
            next_url = request.POST.get('next')  # Get the next parameter
            if next_url:
                return redirect(next_url)
            return render(request, "pos_server/ineligible-device.html", {
                "route":"login",
                "uuid":new_device.token,
                "authorized":True
            })
        else:
            return render(request, "pos_server/ineligible-device.html", {
                "route":"login",
                "failed_login":True,
            })

# @local_network_only
@login_required
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
        menu = Menu.objects.filter(is_active=True).first()
        
        # Sort dishes by ID before grouping to ensure consistent ordering
        dishes = Dish.objects.filter(menu=menu).order_by('id')
        
        # Group dishes by station
        grouped_dishes = defaultdict(list)
        for dish in dishes:
            grouped_dishes[dish.station].append(dish)
        
        # Sort each group of dishes by ID to ensure stable ordering within each station
        sorted_grouped_dishes = {station: sorted(items, key=lambda x: x.id) for station, items in grouped_dishes.items()}
        
        return render(request, "pos_server/order.html", {
            "route": "pos",
            "menu": sorted_grouped_dishes,
            "menu_title": menu.title,
            "json": json.dumps([dish.serialize_with_options() for dish in dishes]),
            "menus": Menu.objects.all()
        })
    elif request.method == "POST":
        body = json.loads(request.body)
        cart = body["cart"]
        for payment in cart["partialPayments"]:
            if payment["type"] == "gift":
                GiftCard.objects.get(number=payment["number"]).charge_card(payment["amount"])
        instructions = body["instructions"]
        is_to_go = body["toGo"]
        dish_counts = Counter(i["pk"] for i in cart["items"])
        new_order = Order(special_instructions=instructions, to_go_order=is_to_go, channel="store")
        new_order.name = body["name"] if body["name"].strip() != '' else None
        new_order.save()
        for dish_id, quantity in dish_counts.items():
            dish = Dish.objects.get(id=dish_id)
            if check_if_only_choice_dish(dish):
                continue
            if dish.station == "bar":
                new_order.bar_status = 1
                new_order.picked_up = False
            elif dish.station == "kitchen":
                new_order.kitchen_status = 1
                new_order.picked_up = False
            elif dish.station == "gng":
                new_order.gng_status = 1
                new_order.picked_up = False
            for dc in dish.dishcomponent_set.all():
                if dc.component.crafting_option == "auto":
                    craft_component(dc.component.id, 1)
                dc.component.inventory -= dc.quantity
                dc.component.save()
            order_dish = OrderDish(order=new_order, dish=dish, quantity=quantity)
            order_dish.save()

        # All of this underneath is a start to customizing dishes. Won't be used right now.

        # dish_counts = defaultdict(lambda: {"customizations": None, "quantity": 0})

        # for item in cart["items"]:
        #     dish_id = item["id"]
        #     customizations = item.get("customizations", None)
        #     if customizations is None:
        #         customizations_key = "None"  # Use a string to represent no customizations
        #     else:
        #         customizations_key = json.dumps(customizations, sort_keys=True)
            
        #     # Use a composite key of dish_id and customizations to count occurrences
        #     key = (dish_id, customizations_key)
        #     dish_counts[key]["customizations"] = customizations
        #     dish_counts[key]["quantity"] += 1

        # # Transform the result into the desired format
        # result = {}
        # for (dish_id, customizations), details in dish_counts.items():
        #     # Ensure the dish_id key exists in the result dictionary
        #     if dish_id not in result:
        #         result[dish_id] = []
        #     result[dish_id].append({
        #         "customizations": details["customizations"],
        #         "quantity": details["quantity"]
        #     })

        # # Output the result
        # print(json.dumps(result, indent=2))
        # new_order = Order(special_instructions=instructions, to_go_order=is_to_go, channel="store")
        # new_order.name = body["name"] if body["name"].strip() != '' else None
        # new_order.save()
        # for dish_id, details in result.items():
        #     dish = Dish.objects.get(id=dish_id)
        #     for detail in details:
        #         if detail["customizations"]:
        #             for parent_str, customization in detail["customizations"].items():
        #                 parent_id = int(parent_str.split('-')[-1])
        #                 parent = Component.objects.get(pk=parent_id)
        #                 ModifiedComponent.objects.create(
        #                     order = new_order, 
        #                     dish = dish, 
        #                     parent = parent,
        #                     component = Component.objects.get(pk=int(customization["id"])),
        #                     quantity = 1,
        #                     operation = "pick"
        #                 )
        #         if dish.station == "bar":
        #             new_order.bar_status = 1
        #             new_order.picked_up = False
        #         elif dish.station == "kitchen":
        #             new_order.kitchen_status = 1
        #             new_order.picked_up = False
        #         for dc in dish.dishcomponent_set.all():
        #             if dc.component.crafting_option == "auto":
        #                 craft_component(dc.component.id, 1)
        #             dc.component.inventory -= dc.quantity
        #             dc.component.save()
        #         order_dish = OrderDish(order=new_order, dish=dish, quantity=detail["quantity"])
        #         order_dish.save()

        new_order.save()
        return JsonResponse({"message":"Sent to kitchen"}, status=200)
    elif request.method == "PUT":
        body = json.loads(request.body)
        print(body)
        cart = body["cart"]
        for payment in cart["partialPayments"]:
            print(payment)
            if payment["type"] == "gift":
                if GiftCard.objects.get(number=payment["number"]).available_balance < payment["amount"]:
                    return JsonResponse({
                            "message":_(f"Card x{payment['number'][-4:]} no longer has a sufficient balance to charge ${payment['amount']}"),
                            "status":402
                        }, status=402)
        return square.terminal_checkout(request)

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
        print(choice_components)
        return render(request, "pos_server/component-choice.html", {"choices":choice_components})
    else:
        return HttpResponseBadRequest("Please include a valid dish ID in the request")

@login_required
def dashboard(request):
    """
    Renders the dashboard view with a list of unique order dates.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered dashboard HTML page with the context containing:
            - route (str): The current route, set to "dashboard".
            - dates (QuerySet): A QuerySet of unique dates from the Order model's timestamp field.
    """
    unique_days = Order.objects.dates('timestamp', 'day')
    return render(request, "pos_server/dashboard.html", {
        "route":"dashboard",
        "dates":unique_days
    })

@login_required
def day_stats(request):
    """
    View function to generate and display statistics for a specific day based on orders.
    Args:
        request (HttpRequest): The HTTP request object containing GET parameters.
    Returns:
        HttpResponse: The rendered HTML response with the day's statistics or an error message.
    The function performs the following steps:
    1. Parses the 'date' parameter from the GET request.
    2. Attempts to parse the date string in two formats: 'Month Day, Year' and 'Mon. Day, Year'.
    3. If parsing fails, returns an error response indicating an invalid date format.
    4. Converts the parsed date into a timezone-aware datetime object.
    5. Fetches all available dishes from the database.
    6. Retrieves all orders for the specified date, ordered by timestamp.
    7. Initializes a stats dictionary to store various metrics:
        - item_stats: Count of each dish sold.
        - stations: Count of dishes prepared by each station.
        - order_occasions: Order counts and earnings by 15-minute time windows.
        - prep_times: Average preparation times.
        - components: Quantities of each dish component used.
        - ingredients: Quantities of each ingredient used.
    8. Defines a helper function to round down a datetime object to the nearest 15-minute window.
    9. Processes each order to populate the stats dictionary:
        - Groups order occasions into 15-minute time windows.
        - Calculates total price of each order.
        - Updates the count and total earnings for each time window.
        - Tracks preparation times to calculate averages.
        - Processes each dish in the order to update item_stats, stations, components, and ingredients.
    10. Converts defaultdicts to regular dicts for rendering.
    11. Sorts items by quantity sold in descending order.
    12. Renders the stats in the 'pos_server/day_stats.html' template.
    Note:
        - The function assumes the existence of the Dish, Order, and related models.
        - The function uses Django's timezone utilities for accurate time calculations.
    """
    if request.GET.get('date'):
        date_str = request.GET.get('date')  # Get the date string from the request
        try:
            # First, try to parse the date with the original format: Full month name
            day = datetime.datetime.strptime(date_str, '%B %d, %Y')
        except ValueError:
            try:
                # If parsing with the full month name fails, try parsing with abbreviated month names
                day = datetime.datetime.strptime(date_str, '%b. %d, %Y')
            except ValueError:
                # If both parsing attempts fail, return an error response
                return render(request, "pos_server/error.html", {
                    "error": "Invalid date format. Please use 'Month Day, Year' or 'Mon. Day, Year'."
                })

        # Convert the parsed date into a timezone-aware datetime object
        day = make_aware(day)

        # Fetch all available dishes
        menu = Dish.objects.all()

        # Fetch all orders for the specific date, ordered by timestamp
        orders = Order.objects.filter(timestamp__date=day).order_by('timestamp')

        # Initialize stats dictionary to store various metrics
        stats = {
            "item_stats": {},  # Stores the count of each dish sold
            "stations": {},  # Stores the count of dishes prepared by each station
            "order_occasions": {},  # Tracks order counts and earnings by time windows
            "prep_times": {},  # Stores average preparation times
            "components": {},  # Tracks quantities of each dish component used
            "ingredients": {},  # Tracks quantities of each ingredient used
        }

        # Function to round down a datetime object to the nearest 15-minute window
        def get_15_min_window(dt):
            # Convert datetime to local timezone for accurate window calculation
            dt = timezone.localtime(dt)
            # Round down to the nearest 15 minutes
            start_minute = 15 * math.floor(dt.minute / 15)
            start_time = dt.replace(minute=start_minute, second=0, microsecond=0)
            # Calculate the end of the 15-minute window
            end_time = start_time + datetime.timedelta(minutes=15)
            return f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"

        # Initialize defaultdicts for grouping data
        stats["order_occasions"] = defaultdict(lambda: {'count': 0, 'total_earnings': 0.0})
        prep_time_data = defaultdict(lambda: {'total_prep_time': datetime.timedelta(0), 'count': 0})

        # Process each order to populate stats
        for order in orders:
            # Group order occasions into 15-minute time windows
            time_window = get_15_min_window(order.timestamp)

            # Calculate total price of the order
            order_price = sum(od.quantity * od.dish.price for od in order.orderdish_set.all())

            # Update the count and total earnings for the time window
            stats["order_occasions"][time_window]['count'] += 1
            stats["order_occasions"][time_window]['total_earnings'] += order_price

            # Track preparation times to calculate averages later
            prep_time_data[time_window]['total_prep_time'] += order.prep_time
            prep_time_data[time_window]['count'] += 1

            # Populate average preparation times per time window
            for window, data in prep_time_data.items():
                count = data['count']
                if count > 0:  # Avoid division by zero
                    average_prep = data['total_prep_time'] / count
                    stats['prep_times'][window] = average_prep

            # Process each dish in the order
            for item in order.dishes.all():
                # Count the quantity of each dish sold
                if item.title not in stats["item_stats"]:
                    stats["item_stats"][item.title] = 1
                else:
                    stats["item_stats"][item.title] += 1

                # Count the distribution of dishes across stations
                if item.station not in stats["stations"]:
                    stats["stations"][item.station] = 1
                else:
                    stats["stations"][item.station] += 1

                # Track the quantity of each component used in the dish
                for dc in item.dishcomponent_set.all():
                    if dc.component.title not in stats["components"]:
                        stats["components"][dc.component.title] = [None] * 2
                        stats["components"][dc.component.title][1] = dc.component.unit_of_measurement
                        stats["components"][dc.component.title][0] = dc.quantity
                    else:
                        stats["components"][dc.component.title][0] += dc.quantity

                    # Track the quantity of each ingredient used in the components
                    for ci in dc.component.componentingredient_set.all():
                        if ci.ingredient.title not in stats["ingredients"]:
                            stats['ingredients'][ci.ingredient.title] = [None] * 2
                            stats['ingredients'][ci.ingredient.title][1] = ci.ingredient.unit_of_measurement
                            stats['ingredients'][ci.ingredient.title][0] = ci.quantity
                        else:
                            stats['ingredients'][ci.ingredient.title][0] += ci.quantity

        # Convert defaultdicts to regular dicts for rendering
        stats["order_occasions"] = dict(stats["order_occasions"])

        # Sort items by quantity sold in descending order
        stats["item_stats"] = dict(sorted(stats["item_stats"].items(), key=lambda item: item[1], reverse=True))

    # Render the stats in the HTML template
    return render(request, "pos_server/day_stats.html", {
        "menu": menu,
        "stats": stats,
    })

# @local_network_only
@login_required
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
        dishes (str): JSON serialized list of dishes associated with the active menu.
        menu (Menu): The active menu object.
        compiled_menu (dict): The compiled menu data.
        components_out (list): List of components for the output display.
    """
    menu = Menu.objects.get(is_active = True)
    dishes = Dish.objects.filter(menu=menu).order_by("id")
    comp_menu, components_out = compile_menu(menu)
    return render(request, "pos_server/pos-output-display.html", {
        "dishes": serializers.serialize('json', dishes),
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
            - categories (dict): A dictionary with keys 'kitchen', 'bar', and 'gng', each containing a list of prettified dishes.
            - components_out (bool): A boolean indicating if components are out (always False in this implementation).
    """
    components_out = False
    categories = {
        "kitchen":[],
        "bar":[],
        "gng":[],
    }
    for dish in menu.dishes.all().order_by("id"):
        categories[dish.station].append(prettify_dish(dish))
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
    if num.is_integer():
        return f"{int(num)}"
    # Otherwise, format the number to remove trailing zeros.
    else:
        return f"{num:.10f}".rstrip("0").rstrip(".")

# @local_network_only
@login_required
def pair_square_terminal(request):
    """
    Handles pairing of the Square terminal with the POS system.

    Depending on the HTTP request method, this function performs different actions:
    - GET: Retrieves the current POS device code from the configuration and renders the pairing page.
    - PUT: Generates a new POS device code, updates the configuration file, and renders the pairing page with the new code.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML response for the pairing page.
    """
    if request.method == "GET":
        code = config.get('POS_device_codes', 'POS_device_code')
        return render(request, "pos_server/pair_pos.html", {
            "code":code,
            "route":"terminal-setup",
        })
    elif request.method == "PUT":
        code = square.create_device_code()
        config.set('POS device codes', 'POS_device_code', code)
        with open('config.cfg', 'w') as configfile:
            config.write(configfile)
        return render(request, "pos_server/pair_pos.html", {
            "code":code,
            "route":"terminal-setup"
        })
    
@login_required
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
        image_path = default_storage.save('temp_image.jpg', ContentFile(header_image.read()))
        color_dict = funcs.get_image_colors(image_path, num_colors)
        default_storage.delete(image_path)
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
        in_progress (QuerySet): A queryset of orders that are currently in progress.
        ready (QuerySet): A queryset of orders that are ready but not yet picked up.
        menu (Menu): The active menu object.
        weather_API_key (str): The weather API key from the settings.
    """
    today = timezone.localdate()
    in_progress = Order.objects.filter(kitchen_status=1, timestamp__date=today)
    ready = Order.objects.filter(Q(kitchen_status=2) & Q(picked_up=False), timestamp__date=today)
    return render(request, "pos_server/orders-status.html", {
        "route":"order-status",
        "in_progress":in_progress,
        "ready":ready,
        "menu":Menu.objects.get(is_active=True),
        "weather_API_key":settings.WEATHER_API_KEY
    })
        
@csrf_exempt
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
        cache.set('checkout_card_status', webhook_data["data"]["object"]["checkout"]["status"], timeout=120)
        globals.checkout_card_status = webhook_data["data"]["object"]["checkout"]["status"]
        return HttpResponse('Webhook processed', status=200)
    else:
        print("invalid signature")
        # Invalid signature, return forbidden
        return HttpResponseForbidden('Invalid signature')
    
def check_card_status(request):
    """
    Handle GET and DELETE requests to check or delete the card status.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        JsonResponse: A JSON response containing the card status for GET requests,
                      or a confirmation of deletion for DELETE requests.
    """
    if request.method == "GET":
        return JsonResponse({"status":cache.get('checkout_card_status')})
    elif request.method == "DELETE":
        cache.delete('checkout_card_status')
        return JsonResponse({"status":globals.checkout_card_status})

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
    active_orders = Order.objects.filter(id__in=active_order_ids)
    serialized_orders = [collect_order(order) for order in active_orders]  # Adjust serialization as needed
    return JsonResponse(serialized_orders, safe=False)

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
    menu = Menu.objects.filter(is_active = True).first()
    dishes = Dish.objects.filter(menu=menu)
    return JsonResponse(serializers.serialize('json', dishes), safe=False)

def collect_order(order, done=False):
    """
    Collects and prepares order details along with related dishes.
    Args:
        order (Order): The order instance to collect details from.
        done (bool, optional): Indicates if the order is done. Defaults to False.
    Returns:
        dict: A dictionary containing the order details and related dishes, or None if the order is not provided.
            The dictionary includes the following keys:
            - order_id (int): The ID of the order.
            - dishes (list): A list of dictionaries, each containing 'name', 'quantity', and 'station' of a dish.
            - name (str): The name associated with the order.
            - to_go_order (bool): Indicates if the order is a to-go order.
            - channel (str): The channel through which the order was placed.
            - phone (str): The phone number associated with the order.
            - address (str): The delivery address if available, otherwise None.
            - special_instructions (str): Any special instructions for the order.
            - timestamp (str): The timestamp of the order in ISO format.
            - start_time (str): The start time of the order in ISO format.
            - kitchen_status (str): The kitchen status of the order.
            - done (bool): Indicates if the order is done.
            - bar_status (str): The bar status of the order.
            - gng_status (str): The grab-and-go status of the order.
            - picked_up (bool): Indicates if the order has been picked up.
            - payment_id (str): The payment ID associated with the order authorization, if available.
    """
    if not order:
        return None
    # Fetch related OrderDish instances for each order
    order_dishes = OrderDish.objects.filter(order=order)

    # Prepare dish details for this order
    dishes_data = []
    for od in order_dishes:
        dishes_data.append({
            'name': od.dish.title,
            'quantity': od.quantity,
            'price': od.dish.price,
            'station': od.dish.station
        })

    # Add the order and its dishes to the orders_data list
    return({
        'order_id': order.id,
        'dishes': dishes_data,
        'name':order.name,
        'to_go_order':order.to_go_order,
        'channel':order.channel,
        'phone':order.phone,
        'address':order.delivery.first().destination if order.delivery.first() else None,
        "special_instructions": order.special_instructions,
        "timestamp":order.timestamp.isoformat(),
        "timestamp_pretty":order.timestamp,
        "start_time":order.start_time.isoformat(),
        "kitchen_status":order.kitchen_status,
        "done":done,
        "bar_status":order.bar_status,
        "gng_status":order.gng_status,
        "picked_up":order.picked_up,
        "payment_id":order.authorization.payment_id if order.authorization else None,
    })
