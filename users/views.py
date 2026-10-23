from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.contrib.auth.models import User
from django.urls import reverse
from django.shortcuts import render
from django.utils.translation import gettext as _
from pos_server.devices import safe_next_url

# Create your views here.

def login_view(request):
    """
    Sign in with an email or username and a password. Unchecking "Stay signed in" ends the session when the
    browser closes. Afterwards: the safe `next` URL, else the register for staff and the store for everyone else.
    (On the register, staff unlock with a PIN instead; see pos_server.register.lock.)
    """
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next")) or ""
    context = {"route": "login", "next_url": next_url, "identifier": "", "remember": True}
    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        username = identifier
        if "@" in identifier:
            matches = list(User.objects.filter(email__iexact=identifier).values_list("username", flat=True)[:2])
            if len(matches) == 1:
                username = matches[0]
        user = authenticate(request, username=username, password=password) if identifier and password else None
        if user is not None:
            login(request, user)
            if not request.POST.get("remember"):
                request.session.set_expiry(0)
            return redirect(next_url or reverse("pos" if user.is_staff else "store_home"))
        context.update(identifier=identifier, remember=bool(request.POST.get("remember")), failed=True,
                       missing_identifier=not identifier, missing_password=not password)
    return render(request, "users/login.html", context, status=400 if request.method == "POST" else 200)

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

def welcome(request, uidb64, token):
    """The invite link from the admin's Staff page: a new staff member sets their password, then is signed in."""
    from django.contrib.auth.password_validation import validate_password
    from django.contrib.auth.tokens import default_token_generator
    from django.core.exceptions import ValidationError
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    from admin_portal.models import StoreSettings
    try:
        person = User.objects.get(pk=int(force_str(urlsafe_base64_decode(uidb64))), is_staff=True, is_active=True)
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        person = None
    valid = person is not None and default_token_generator.check_token(person, token)
    context = {"valid": valid, "person": person, "store": StoreSettings.load().name, "error": []}
    if valid and request.method == "POST":
        password = request.POST.get("password1", "")
        if password != request.POST.get("password2", ""):
            context["error"] = [_("The two passwords don’t match.")]
        else:
            try:
                validate_password(password, person)
            except ValidationError as error:
                context["error"] = list(error.messages)
        if not context["error"]:
            person.set_password(password)
            person.save(update_fields=["password"])  # the link stops working: the token depends on the password
            login(request, person, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("app_switcher_index")
    return render(request, "users/welcome.html", context, status=200 if valid else 400)
