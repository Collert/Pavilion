from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse


# Create your views here.

def index(request):
    return redirect(reverse("dashboard"))

def dashboard(request):
    return render(request, "new_admin/dashboard.html")