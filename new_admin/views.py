from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from pos_server.models import Order, Menu, Dish
from online_store.models import PromoContent
from events.models import Event
from pos_server.views import collect_order
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse
import datetime
import calendar
import json

# Create your views here.

@login_required
@user_passes_test(lambda u: u.is_superuser)
def index(request):
    return redirect(reverse("admin-dashboard-home"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def dashboard(request):
    return redirect(reverse("admin-dashboard-home"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def store(request):
    return redirect(reverse("admin-store-branding"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def menus(request):
    return redirect(reverse("admin-menus-list"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def orders(request):
    return redirect(reverse("admin-orders-retail"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def dashboard_home(request):
    return render(request, "new_admin/dashboard_home.html")

@login_required
@user_passes_test(lambda u: u.is_superuser)
def retail_orders(request):
    order_list = Order.objects.all().order_by('-timestamp')
    paginator = Paginator(order_list, 20)  # Show 20 orders per page

    page_number = request.GET.get('page', 1)
    orders = paginator.get_page(page_number)
    return render(request, "new_admin/orders_retail.html", {
        "orders": orders,
        "page_count": paginator.num_pages
    })

@login_required
@user_passes_test(lambda u: u.is_superuser)
def retail_order(request, id):
    if request.method == "GET":
        order = Order.objects.get(id=id)
        
        return render(request, "new_admin/order.html", {
            "order": collect_order(order)
        })
    elif request.method == "DELETE":
        order = Order.objects.get(id=id)
        order.delete()
        return redirect(reverse("admin-orders-retail"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def receipt(request, id):
    order = Order.objects.get(id=id)
    return render(request, "new_admin/order-receipt.html", {
        "restaurant_name": "Restaurant Name",
        "restaurant_address": "Restaurant Address",
        "order": collect_order(order)
    })

@login_required
@user_passes_test(lambda u: u.is_superuser)
def store_branding(request):
    return render(request, "new_admin/store_branding.html")

@login_required
@user_passes_test(lambda u: u.is_superuser)
def promo_content(request):
    content = PromoContent.objects.all()
    return render(request, "new_admin/store_promo_content.html", {
        "content":content
    })

@login_required
@user_passes_test(lambda u: u.is_superuser)
def promo_content_instance(request, id):
    content = PromoContent.objects.get(pk=id)
    if request.method == "GET":
        return render(request, "new_admin/store_promo_content-instance.html", {
            "content":content
        })
    elif request.method == "POST":
        content.title = request.POST.get('title')
        new_image = request.FILES.get('new-image')
        print(new_image)
        if new_image:
            content.image = new_image
        content.tagline=request.POST.get('tagline', '')
        content.color=request.POST.get('main-color', '#000000')
        content.accent=request.POST.get('accent-color', '#ffffff')
        content.call_to_action=request.POST.get('new-button-text')
        content.link=request.POST.get('new-button-link')
        content.save()
        return redirect(reverse("admin-store-promo-content"))
    elif request.method == "DELETE":
        content.delete()
        return JsonResponse({"message": "Content deleted successfully"})

@login_required
@user_passes_test(lambda u: u.is_superuser)
def promo_content_new(request):
    if request.method == "GET":
        content = {
            "color":"#000000",
            "accent":"#ffffff",
            "call_to_action":_("Call to action")
        }
        return render(request, "new_admin/store_promo_content-instance.html", {
            "content":content,
            "new":True
        })
    elif request.method == "POST":
        content = PromoContent(title = request.POST.get('title'))
        print(request.FILES)
        new_image = request.FILES['new-image']
        if new_image:
            content.image = new_image
        content.tagline=request.POST.get('tagline', '')
        content.color=request.POST.get('main-color', '#000000')
        content.accent=request.POST.get('accent-color', '#ffffff')
        content.call_to_action=request.POST.get('new-button-text')
        content.link=request.POST.get('new-button-link')
        content.save()
        return redirect(reverse("admin-store-promo-content"))

@login_required
@user_passes_test(lambda u: u.is_superuser)
def events(request):
    now = timezone.localtime(timezone.now())
    upcoming = Event.objects.filter(end__gt=now).all()
    past = Event.objects.filter(end__lt=now).all()
    return render(request, "new_admin/store_events.html", {
            "upcoming":upcoming,
            "past":past
        })

@login_required
@user_passes_test(lambda u: u.is_superuser)
def event(request, id):
    event = Event.objects.get(pk=id)
    if request.method == "GET":
        return render(request, "new_admin/store_event.html", {
            "event":event
        })
    elif request.method == "POST":
        event.title = request.POST.get("title")
        event.description = request.POST.get("description")
        event.location_title = request.POST.get("location-title")
        event.location_address = request.POST.get("location_address")
        event.start = timezone.make_aware(datetime.datetime.strptime(request.POST.get("start"), "%Y-%m-%dT%H:%M"))
        event.end = timezone.make_aware(datetime.datetime.strptime(request.POST.get("end"), "%Y-%m-%dT%H:%M"))
        event.in_person_open = bool(request.POST.get("store-channel", False))
        event.online_open = bool(request.POST.get("online-channel", False))
        event.delivery_open = bool(request.POST.get("delivery-channel", False))
        event.save()
        return redirect(reverse("admin-store-events"))
    elif request.method == "DELETE":
        event.delete()
        return JsonResponse({"message": "Event deleted successfully"})

@login_required
@user_passes_test(lambda u: u.is_superuser)
def event_new(request):
    if request.method == "GET":
        return render(request, "new_admin/store_event.html")
    elif request.method == "POST":
        Event.objects.create(
            title = request.POST.get("title"),
            description = request.POST.get("description"),
            location_title = request.POST.get("location-title"),
            location_address = request.POST.get("location_address"),
            start = timezone.make_aware(datetime.datetime.strptime(request.POST.get("start"), "%Y-%m-%dT%H:%M")),
            end = timezone.make_aware(datetime.datetime.strptime(request.POST.get("end"), "%Y-%m-%dT%H:%M")),
            in_person_open = bool(request.POST.get("store-channel", False)),
            online_open = bool(request.POST.get("online-channel", False)),
            delivery_open = bool(request.POST.get("delivery-channel", False))
        )
        return redirect(reverse("admin-store-events"))
    
def dashboard_history(request):
    now = timezone.localtime(timezone.now())  # Get current local time
    year, month = now.year, now.month

    # Get first and last day of the month
    _, last_day = calendar.monthrange(year, month)
    start_date = timezone.make_aware(datetime.datetime(year, month, 1))  # Ensure timezone-aware
    end_date = timezone.make_aware(datetime.datetime(year, month, last_day))

    # Query unique order dates (returns `datetime.date` objects)
    business_days = Order.objects.filter(
        timestamp__gte=start_date, timestamp__lte=end_date
    ).dates("timestamp", "day")

    # Convert `datetime.date` to timezone-aware `datetime.datetime`
    business_dates = [
        {"title": "Business Day", "start": timezone.localtime(timezone.make_aware(datetime.datetime.combine(day, datetime.datetime.min.time()))).isoformat()}
        for day in business_days
    ]

    return render(request, "new_admin/dashboard-history.html", {
        "current_month_dates": json.dumps(business_dates)  # Pass initial month data
    })

def get_business_dates(request):
    """Fetch business days dynamically when user navigates months."""
    now = timezone.localtime(timezone.now())  # Get current local time
    year = int(request.GET.get("year", now.year))
    month = int(request.GET.get("month", now.month))

    _, last_day = calendar.monthrange(year, month)

    # Ensure start and end dates are timezone-aware
    start_date = timezone.make_aware(datetime.datetime(year, month, 1))
    end_date = timezone.make_aware(datetime.datetime(year, month, last_day, 23, 59, 59))

    # Query unique business days for the selected month
    business_days = Order.objects.filter(timestamp__gte=start_date, timestamp__lte=end_date).dates("timestamp", "day")

    # Convert `datetime.date` to timezone-aware `datetime.datetime`
    business_dates = [
        {"title": "Business Day", "start": timezone.localtime(timezone.make_aware(datetime.datetime.combine(day, datetime.datetime.min.time()))).isoformat()}
        for day in business_days
    ]
    
    return JsonResponse(business_dates, safe=False)

def menus_list(request):
    menus = Menu.objects.all()
    return render(request, "new_admin/menus-list.html", {
        "menus":menus
    })

def menu_instance(request, id):
    menu = Menu.objects.get(pk=id)
    if request.method == "GET":
        dishes = Dish.objects.filter(menu=menu).all()
        return render(request, "new_admin/menus-instance.html", {
            "menu":menu,
            "dishes":dishes
        })
    elif request.method == "POST":
        menu.background_color = request.POST.get("background-color", menu.background_color)
        menu.accent_1 = request.POST.get("accent-1", menu.accent_1)
        menu.accent_2 = request.POST.get("accent-2", menu.accent_2)
        menu.accent_3 = request.POST.get("accent-3", menu.accent_3)
        menu.title = request.POST.get("title", menu.title)
        menu.save()
        return redirect(reverse("admin-menu-instance", args=[id]))
    elif request.method == "PUT":
        menu.is_active = not menu.is_active
        menu.save()
        status = "activated" if menu.is_active else "deactivated"
        return JsonResponse({"message":_(f"Menu {menu.title} {status}!")})
    elif request.method == "DELETE":
        menu.delete()
        return JsonResponse({"message":_(f"Menu {menu.title} deleted!")})