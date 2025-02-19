from django.shortcuts import render
from pos_server.models import *
from collections import defaultdict
from pos_server.views import prettify_dish, check_if_only_choice_dish
from .notifications import PushSubscription
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse, HttpResponse, FileResponse, HttpResponseNotFound
from django.core import serializers
import os
from inventory.views import craft_component
from collections import Counter
from .models import *
from django.shortcuts import redirect
from django.urls import reverse
from deliveries.models import Delivery
from pos_server.views import collect_order
from payments.models import Transaction
import datetime
from events.models import Event
from django.db.models import Q
from django.conf import settings
from gift_cards.models import GiftCard, GiftCardAuthorization
from django.utils.translation import gettext_lazy as _

def menu(request):
    """
    Handles the menu view for the online store.

    This view retrieves the active menu and its associated dishes, groups them by station,
    and also retrieves other inactive menus and their dishes, grouping them by station as well.
    The data is then rendered to the "online_store/menu.html" template.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML response for the menu view.

    Query Parameters:
        actual (str, optional): The primary key of the menu to be set as active. If not provided,
                                the first active menu will be used.

    Template Context:
        route (str): The route name, set to "menu".
        menu (dict): A dictionary containing the active menu and its grouped dishes.
            - menu (Menu): The active menu object.
            - dishes (dict): A dictionary where keys are station names and values are lists of dishes.
        others (dict): A dictionary where keys are titles of inactive menus and values are dictionaries
                       of grouped dishes by station.
        override_menu (str): The primary key of the menu to be set as active, if provided in the query parameters.
    """
    query = request.GET.get('actual', None)
    if query:
        try:
            active = Menu.objects.get(pk=query)
        except:
            active = Menu.objects.filter(is_active = True).first()
    else:
        active = Menu.objects.filter(is_active = True).first()
    active_dishes = Dish.objects.filter(menu=active)
    grouped_active = defaultdict(list)
    for dish in active_dishes:
        grouped_active[dish.station].append({"obj":dish,"json":json.dumps([dish.serialize_with_options()])})
    others = Menu.objects.filter(is_active = False).all()
    grouped_other_menus = defaultdict(dict)
    for menu_obj in others:
        this_grouped = defaultdict(list)
        this_dishes = Dish.objects.filter(menu=menu_obj)
        for dish in this_dishes:
            this_grouped[dish.station].append(dish)
        grouped_other_menus[menu_obj.title] = dict(this_grouped)
    return render(request, "online_store/menu.html", {
        "route":"menu",
        "menu":{"menu":active, "dishes":dict(grouped_active)},
        "others":dict(grouped_other_menus),
        "override_menu": query
    })

def dish(request, id):
    """
    View function to display details of a specific dish.

    Args:
        request (HttpRequest): The HTTP request object.
        id (int): The primary key of the dish to be retrieved.

    Returns:
        HttpResponse: Renders the dish details page if the dish exists.
        HttpResponseNotFound: Returns a 404 response if the dish does not exist.

    The function retrieves the dish by its primary key. If the dish is found,
    it collects all allergens associated with the dish's components and their
    ingredients. If there are multiple allergens, it removes 'None' from the set.
    Finally, it renders the dish details page with the dish information, menu,
    allergens, and serialized dish data in JSON format.
    """
    try:
        item = Dish.objects.get(pk=id)
    except Dish.DoesNotExist:
        return HttpResponseNotFound(_("Dish not found"))
    allergens = set()
    for dc in item.dishcomponent_set.all():
        for ci in dc.component.componentingredient_set.all():
            allergens.add(ci.ingredient.allergen)
    if len(allergens) > 1:
        allergens.discard(None)
    return render(request, "online_store/dish.html", {
        "route":"dish",
        "dish":item,
        "pretty_dish":prettify_dish(item),
        "menu":{"menu":item.menu.first()},
        "allergens":', '.join(a for a in allergens if a is not None) if None not in allergens else None,
        "json":json.dumps([item.serialize_with_options()])
    })

def index(request):
    """
    Renders the home page of the online store.

    This view function retrieves the active menu and all promotional content,
    then renders the "online_store/home.html" template with the retrieved data.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered home page with the context data.
    """
    menu = Menu.objects.filter(is_active = True).first()
    offers = PromoContent.objects.all()
    now = timezone.localtime(timezone.now())
    next_event = Event.objects.filter(start__gt=now).first()
    return render(request, "online_store/home.html", {
        "route":"index",
        "menu":{"menu":menu},
        "offers":offers,
        "next_event":next_event
    })

def order_status(request, id, from_placing = False):
    """
    View function to display the status of an order.

    Args:
        request (HttpRequest): The HTTP request object.
        id (int): The ID of the order.
        from_placing (bool, optional): Flag indicating if the request is from placing an order. Defaults to False.

    Returns:
        HttpResponse: The rendered order status page or a 404 response if the order is not found.
    """
    try:
        order = Order.objects.get(pk=id)
        rejected_order = None
    except Order.DoesNotExist:
        try:
            order = None
            rejected_order = RejectedOrder.objects.get(order_id=id)
        except RejectedOrder.DoesNotExist:
            return HttpResponseNotFound(_("Order not found"))
    return render(request, "online_store/order.html", {
        "route":"order_status",
        "order":collect_order(order),
        "rejected_order":rejected_order,
        "from_placing":bool(from_placing),
        "status":order.progress_status() if order else None,
        "menu":{"menu":Menu.objects.filter(is_active = True).first()},
    })

def place_order(request):
    """
    Handles the placement of an order based on the POST request data.

    Args:
        request (HttpRequest): The HTTP request object containing order details.

    Returns:
        HttpResponse: Redirects to the order status page if successful, otherwise returns an error message.

    The function performs the following steps:
    1. Extracts and processes the cart and payment information from the request.
    2. Calculates the remaining cart total after applying partial payments.
    3. Retrieves the transaction object if the cart total is greater than zero.
    4. Parses and combines the delivery/pickup time with the current date to create a timezone-aware datetime object.
    5. Creates an Order object with the provided details and saves it.
    6. Processes gift card payments and updates the gift card balance.
    7. Iterates through the dishes in the cart, updates inventory, and creates OrderDish objects.
    8. If the order method is delivery, creates a Delivery object with the provided address and instructions.
    9. Redirects to the order status page upon successful order placement.
    10. Returns an error message if any exception occurs during the process.
    """
    if request.method == "POST":
        uuid = request.POST["transaction_uuid"]
        try:
            cart = json.loads(request.POST["cart-string"])
            cart_total = float(cart["total"])
            for payment in cart["partialPayments"]:
                cart_total -= float(payment["amount"])
            if cart_total > 0:
                transaction = Transaction.objects.get(uuid=uuid)
            else:
                transaction = None
            method = request.POST["delivery-pickup-toggle"]
            name = request.POST["name"]
            phone = request.POST["phone"]
            time = request.POST["time"]
            parsed_time = datetime.datetime.strptime(time, "%H:%M").time()
            # Combine with current date to create a datetime object (naive)
            current_date = timezone.localtime(timezone.now()).date()  # Or use any specific date
            naive_datetime = datetime.datetime.combine(current_date, parsed_time)
            # Apply timezone to make it an aware datetime object
            aware_datetime = timezone.make_aware(naive_datetime, timezone.get_current_timezone())
            order_instructions = request.POST["order-instructions"]
            dishes = cart["items"]
            dish_ids = [dish["pk"] for dish in dishes]
            is_to_go = request.POST["here-to-go-toggle"] == "go"
            order = Order(
                special_instructions=order_instructions, 
                to_go_order=is_to_go, 
                phone=int(phone) if phone != '' else None, 
                authorization=transaction.authorization.first() if transaction else None,
                start_time=aware_datetime
            )
            order.name = name.strip() if name != '' else None
            # transaction.delete()
            if method == "pick-up":
                order.channel = "web"
            elif method == "delivery":
                order.to_go_order = True
                order.channel = "delivery"
            order.save()
            for payment in cart["partialPayments"]:
                if payment["type"] == "gift":
                    card = GiftCard.objects.get(number=payment["number"])
                    card.charge_card(payment["amount"])
                    GiftCardAuthorization.objects.create(card=card, order=order, charged_balance=payment["amount"])
            dish_counts = Counter(dish_ids)
            for dish_id, quantity in dish_counts.items():
                dish = Dish.objects.get(id=dish_id)
                if check_if_only_choice_dish(dish):
                    continue
                if dish.station == "bar":
                    order.bar_status = 0
                    order.picked_up = False
                elif dish.station == "kitchen":
                    order.kitchen_status = 0
                    order.picked_up = False
                elif dish.station == "gng":
                    order.gng_status = 0
                    order.picked_up = False
                for dc in dish.dishcomponent_set.all():
                    if dc.component.crafting_option == "auto":
                        craft_component(dc.component.id, 1)
                    dc.component.inventory -= dc.quantity
                    dc.component.save()
                order_dish = OrderDish(order=order, dish=dish, quantity=quantity)
                order_dish.save()
            order.save()
            if method == "delivery":
                address = f'{request.POST["delivery-address-1"]}, {request.POST["delivery-address-city"]}, BC {request.POST["delivery-address-postal"]}'
                unit = request.POST["delivery-address-2"]
                delivery_instructions = ""
                if request.POST["delivery-dropoff-method"] == "door":
                    delivery_instructions = _("Leave the package at the door. ")
                elif request.POST["delivery-dropoff-method"] == "meet":
                    delivery_instructions = _("Meet the customer at the door. ")
                elif request.POST["delivery-dropoff-method"] == "out":
                    delivery_instructions = _("Meet the customer outside. ")
                delivery_instructions += request.POST["delivery-instructions"]
                Delivery.objects.create(order=order, destination=address, address_2=unit, phone=int(phone), instructions=delivery_instructions)
            return redirect(reverse("order_status", kwargs={"id":order.id, "from_placing":True}))
        except Exception as e:
            print(e)
            return HttpResponse("Don't do this. I will get to implementing a better page soon")

def order_history(request):
    """
    Handles the order history view for the online store.

    This view retrieves the list of orders from the request, processes them, and renders the order history page.

    Args:
        request (HttpRequest): The HTTP request object containing the GET parameters.

    Returns:
        HttpResponse: The rendered order history page with the combined list of orders and rejected orders.

    GET Parameters:
        orders (str): A JSON-encoded list of order IDs. Defaults to an empty list if not provided or invalid.

    Template:
        online_store/history.html

    Context:
        route (str): The route name, set to "history".
        menu (dict): The active menu object.
        orders (list): The combined list of Order and RejectedOrder objects, sorted by ID in descending order.
    """
    orders = request.GET.get('orders', '[]')
    try:
        orders_list = json.loads(orders)
        if orders_list is None:
            orders_list = []
    except json.JSONDecodeError:
        orders_list = []
    orders_objs = list(Order.objects.filter(pk__in=orders_list).all().order_by("id").reverse())
    rejected_orders = list(RejectedOrder.objects.filter(order_id__in=orders_list).all().order_by("order_id").reverse())
    for ro in rejected_orders:
        ro.id = ro.order_id
        ro.progress_status = -1
        ro.channel = "web"
    combined_orders = sorted(orders_objs + rejected_orders, key=lambda x: x.id if isinstance(x, Order) else x.order_id, reverse=True)
    print(orders_list)
    return render(request, "online_store/history.html", {
        "route":"history",
        "menu":{"menu":Menu.objects.filter(is_active = True).first()},
        "orders":combined_orders
    })

def service_worker_view(request):
    """
    Handles the request to serve the service worker JavaScript file.
    This view function determines the absolute path to the service worker file,
    checks if the file exists, and serves it if it does. If the file does not
    exist, it returns a 404 Not Found response.
    Args:
        request (HttpRequest): The HTTP request object.
    Returns:
        HttpResponse: The HTTP response containing the service worker file content
                      with the appropriate content type and headers.
        HttpResponseNotFound: If the service worker file is not found at the specified path.
    """
    # Determine the absolute path to the service worker file
    file_path = os.path.join(settings.BASE_DIR, "online_store", 'service-worker.js')
    print(file_path)

    # Check if the file exists
    if not os.path.isfile(file_path):
        return HttpResponseNotFound(f"Service worker file not found at: {file_path}")

    # Serve the file if it exists
    with open(file_path, 'r') as f:
        response = HttpResponse(f.read(), content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/online-store/'  # Optional, explicitly set scope
        return response

@csrf_exempt
@login_required
def save_subscription(request):
    """
    Handle saving a push subscription for the current user.

    This view function processes a POST request containing subscription data
    in JSON format. It either creates a new PushSubscription object or updates
    an existing one for the current user.

    Args:
        request (HttpRequest): The HTTP request object containing the subscription data.

    Returns:
        JsonResponse: A JSON response indicating the subscription has been saved.
    """
    if request.method == 'POST':
        subscription_data = json.loads(request.body)
        user = request.user
        subscription, created = PushSubscription.objects.get_or_create(
            user=user,
            endpoint=subscription_data['endpoint'],
            defaults={
                'p256dh': subscription_data['keys']['p256dh'],
                'auth': subscription_data['keys']['auth']
            }
        )
        if not created:
            subscription.p256dh = subscription_data['keys']['p256dh']
            subscription.auth = subscription_data['keys']['auth']
            subscription.save()
        return JsonResponse({'message': 'Subscription saved.'})