from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse


# Create your views here.

def index(request):
    return redirect(reverse("admin-dashboard-home"))

def dashboard(request):
    return redirect(reverse("admin-dashboard-home"))

def dashboard_home(request):
    return render(request, "new_admin/dashboard_home.html")

def store(request):
    return redirect(reverse("admin-store-branding"))

def store_branding(request):
    return render(request, "new_admin/store_branding.html")