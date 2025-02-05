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

def menu(request):
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
    try:
        item = Dish.objects.get(pk=id)
    except Dish.DoesNotExist:
        return HttpResponseNotFound("Dish not found")
    allergens = set()
    for dc in item.dishcomponent_set.all():
        for ci in dc.component.componentingredient_set.all():
            allergens.add(ci.ingredient.allergen)
    if len(allergens) > 1:
        allergens.remove(None)
    return render(request, "online_store/dish.html", {
        "route":"dish",
        "dish":item,
        "pretty_dish":prettify_dish(item),
        "menu":{"menu":item.menu.first()},
        "allergens":', '.join(allergens) if None not in allergens else None,
        "json":json.dumps([item.serialize_with_options()])
    })

def index(request):
    menu = Menu.objects.filter(is_active = True).first()
    offers = PromoContent.objects.all()
    return render(request, "online_store/home.html", {
        "route":"index",
        "menu":{"menu":menu},
        "offers":offers
    })

def order_status(request, id, from_placing = False):
    try:
        order = Order.objects.get(pk=id)
        rejected_order = None
    except Order.DoesNotExist:
        try:
            order = None
            rejected_order = RejectedOrder.objects.get(order_id=id)
        except RejectedOrder.DoesNotExist:
            return HttpResponseNotFound("Order not found")
    return render(request, "online_store/order.html", {
        "route":"order_status",
        "order":collect_order(order),
        "rejected_order":rejected_order,
        "from_placing":bool(from_placing),
        "status":order.progress_status() if order else None,
        "menu":{"menu":Menu.objects.filter(is_active = True).first()},
    })

def place_order(request):
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
            order.table = name.strip() if name != '' else None
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
                    delivery_instructions = "Leave the package at the door. "
                elif request.POST["delivery-dropoff-method"] == "meet":
                    delivery_instructions = "Meet the customer at the door. "
                elif request.POST["delivery-dropoff-method"] == "out":
                    delivery_instructions = "Meet the customer outside. "
                delivery_instructions += request.POST["delivery-instructions"]
                Delivery.objects.create(order=order, destination=address, address_2=unit, phone=int(phone), instructions=delivery_instructions)
            return redirect(reverse("order_status", kwargs={"id":order.id, "from_placing":True}))
        except Exception as e:
            print(e)
            return HttpResponse("Don't do this. I will get to implementing a better page soon")

def order_history(request):
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