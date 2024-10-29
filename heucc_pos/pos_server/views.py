from django.shortcuts import render
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse, HttpResponseForbidden
from django.urls import reverse
from django.core import serializers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from collections import Counter
from .models import *
import json
import time
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
from pos_server.decorators import local_network_only
from inventory.views import craft_component
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.utils.timezone import make_aware

# Create a configparser object
file_dir = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config.read(os.path.join(file_dir, 'config.cfg'))

# Create your views here.

def index(request):
    return HttpResponseRedirect(reverse("pos"))

def login_view(request):
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
    logout(request)
    return redirect(reverse("login_view"))

# @local_network_only
@login_required
def kitchen(request):
    if request.method == "GET":
        today = timezone.localdate()
        # Fetch all orders
        orders = Order.objects.filter(picked_up=False, timestamp__date=today)

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
        order.picked_up = True
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    elif request.method == "PUT":
        order_id = json.loads(request.body)["orderId"]
        order = Order.objects.get(id=order_id)
        order.kitchen_done = True
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    elif request.method == "POST":
        body = json.loads(request.body)
        order_id = body["orderId"]
        action = body["action"]
        order = Order.objects.get(id=order_id)
        payment_id = order.authorization.payment_id
        if action == "approve":
            order.approved = True
            order.save()
            return JsonResponse({"status":"Order marked approved", "action":action, "payment_id":payment_id}, status=200)
        elif action == "delete":
            order.delete()
            return JsonResponse({"status":"Order deleted", "action":action, "payment_id":payment_id}, status=200)

# @local_network_only
@login_required
def order_marking(request):
    if request.method == "GET":
        today = timezone.localdate()
        # Fetch all orders
        orders = Order.objects.filter(picked_up=False, timestamp__date=today)

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
        order.picked_up = True
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    elif request.method == "PUT":
        order_id = json.loads(request.body)["orderId"]
        order = Order.objects.get(id=order_id)
        order.kitchen_done = True
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    elif request.method == "POST":
        body = json.loads(request.body)
        order_id = body["orderId"]
        action = body["action"]
        order = Order.objects.get(id=order_id)
        payment_id = order.authorization.payment_id
        if action == "approve":
            order.approved = True
            order.save()
            return JsonResponse({"status":"Order marked approved", "action":action, "payment_id":payment_id}, status=200)
        elif action == "delete":
            order.delete()
            return JsonResponse({"status":"Order deleted", "action":action, "payment_id":payment_id}, status=200)

# @local_network_only
@login_required
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
        order.save()
        return JsonResponse({"status":"Order marked done"}, status=200)
    
@login_required
def menu_select(request):
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
            "json": serializers.serialize('json', dishes),
            "menus": Menu.objects.all()
        })
    elif request.method == "POST":
        body = json.loads(request.body)
        order = body["order"]
        instructions = body["instructions"]
        is_to_go = body["toGo"]
        dish_counts = Counter(order)
        new_order = Order(special_instructions=instructions, to_go_order=is_to_go, channel="store")
        new_order.table = body["table"] if body["table"].strip() != '' else None
        new_order.save()
        for dish_id, quantity in dish_counts.items():
            dish = Dish.objects.get(id=dish_id)
            if dish.station == "bar":
                new_order.bar_done = False
                new_order.picked_up = False
            elif dish.station == "kitchen":
                new_order.kitchen_done = False
                new_order.kitchen_needed = True
                new_order.picked_up = False
            for dc in dish.dishcomponent_set.all():
                if dc.component.crafting_option == "auto":
                    craft_component(dc.component.id, 1)
                dc.component.inventory -= dc.quantity
                dc.component.save()
            order_dish = OrderDish(order=new_order, dish=dish, quantity=quantity)
            order_dish.save()
        new_order.save()
        return JsonResponse({"message":"Sent to kitchen"}, status=200)
    elif request.method == "PUT":
        return square.terminal_checkout(request)

@login_required
def dashboard(request):
    unique_days = Order.objects.dates('timestamp', 'day')
    return render(request, "pos_server/dashboard.html", {
        "route":"dashboard",
        "dates":unique_days
    })

@login_required
def day_stats(request):
    if request.GET.get('date'):
        day = datetime.datetime.strptime(request.GET.get('date'), '%B %d, %Y')
        day = make_aware(day)
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
            dt = timezone.localtime(dt)
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
        stats["item_stats"] = dict(sorted(stats["item_stats"].items(), key=lambda item: item[1], reverse=True)) # https://stackoverflow.com/questions/613183/how-do-i-sort-a-dictionary-by-value
    return render(request, "pos_server/day_stats.html",{
        "menu":menu,
        "stats":stats
    })

# @local_network_only
@login_required
def pos_out_display(request):
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
    final_dish = {
        "title":dish.title,
        "components":"",
        "price":format_float(dish.price),
        "available":dish.in_stock or dish.force_in_stock,
    }
    dcs = dish.dishcomponent_set.all()
    for index, dc in enumerate(dcs):
        if dc.component.type == "food":
            if dc.component.unit_of_measurement == "l" or dc.component.unit_of_measurement == "ml":
                quantity_str = "a bowl of "
            elif dc.component.unit_of_measurement == "g" or dc.component.unit_of_measurement == "kg":
                    quantity_str = f"{int(dc.quantity)}{dc.component.unit_of_measurement} of "
            else:
                if dc.quantity == 1:
                    quantity_str = ""
                elif dc.quantity < 1:
                    quantity_str = "a piece of "
                else:
                    quantity_str = f"{int(dc.quantity)} "
        else:
            if dc.quantity == 1:
                quantity_str = "a cup of "
            else:
                quantity_str = f"{int(dc.quantity)} cups of "
        final_dish["components"] += f"{quantity_str}"
        final_dish["components"] += f"{dc.component.title.lower()}"
        if dc.quantity > 1 and not dc.component.type == 'beverage' and not (dc.component.unit_of_measurement == "g" or dc.component.unit_of_measurement == "kg"):
            final_dish["components"] += "s"
        if dc.component.inventory < dc.quantity:
            # final_dish["components"] += "*"
            components_out = True
        if index != len(dcs) - 1:
            final_dish["components"] += ", "
    return final_dish

def format_float(num:float) -> str:
    if num.is_integer():
        return f"{int(num)}"
    # Otherwise, format the number to remove trailing zeros.
    else:
        return f"{num:.10f}".rstrip("0").rstrip(".")

# @local_network_only
@login_required
def pair_square_terminal(request):
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
    today = timezone.localdate()
    in_progress = Order.objects.filter(kitchen_done=False, timestamp__date=today)
    ready = Order.objects.filter(Q(kitchen_done=True) & Q(picked_up=False), timestamp__date=today)
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
        globals.checkout_card_status = webhook_data["data"]["object"]["checkout"]["status"]
        return HttpResponse('Webhook processed', status=200)
    else:
        print("invalid signature")
        # Invalid signature, return forbidden
        return HttpResponseForbidden('Invalid signature')
    
def check_card_status(request):
    if request.method == "GET":
        return JsonResponse({"status":globals.checkout_card_status})
    elif request.method == "DELETE":
        globals.checkout_card_status = ''
        return JsonResponse({"status":globals.checkout_card_status})

def active_orders(request):
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
    menu = Menu.objects.filter(is_active = True).first()
    dishes = Dish.objects.filter(menu=menu)
    return JsonResponse(serializers.serialize('json', dishes), safe=False)

def collect_order(order, done=False):
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
        'to_go_order':order.to_go_order,
        'channel':order.channel,
        'phone':order.phone,
        'address':order.delivery.first().destination if order.delivery.first() else None,
        'approved':order.approved,
        "special_instructions": order.special_instructions,
        "timestamp":order.timestamp.isoformat(),
        "kitchen_done":order.kitchen_done,
        "kitchen_needed":order.kitchen_needed,
        "done":done,
        "bar_done":order.bar_done,
        "picked_up":order.picked_up,
        "payment_id":order.authorization.payment_id if order.authorization else None,
    })