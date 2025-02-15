from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from pos_server.models import Order
from pos_server.views import collect_order
from django.core.paginator import Paginator


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