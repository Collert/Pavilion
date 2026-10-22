from django.shortcuts import render
from pos_server.models import *
from collections import defaultdict
from pos_server.views import prettify_dish, group_by_station
from pos_server.pricing import combo_queryset, price_cart, to_cents
from pos_server.dish_info import dish_allergens
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse, HttpResponse, FileResponse, HttpResponseNotAllowed, HttpResponseNotFound
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
from django.utils import timezone
from events.models import Event
from django.db.models import Q
from django.conf import settings
from gift_cards.models import GiftCard, GiftCardAuthorization
from django.utils.translation import gettext_lazy as _
from .context_processors import current_business_event
from django.utils.safestring import mark_safe
from . import storefront, themes


def store_links(request) -> dict:
    """Where the theme's header, footer and buttons go. Pages not built yet are "" and their links are left out."""
    signed_in = request.user.is_authenticated
    return {"home": storefront.link("store_home"), "menu": storefront.link("menu"), "catering": storefront.link("store_catering"),
            "services": storefront.link("store_services"), "cart": storefront.link("store_cart") or storefront.link("menu"),
            "account": storefront.link("order_history") if signed_in else "", "login": storefront.link("login_view")}


def theme_page(request, page, context=None, active=""):
    """Render one of the live theme's pages (themes/<name>/<page>) with what every theme page gets."""
    cur = themes.current(request)
    links = store_links(request)
    nav = [(key, label, links[key]) for key, label in (("home", _("Home")), ("menu", _("Menu")), ("catering", _("Catering")),
                                                        ("services", _("Events & services"))) if links[key]]
    user = request.user
    week = storefront.week_hours()
    return render(request, cur.theme.template(page), {
        "hours": week, "footer_hours": storefront.hours_summary(week),
        "theme": cur.theme, "t": cur.values, "store": cur.store, "logo_url": cur.logo_url, "preview": cur.preview,
        "css_vars": mark_safe(themes.css_vars(cur.theme, cur.values)), "status": storefront.open_now(),
        "cart_count": storefront.cart_count(request), "live_order": storefront.live_order(user), "links": links,
        "nav": nav, "active": active, "me": {"initials": storefront.initials(user), "first": user.first_name or user.username} if user.is_authenticated else None,
        "banner_url": links.get(cur.values.get("banner_link", ""), ""),
        "year": timezone.localdate().year,
        **(context or {}),
    })

def store_item(obj):
    """
    A dish or combo as the online store's JS needs it (money in cents). A combo asks for its first required
    one-pick choice that has more than one dish ("Choose a drink"); every other choice keeps its default picks
    (a sold-out default is swapped for the first dish that isn't).
    """
    if isinstance(obj, Combo):
        ask = None
        base = {}
        for slot in obj.slots.all():
            options = list(slot.options.all())
            if ask is None and slot.required and slot.picks == 1 and len(options) > 1:
                ask = {"slot": slot.id, "title": slot.title, "options": [
                    {"dish": o.dish_id, "title": o.dish.title, "available": o.dish.available,
                     "price": to_cents(obj.unit_price([p for p in obj.default_picks() if p.slot_id != slot.id] + [o]) - obj.standard_price())}
                    for o in options]}
                continue
            picks = [o if o.dish.available else next((x for x in options if x.dish.available), o) for o in slot.default_picks()]
            if picks:
                base[str(slot.id)] = [{"dish": o.dish_id} for o in picks]
        return {"key": f"c{obj.id}", "type": "combo", "id": obj.id, "title": obj.title, "price": to_cents(obj.standard_price()),
                "image": obj.image.url if obj.image else None, "available": obj.available, "ask": ask, "slots": base}
    return {"key": f"d{obj.id}", "type": "dish", "id": obj.id, "title": obj.title, "price": to_cents(obj.price),
            "image": obj.image.url if obj.image else None, "available": obj.available}


def menu_sections(menu):
    """[(Station, [Dish or Combo])] for a menu: dishes by station, each combo with its first dish's station."""
    if menu is None:
        return []
    dishes = Dish.objects.filter(menu=menu).select_related("station").order_by("station__position", "id")
    sections = dict(group_by_station(dishes))
    for combo in combo_queryset().filter(menu=menu, active=True, archived=False):
        station = combo.section_station()
        if station is not None:
            sections.setdefault(station, []).append(combo)
    return sorted(sections.items(), key=lambda pair: (pair[0].position, pair[0].id))


def cart_quote(request):
    """
    Price the online cart for display: {"lines": [...], "coupon": "CODE", "method": "pickup"|"delivery", "toGo": bool}
    (see pos_server.pricing; the method and to-go decide the disposables' fees). A coupon that can't be used is
    reported in `orderDiscountError` and left out of the totals.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        body = json.loads(request.body)
        lines = body.get("lines") or []
        coupon = str(body.get("coupon") or "").strip()
        delivery = body.get("method") == "delivery"
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"message": _("The cart is malformed.")}, status=400)
    try:
        cart = price_cart(lines, order_discount={"code": coupon} if coupon else None, channel="delivery" if delivery else "web",
                          to_go=delivery or bool(body.get("toGo")), strict=False)
    except OrderError as e:
        return JsonResponse({"message": str(e)}, status=400)
    return JsonResponse(cart.as_json())

def index(request):
    """The store's home page, as the live theme draws it (online_store/storefront.py works out what's on it)."""
    t = themes.current(request).values
    headline = t.get("headline", "")
    first, amp, rest = headline.partition(" & ")
    return theme_page(request, "home.html", {
        "headline": {"first": first, "amp": bool(amp), "rest": rest},
        "menus_now": storefront.menus_now() if t.get("show_menus") else [],
        "favourites": storefront.best_sellers(pictures=t.get("pictures", "photos")) if t.get("show_favourites") else [],
        "catering": storefront.catering_teaser() if t.get("show_catering") else None,
        "services": storefront.services_teaser() if t.get("show_services") else [],
        "pickup_minutes": storefront.pickup_minutes(),
        "hero_url": themes.media_url(t.get("hero")), "hero_drawing": _hero_drawing(),
    }, active="home")


def _hero_drawing():
    """The best seller's drawing, for a hero without a photo."""
    top = storefront.best_sellers(count=1)
    return storefront.drawing(top[0]["dish"].title) if top else "burger"


def place_order(request):
    """
    Create an online (pickup or delivery) order from the checkout form.

    The cart (lines and an optional coupon) is priced on the server (pos_server.pricing). Whatever gift cards don't cover must have been
    paid by card through the payment popup: the posted `transaction_uuid` has to be a paid, unused
    Transaction for at least that amount. Orders start pending and must be approved on the KDS.
    """
    if request.method != "POST":
        return redirect(reverse("menu"))
    post = request.POST
    try:
        cart = json.loads(post["cart-string"])
        lines = cart["lines"]
        coupon = str(cart.get("coupon") or "").strip()
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
                lines=lines,
                order_discount={"code": coupon} if coupon else None,
                channel="delivery" if method == "delivery" else "web",
                initial_status=OrderStation.STATUS_PENDING,
                gift_card_payments=gift_payments,
                verify_payment=verify_payment,
                special_instructions=post.get("order-instructions") or None,
                to_go_order=method == "delivery" or post.get("here-to-go-toggle") == "go",
                phone=phone,
                start_time=start_time,
                name=post.get("name", "").strip() or None,
                customer=request.user if request.user.is_authenticated else None,
                email=request.user.email if request.user.is_authenticated else "",
            )
            if payment.get("authorization"):
                authorization = payment["authorization"]
                order.authorization = authorization
                order.save(update_fields=["authorization"])
                order.payments.filter(method=OrderPayment.CARD).update(
                    square_payment_id=authorization.payment_id, card_brand=authorization.card_brand,
                    card_last4=authorization.card_last4)
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
    return redirect(reverse("order_status", kwargs={"id": order.id}))

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

    # Check if the file exists
    if not os.path.isfile(file_path):
        return HttpResponseNotFound(f"Service worker file not found at: {file_path}")

    # Serve the file if it exists
    with open(file_path, 'r') as f:
        response = HttpResponse(f.read(), content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/'  # Optional, explicitly set scope
        return response

