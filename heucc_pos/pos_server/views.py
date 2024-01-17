from django.shortcuts import render
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse, HttpResponseForbidden
from django.http import StreamingHttpResponse
from django.urls import reverse
from django.core import serializers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import hmac
import hashlib
import base64
from collections import Counter
from .models import *
import json
import time
from django.utils import timezone
from .globals import new_data_queue
import datetime
from collections import defaultdict
import math
from . import square

# Create your views here.

def index(request):
    if request.user and request.user.is_authenticated:
        return HttpResponseRedirect(reverse("menu_select"))
    return render(request, "pos_server/index.html", {
        "route":"login"
    })

def login_view(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return HttpResponseRedirect(reverse("menu_select"))
        else:
            return render(request, "pos_server/index.html", {
                "route":"login",
                "failed_login":True
            })

def kitchen(request):
    if request.method == "GET":
        today = timezone.localdate()
        # Fetch all orders
        orders = Order.objects.filter(kitchen_done=False, timestamp__date=today)

        # Prepare data for each order
        orders_data = []
        for order in orders:
            orders_data.append(collect_order(order))
        # print(orders_data)
        return render(request, "pos_server/kitchen.html", {
            "route":"kitchen",
            'orders': orders_data,
            "portrait" : request.GET.get('portrait', 'false') == 'true'
        })
    elif request.method == "DELETE":
        order_id = json.loads(request.body)["orderId"]
        order = Order.objects.get(id=order_id)
        order.kitchen_done = True
        if order.bar_done and order.kitchen_done:
            order.prep_time = datetime.datetime.now() - order.timestamp
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)

def bar(request):
    if request.method == "GET":
        today = timezone.localdate()
        # Fetch all orders
        orders = Order.objects.filter(bar_done=False, timestamp__date=today)

        # Prepare data for each order
        orders_data = []
        for order in orders:
            orders_data.append(collect_order(order))
        # print(orders_data)
        return render(request, "pos_server/bar.html", {
            "route":"kitchen",
            'orders': orders_data,
            "portrait" : request.GET.get('portrait', 'false') == 'true'
        })
    elif request.method == "DELETE":
        order_id = json.loads(request.body)["orderId"]
        order = Order.objects.get(id=order_id)
        order.bar_done = True
        if order.bar_done and order.kitchen_done:
            order.prep_time = datetime.datetime.now() - order.timestamp
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    
def menu_select(request):
    menus = Menu.objects.all()
    return render(request, "pos_server/menu_select.html", {
            "route":"menu_select",
            "menus":menus
        })

def pos(request, menu):
    if request.method == "GET":
        dishes = Dish.objects.filter(menu=Menu.objects.filter(title = menu).first())
        return render(request, "pos_server/order.html", {
            "route":"pos",
            "menu": dishes,
            "menu_title": menu,
            "json": serializers.serialize('json', dishes)
        })
    elif request.method == "POST":
        body = json.loads(request.body)
        order = body["order"]
        instructions = body["instructions"]
        dish_counts = Counter(order)
        new_order = Order(special_instructions=instructions)
        new_order.table = body["table"] if body["table"].strip() != '' else None
        new_order.save()
        for dish_id, quantity in dish_counts.items():
            dish = Dish.objects.get(id=dish_id)
            order_dish = OrderDish(order=new_order, dish=dish, quantity=quantity)
            order_dish.save()
        return JsonResponse({"message":"Sent to kitchen"}, status=200)
    elif request.method == "PUT":
        return square.terminal_checkout(request, False)
    
def dashboard(request):
    unique_days = Order.objects.dates('timestamp', 'day')
    for day in unique_days:
        print(day.strftime("%Y-%m-%d"))
    return render(request, "pos_server/dashboard.html", {
        "route":"dashboard",
        "dates":unique_days
    })

def day_stats(request):
    if request.GET.get('date'):
        day = datetime.datetime.strptime(request.GET.get('date'), '%b. %d, %Y')
        menu = Dish.objects.all()
        orders = Order.objects.filter(timestamp__date = day).order_by('timestamp')
        stats = {
            "item_stats":{

            },
            "stations":{

            },
            "order_occasions":{

            },
            "prep_times":{
                
            },
            "components":{

            },
            "ingredients":{

            }
        }
        # Function to round down time to the nearest 15 minutes
        def get_15_min_window(dt):
            # Round down to the nearest 15 minutes for the start of the window
            start_minute = 15 * math.floor(dt.minute / 15)
            start_time = dt.replace(minute=start_minute, second=0, microsecond=0)

            # Calculate the end of the window
            end_time = start_time + datetime.timedelta(minutes=15)

            return f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"
        stats["order_occasions"] = defaultdict(lambda: {'count': 0, 'total_earnings': 0.0})
        prep_time_data = defaultdict(lambda: {'total_prep_time': datetime.timedelta(0), 'count': 0})
        for order in orders:
            # Group order occasions by 15 minute intervals
            time_window = get_15_min_window(order.timestamp)
            order_price = sum(od.quantity * od.dish.price for od in order.orderdish_set.all())
            # Update the count and total earnings in the dictionary
            stats["order_occasions"][time_window]['count'] += 1
            stats["order_occasions"][time_window]['total_earnings'] += order_price
            # Get averages of order prep times
            prep_time_data[time_window]['total_prep_time'] += order.prep_time
            prep_time_data[time_window]['count'] += 1
            for window, data in prep_time_data.items():
                count = data['count']
                if count > 0:
                    average_prep = data['total_prep_time'] / count
                    stats['prep_times'][window] = average_prep
            for item in order.dishes.all():
                # Getting quantity of each dish
                if item.title not in stats["item_stats"]:
                    stats["item_stats"][item.title] = 1
                else:
                    stats["item_stats"][item.title] += 1
                # Getting station distributions
                if item.station not in stats["stations"]:
                    stats["stations"][item.station] = 1
                else:
                    stats["stations"][item.station] += 1
                # Get quantity of each items component
                for dc in item.dishcomponent_set.all():
                    if dc.component.title not in stats["components"]:
                        stats["components"][dc.component.title] = [None] * 2
                        stats["components"][dc.component.title][1] = dc.component.unit_of_measurement
                        stats["components"][dc.component.title][0] = dc.quantity
                    else:
                        stats["components"][dc.component.title][0] += dc.quantity
                    # Get quantity of each components ingredient
                    for ci in dc.component.componentingredient_set.all():
                        if ci.ingredient.title not in stats["ingredients"]:
                            stats['ingredients'][ci.ingredient.title] = [None] * 2
                            stats['ingredients'][ci.ingredient.title][1] = ci.ingredient.unit_of_measurement
                            stats['ingredients'][ci.ingredient.title][0] = ci.quantity
                        else:
                            stats['ingredients'][ci.ingredient.title][0] += ci.quantity
        stats["order_occasions"] = dict(stats["order_occasions"])
    return render(request, "pos_server/day_stats.html",{
        "menu":menu,
        "stats":stats
    })

def pos_out_display(request):
    menu = request.GET.get('menu')
    dishes = Dish.objects.filter(menu=Menu.objects.filter(title = menu).first())
    return render(request, "pos_server/pos-output-display.html", {
        "dishes": serializers.serialize('json', dishes)
    })

def pair_square_terminal(request):
    code = square.create_device_code()
    return JsonResponse({"code":code})

@require_POST
@csrf_exempt  # Disable CSRF for this view as it's an external API
def square_webhook(request):
    # Your Square webhook signing secret
    webhook_secret = b'your_square_webhook_secret'

    # Get the signature from Square in the request headers
    square_signature = request.headers.get('X-Square-Signature')

    # Prepare the string to be hashed
    string_to_sign = request.build_absolute_uri() + request.body.decode('utf-8')

    # Hash the string
    hashed = hmac.new(webhook_secret, string_to_sign.encode(), hashlib.sha1)
    calculated_signature = base64.b64encode(hashed.digest()).decode()

    # Verify the signature
    if hmac.compare_digest(calculated_signature, square_signature):
        # Process the webhook data
        square.terminal_checkout(request, True)
        return HttpResponse('Webhook processed', status=200)
    else:
        # Invalid signature, return forbidden
        return HttpResponseForbidden('Invalid signature')

def event_stream():
    while True:
        while new_data_queue:
            data = new_data_queue[0]
            order_data_json = json.dumps(collect_order(data))
            print("sending dish")
            yield f"data: {order_data_json}\n\n"
            time.sleep(2)
            try:
                new_data_queue.remove(data)
            except:
                pass
        # Send a heartbeat every X seconds
        yield "\n"
        time.sleep(1)

def order_updates(request):
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    # response['Cache-Control'] = 'no-cache'
    return response

def collect_order(order):
    # Fetch related OrderDish instances for each order
    order_dishes = OrderDish.objects.filter(order=order)

    # Prepare dish details for this order
    dishes_data = [{
        'name': od.dish.title,
        'quantity': od.quantity,
        'station': od.dish.station
    } for od in order_dishes]

    # Add the order and its dishes to the orders_data list
    return({
        'order_id': order.id,
        'dishes': dishes_data,
        'table':order.table,
        "special_instructions": order.special_instructions,
        "timestamp":order.timestamp.replace(tzinfo=timezone.utc).astimezone(tz=None).isoformat()
    })

