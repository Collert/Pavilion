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
    """
    Handles the view for displaying ready orders for a courier.

    This view checks if there is an active courier associated with the request.
    It then filters the Delivery objects based on the kitchen, bar, and gng status,
    ensuring that the orders are not completed, not picked up, and are for the current date.
    If the courier is active and has an incomplete delivery, it redirects to the delivery order page.
    Otherwise, it renders the 'deliveries/orders.html' template with the filtered orders and courier information.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered 'deliveries/orders.html' template with context data or a redirect to the delivery order page.
    """
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
    """
    Calculate the estimated time of arrival (ETA) for a delivery.

    Args:
        request (HttpRequest): The HTTP request object containing the destination address and customer status.

    Returns:
        JsonResponse: A JSON response containing the ETA information.
        HttpResponseBadRequest: If the destination address is not provided.
        HttpResponseNotFound: If no couriers are available.
        HttpResponseForbidden: If the courier is not active.

    Notes:
        - If the request is from a customer, the function calculates the ETA using all available couriers' modes of transportation.
        - If the request is from a courier, the function calculates the ETA using the courier's mode of transportation.
        - The function uses the Google Distance Matrix API to calculate the ETA.
        - The function handles potential KeyErrors from the API response and returns a default response in such cases.
    """
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
    """
    Handle the profile view for couriers.

    This view handles POST requests to start or end a courier's active status and 
    checks if the courier is currently working or delivering.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered profile page with context variables:
            - working (bool): Indicates if the courier is currently working.
            - delivering (bool): Indicates if the courier is currently delivering.
            - route (str): The current route, set to "profile".
    """
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
    """
    Handle different HTTP methods for an order.

    Args:
        request (HttpRequest): The HTTP request object.
        id (int): The ID of the delivery.

    Returns:
        HttpResponse: The HTTP response object.

    HTTP Methods:
        GET:
            - Retrieve the delivery details and render the order page.
            - Returns a rendered HTML page with delivery details, courier status, ETA, and restaurant address.

        PUT:
            - Update the ETA and assign the delivery to the current user if not already taken.
            - Returns a JSON response indicating success or failure.

        POST:
            - Update the ETA of the delivery.
            - Redirects to the delivery order page.

        DELETE:
            - Mark the delivery as completed.
            - Returns a JSON response indicating success.
    """
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
    """
    Check if the user in the request is an active courier.

    Args:
        request (HttpRequest): The HTTP request object containing the user information.

    Returns:
        dict: A dictionary representing the active courier if the user is found, otherwise None.
    """
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
    """
    Save or update a push subscription for the current user.

    This view handles POST requests containing subscription data in JSON format.
    It either creates a new PushSubscription object or updates an existing one
    for the current user based on the provided endpoint.

    Args:
        request (HttpRequest): The HTTP request object containing the subscription data.

    Returns:
        JsonResponse: A JSON response indicating that the subscription has been saved.
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
