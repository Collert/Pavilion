from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from pos_server.models import Order
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