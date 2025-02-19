from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from pos_server.models import Order
from online_store.models import PromoContent
from pos_server.views import collect_order
from django.core.paginator import Paginator
from django.utils.translation import gettext_lazy as _

# Create your views here.

def index(request):
    return redirect(reverse("admin-dashboard-home"))

def dashboard(request):
    return redirect(reverse("admin-dashboard-home"))

def store(request):
    return redirect(reverse("admin-store-branding"))

def orders(request):
    return redirect(reverse("admin-orders-retail"))

def dashboard_home(request):
    return render(request, "new_admin/dashboard_home.html")

def retail_orders(request):
    order_list = Order.objects.all().order_by('-timestamp')
    paginator = Paginator(order_list, 20)  # Show 10 orders per page

    page_number = request.GET.get('page', 1)
    orders = paginator.get_page(page_number)
    return render(request, "new_admin/orders_retail.html", {
        "orders": orders,
        "page_count": paginator.num_pages
    })

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
    
def receipt(request, id):
    order = Order.objects.get(id=id)
    return render(request, "new_admin/order-receipt.html", {
        "restaurant_name": "Restaurant Name",
        "restaurant_address": "Restaurant Address",
        "order": collect_order(order)
    })

def store_branding(request):
    return render(request, "new_admin/store_branding.html")

def promo_content(request):
    content = PromoContent.objects.all()
    return render(request, "new_admin/store_promo_content.html", {
        "content":content
    })

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