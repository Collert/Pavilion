from django.shortcuts import render
from pos_server.models import *
from collections import defaultdict
from pos_server.views import prettify_dish, group_by_station
from .notifications import PushSubscription
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse, HttpResponse, FileResponse, HttpResponseNotFound
from django.core import serializers
import os
from .models import *
from django.shortcuts import redirect
from django.urls import reverse
from deliveries.models import Delivery
from pos_server.orders import OrderError, collect_order, create_order
from payments.models import PaymentError, Transaction
from django.db import transaction as db_transaction
import datetime
from events.models import Event
from django.db.models import Q
from django.conf import settings
from gift_cards.models import GiftCard, GiftCardAuthorization
from django.utils.translation import gettext_lazy as _
from .context_processors import current_business_event

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
            active = Menu.active()
    else:
        active = Menu.active()
    active_dishes = Dish.objects.filter(menu=active).select_related("station").prefetch_related("components__child_dishes")
    grouped_active = [
        (station, [{"obj":dish,"json":json.dumps([dish.serialize_with_options()])} for dish in dishes])
        for station, dishes in group_by_station(active_dishes)
    ]
    others = Menu.objects.filter(is_active = False).all()
    grouped_other_menus = {}
    for menu_obj in others:
        this_dishes = Dish.objects.filter(menu=menu_obj).select_related("station")
        grouped_other_menus[menu_obj.title] = group_by_station(this_dishes)
    return render(request, "online_store/menu.html", {
        "route":"menu",
        "menu":{"menu":active, "dishes":grouped_active},
        "others":grouped_other_menus,
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
    menu = Menu.active()
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
        "menu":{"menu":Menu.active()},
    })

def place_order(request):
    """
    Create an online (pickup or delivery) order from the checkout form.

    The cart is priced on the server (pos_server.orders). Whatever gift cards don't cover must have been
    paid by card through the payment popup: the posted `transaction_uuid` has to be a paid, unused
    Transaction for at least that amount. Orders start pending and must be approved on the KDS.
    """
    if request.method != "POST":
        return redirect(reverse("menu"))
    post = request.POST
    try:
        cart = json.loads(post["cart-string"])
        items = cart["items"]
        gift_payments = [(p["number"], p["amount"]) for p in cart.get("partialPayments", []) if p.get("type") == "gift"]
        method = post["delivery-pickup-toggle"]
        if method not in ("pick-up", "delivery"):
            raise ValueError(method)
        parsed_time = datetime.datetime.strptime(post["time"], "%H:%M").time()
        phone = post.get("phone", "").strip()
        phone = int("".join(ch for ch in phone if ch.isdigit())) if phone else None
    except (KeyError, ValueError, TypeError):
        return order_error(request, _("Some of the order details are missing or invalid. Please check the form and try again."))

    if method == "delivery" and not phone:
        return order_error(request, _("A phone number is required for delivery."))

    # Pickup/delivery time is today, in the restaurant's timezone
    today = timezone.localtime(timezone.now()).date()
    start_time = timezone.make_aware(datetime.datetime.combine(today, parsed_time), timezone.get_current_timezone())

    event = current_business_event()
    if event is None or not (event.delivery_open if method == "delivery" else event.online_open):
        return order_error(request, _("Sorry, we are not taking online orders right now."))
    if not (event.start <= start_time <= event.end):
        return order_error(request, _("Please pick a time between %(start)s and %(end)s.") % {
            "start": timezone.localtime(event.start).strftime("%H:%M"), "end": timezone.localtime(event.end).strftime("%H:%M")})
    payment = {}

    def verify_payment(remaining):
        if remaining > 0:
            try:
                transaction = Transaction.consume(post.get("transaction_uuid"), remaining)
            except PaymentError as e:
                raise OrderError(str(e))
            payment["authorization"] = transaction.authorization.first()

    try:
        with db_transaction.atomic():
            order = create_order(
                items=items,
                channel="delivery" if method == "delivery" else "web",
                initial_status=OrderStation.STATUS_PENDING,
                gift_card_payments=gift_payments,
                verify_payment=verify_payment,
                special_instructions=post.get("order-instructions") or None,
                to_go_order=method == "delivery" or post.get("here-to-go-toggle") == "go",
                phone=phone,
                start_time=start_time,
                name=post.get("name", "").strip() or None,
            )
            if payment.get("authorization"):
                order.authorization = payment["authorization"]
                order.save(update_fields=["authorization"])
            if method == "delivery":
                address = f'{post.get("delivery-address-1", "")}, {post.get("delivery-address-city", "")}, BC {post.get("delivery-address-postal", "")}'
                dropoff = {
                    "door": _("Leave the package at the door. "),
                    "meet": _("Meet the customer at the door. "),
                    "out": _("Meet the customer outside. "),
                }.get(post.get("delivery-dropoff-method"), "")
                Delivery.objects.create(order=order, destination=address, address_2=post.get("delivery-address-2", ""),
                                        phone=phone, instructions=f"{dropoff}{post.get('delivery-instructions', '')}")
    except OrderError as e:
        return order_error(request, str(e))
    return redirect(reverse("order_status", kwargs={"id":order.id, "from_placing":True}))

def order_error(request, message):
    return render(request, "online_store/order-error.html", {
        "route": "order_error",
        "message": message,
        "menu": {"menu": Menu.active()},
    }, status=400)

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
        "menu":{"menu":Menu.active()},
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
        response['Service-Worker-Allowed'] = '/'  # Optional, explicitly set scope
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