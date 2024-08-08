from django.shortcuts import render
from pos_server.models import *
from collections import defaultdict
from pos_server.views import prettify_dish
from .notifications import PushSubscription
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse
from django.core import serializers
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

# Create your views here.

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
        grouped_active[dish.station].append({"obj":dish,"json":serializers.serialize("json", [dish])})
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
    item = Dish.objects.get(pk=id)
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
        "json":serializers.serialize("json", [item])
    })

def index(request):
    menu = Menu.objects.filter(is_active = True).first()
    offers = PromoContent.objects.all()
    return render(request, "online_store/home.html", {
        "route":"index",
        "menu":{"menu":menu},
        "offers":offers
    })

def order_status(request, id):
    order = Order.objects.get(pk=id)
    if order.channel == "delivery":
        if order.delivery.first().completed:
            status = 6
        elif order.picked_up:
            status = 5
        elif not order.picked_up and order.kitchen_done and order.bar_done and order.gng_done:
            status = 4
        elif (not order.kitchen_done or not order.bar_done or not order.gng_done) and order.approved:
            status = 3
        elif not order.approved and order.start_time < timezone.now:
            status = 2
        else:
            status = 1
    elif order.channel == "web":
        if order.picked_up:
            status = 5
        elif not order.picked_up and order.kitchen_done and order.bar_done and order.gng_done:
            status = 4
        elif (not order.kitchen_done or not order.bar_done or not order.gng_done) and order.approved:
            status = 3
        elif not order.approved and order.start_time < timezone.now:
            status = 2
        else:
            status = 1
    return render(request, "online_store/order.html", {
        "route":"order_status",
        "order":collect_order(order),
        "status":status,
        "menu":{"menu":Menu.objects.filter(is_active = True).first()},
    })

def place_order(request):
    if request.method == "POST":
        uuid = request.POST["transaction_uuid"]
        try:
            transaction = Transaction.objects.get(uuid=uuid)
            transaction.delete()
            method = request.POST["delivery-pickup-toggle"]
            name = request.POST["name"]
            phone = request.POST["phone"]
            order_instructions = request.POST["order-instructions"]
            dishes = json.loads(request.POST["dishes-string"])
            dish_ids = [dish["pk"] for dish in dishes]
            is_to_go = request.POST["here-to-go-toggle"] == "go"
            order = Order(special_instructions=order_instructions, to_go_order=is_to_go, phone=int(phone) if phone != '' else None)
            order.table = name.strip() if name != '' else None
            if method == "pick-up":
                order.channel = "web"
            elif method == "delivery":
                order.to_go_order = True
                order.channel = "delivery"
            order.save(temp=True)
            dish_counts = Counter(dish_ids)
            for dish_id, quantity in dish_counts.items():
                dish = Dish.objects.get(id=dish_id)
                if dish.station == "bar":
                    order.bar_done = False
                    order.picked_up = False
                elif dish.station == "kitchen":
                    order.kitchen_done = False
                    order.kitchen_needed = True
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
            return redirect(reverse("order_status", kwargs={"id":order.id}))
        except:
            return "Don't do this. I will get to implementing a better page soon"

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