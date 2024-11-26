from django.utils import timezone
from django.shortcuts import render
from django.http import JsonResponse, HttpResponseNotFound, HttpResponseForbidden, HttpResponseBadRequest
from pos_server.models import *
from django.contrib.auth.decorators import login_required, user_passes_test
import requests
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from . import globals
from .models import *
import json
import datetime
from django.shortcuts import redirect
from .notifications import PushSubscription
from . import signals

RESTAURANT_ADDRESS = "501 4th Ave, New Westminster, BC V3L 1P3"

@login_required
@user_passes_test(lambda u: u.is_staff)
def ready_orders(request):
    courier = check_if_active_courier(request)
    today = timezone.localdate()
    orders = Delivery.objects.filter(
        order__kitchen_status__in = [2, 4],
        order__bar_status__in = [2, 4],
        order__gng_status__in = [2, 4],
        completed=False,
        order__picked_up=False,
        timestamp__date=today
    )
    if courier:
        for d in Delivery.objects.filter(completed = False).all():
            if d.courier == request.user:
                return redirect("delivery_order", id=d.id)
    return render(request, "deliveries/orders.html", {
        "route":"orders",
        "orders":orders,
        "courier":courier
    })

def get_eta(request):
    origin = RESTAURANT_ADDRESS
    destination = request.GET.get('destination')
    if not destination:
        return HttpResponseBadRequest("Destination address needed.")
    customer_requesting = request.GET.get('customer', 'false') == 'true'
    if customer_requesting:
        if len(globals.active_couriers) == 0:
            return HttpResponseNotFound("No couriers available")
        times = []
        modes = {dict(courier)["mode"] for courier in globals.active_couriers}
        for mode in modes:
            url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={origin}&destinations={destination}&mode={mode}&key={settings.GOOGLE_API_KEY}"
            response = requests.get(url)
            data = response.json()
            try:
                times.append(data["rows"][0]["elements"][0]["duration"])
            except KeyError:
                return JsonResponse({"text": "???", "value": 1})
        min_time = min(times, key=lambda x: x['value'])
        max_time = max(times, key=lambda x: x['value'])
        return JsonResponse({
            "min_time":min_time,
            "max_time":max_time,
            "one_time":min_time if min_time["value"] == max_time["value"] else None
        })
    else:
        courier = check_if_active_courier(request)
        if not courier:
            return HttpResponseForbidden("Start accepting orders to view this page.")
        mode = courier["mode"]
        url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={origin}&destinations={destination}&mode={mode}&key={settings.GOOGLE_API_KEY}"
        response = requests.get(url)
        data = response.json()
        try:
            return JsonResponse(data["rows"][0]["elements"][0]["duration"])
        except KeyError:
            return JsonResponse({"text": "???", "value": 1})

@login_required
@user_passes_test(lambda u: u.is_staff)
def profile(request):
    if request.method == "POST":
        if request.POST.get("act") == "start":
            method = request.POST["transport-mode"]
            globals.active_couriers.add(frozenset({"user":request.user, "mode":method}.items()))
        elif request.POST.get("act") == "end":
            for cour in globals.active_couriers:
                dict_cour = dict(cour)
                if dict_cour["user"] == request.user:
                    globals.active_couriers.remove(cour)
                    break
    working = check_if_active_courier(request) != None
    delivering = False
    if working:
        for d in Delivery.objects.filter(completed = False).all():
            if d.courier == request.user:
                delivering = True
                break
    return render(request, "deliveries/profile.html", {
        "working":working,
        "delivering":delivering,
        "route":"profile"
    })

@login_required
@user_passes_test(lambda u: u.is_staff)
def order(request, id):
    if request.method == "GET":
        delivery = Delivery.objects.get(pk=id)
        courier = check_if_active_courier(request)
        if delivery.eta:
            local_datetime = delivery.eta.astimezone(timezone.get_current_timezone())
            eta = local_datetime.strftime("%I:%M %p")
        else:
            eta = None
        return render(request, "deliveries/order.html", {
            "route":"order",
            "delivery":delivery,
            "courier":courier,
            "eta":eta,
            "restaurant_address":RESTAURANT_ADDRESS
        })
    elif request.method == "PUT":
        body = json.loads(request.body)
        delivery = Delivery.objects.get(pk=id)
        time_obj = datetime.datetime.strptime(body["eta"], "%I:%M %p")
        current_date = datetime.datetime.now().date()
        eta = datetime.datetime.combine(current_date, time_obj.time())
        order = delivery.order
        order.picked_up = True
        if not delivery.courier:
            delivery.courier = request.user
            delivery.eta = timezone.make_aware(eta)
            delivery.save()
            order.save()
            return JsonResponse({"status":"success"}, status=200)
        else:
            return JsonResponse({"status":"Order already taken"}, status=403)
    elif request.method == "POST":
        time = datetime.datetime.strptime(request.POST["new-eta"], "%H:%M").time()
        current_date = timezone.now().date()
        new_eta = timezone.make_aware(datetime.datetime.combine(current_date, time))
        delivery = Delivery.objects.get(pk=id)
        delivery.eta = new_eta
        delivery.save()
        return redirect("delivery_order", id=id)
    elif request.method == "DELETE":
        delivery = Delivery.objects.get(pk=id)
        delivery.completed = True
        delivery.save()
        return JsonResponse({"status":"success"}, status=200)

def check_if_active_courier(request):
    dict_cour = None
    for cour in globals.active_couriers:
        dict_cour = dict(cour)
        if dict_cour["user"] == request.user:
            return dict_cour
        
def vapid_public_key(request):
    return JsonResponse({'vapidPublicKey': settings.VAPID_PUBLIC_KEY})

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
