from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.contrib.auth.models import User
from django.urls import reverse
from django.shortcuts import render

# Create your views here.

def login_view(request):
    if request.method == "GET":
        return render(request, "users/login.html", {
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
            return render(request, "users/login.html", {
                "route":"login",
                "failed_login":True
            })

@login_required
def logout_view(request):
    logout(request)
    return redirect(reverse("login_view"))

def register(request):
    if request.method == "GET":
        return render(request, "users/register.html", {
            "route":"register",
        })
    elif request.method == 'POST':
        username = request.POST["username"]
        email = request.POST["email"]
        password = request.POST["password1"]
        first_name = request.POST["first_name"]
        last_name = request.POST["last_name"]
        try:
            user = User.objects.create_user(email=email, username=username, password=password, first_name=first_name, last_name=last_name)
        except:
            return render(request, "users/register.html", {
                "route":"register",
                "error":True
            })
        login(request, user)
        return redirect(reverse("profile"))
    
@login_required
def profile(request):
    user = request.user
    return render(request, "users/profile.html", {
        "route":"profile",
        "user":user
    })