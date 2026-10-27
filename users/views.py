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
    """Everyone's profile (name, contact, language, cards) is the store's account page now."""
    return redirect("account_profile")

def welcome(request, uidb64, token):
    """Set a password from a link: a new staff member's invite (the admin's Staff page) or a password reset (the
    admin's Customers page, users/accounts.py). Then they're signed in: staff to the apps, customers to their account."""
    from django.contrib.auth.password_validation import validate_password
    from django.contrib.auth.tokens import default_token_generator
    from django.core.exceptions import ValidationError
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    from admin_portal.models import StoreSettings
    try:
        person = User.objects.get(pk=int(force_str(urlsafe_base64_decode(uidb64))), is_active=True)
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        person = None
    valid = person is not None and default_token_generator.check_token(person, token)
    context = {"valid": valid, "person": person, "store": StoreSettings.load().name, "error": [],
               "reset": bool(person and person.has_usable_password())}
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
            return redirect("app_switcher_index" if person.is_staff else "account")
    return render(request, "users/welcome.html", context, status=200 if valid else 400)


def verify_email(request, uidb64, token):
    """The link from users/accounts.py: confirms their email, or switches them to the new one a staff member set."""
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    from admin_portal.models import StoreSettings
    from .accounts import confirm, email_tokens
    try:
        person = User.objects.get(pk=int(force_str(urlsafe_base64_decode(uidb64))), is_active=True)
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        person = None
    valid = person is not None and email_tokens.check_token(person, token)
    if valid:
        confirm(person)
    return render(request, "users/verified.html", {"valid": valid, "person": person, "store": StoreSettings.load().name},
                  status=200 if valid else 400)


# ---------- first-time setup ----------

SETUP_KEY = "first_setup"
PLACE_FIELDS = ("name", "phone", "address", "city", "time_zone", "tax_rate")


def setup(request):
    """
    The first-time setup screen (users/setup.py): while there's no account, in two steps. "place": the store's name,
    phone, address, time zone and tax (kept in the session). "you": the first owner's name, email, password and an
    optional register PIN, plus the setup code outside development; finishing makes the account and the store
    settings together, signs them in and opens the admin.
    """
    from django.contrib import messages
    from django.db import transaction

    from admin_portal import store_info
    from admin_portal.models import StoreSettings

    from . import setup as first_setup
    if not first_setup.needed():
        return redirect("login_view")
    if first_setup.code_required():
        first_setup.setup_code()  # made (and logged) now, so it's in the log before anyone is asked for it
    st = request.session.get(SETUP_KEY) or {"step": "place", "place": {
        "name": "", "phone": "", "address": "", "city": "", "time_zone": store_info.RUNNING["time_zone"], "tax_rate": "0"}}
    you = {"first": "", "last": "", "email": ""}
    errors = {}
    if request.method == "POST":
        op = request.POST.get("op", "")
        if st["step"] == "place" or op == "back":
            if op == "back":
                st["step"] = "place"
            else:
                place = {k: request.POST.get(k, "").strip() for k in PLACE_FIELDS}
                checked = store_info.problems({**place, "email": "", "directions": "", "music_storage_gb": "10",
                                               "routing_url": "", "geocoder_url": ""})
                errors = {k: v for k, v in checked.items() if k in PLACE_FIELDS}
                st["place"] = place
                if not errors:
                    st["step"] = "you"
            request.session[SETUP_KEY] = st
        else:
            you = {"first": request.POST.get("first", "").strip()[:150], "last": request.POST.get("last", "").strip()[:150],
                   "email": request.POST.get("email", "").strip().lower()[:254]}
            password, again, pin = request.POST.get("password1", ""), request.POST.get("password2", ""), request.POST.get("pin", "").strip()
            errors = _setup_problems(you, password, again, pin)
            if first_setup.code_required() and not first_setup.code_matches(request.POST.get("code")):
                errors["code"] = _("That’s not the setup code. It’s in the server’s log (or run manage.py setup_code).")
            if not errors:
                with transaction.atomic():
                    if User.objects.exists():  # someone finished first
                        return redirect("login_view")
                    user = _make_owner(you, password, pin)
                    store = _save_place(st["place"], you["email"])
                first_setup.finished()
                request.session.pop(SETUP_KEY, None)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                if store_info.restart_pending(store):
                    messages.success(request, _("Pavilion is set up. Restart it to use your time zone."))
                    return redirect("admin_store_info")
                messages.success(request, _("Pavilion is set up. Welcome!"))
                return redirect("admin_home")
    return render(request, "users/setup.html", {
        "st": st, "you": you, "errors": errors, "zones": store_info.zones(),
        "code_required": first_setup.code_required() and st["step"] == "you",
        "pin_note": first_setup.code_required(),
    }, status=400 if errors else 200)


def _setup_problems(you, password, again, pin) -> dict:
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    out = {}
    if not you["first"]:
        out["first"] = _("Your first name, please.")
    try:
        validate_email(you["email"])
    except ValidationError:
        out["email"] = _("That doesn’t look like an email address.")
    if not password:
        out["password1"] = _("Choose a password.")
    elif password != again:
        out["password2"] = _("The two passwords don’t match.")
    else:
        try:
            validate_password(password, User(username=you["email"], email=you["email"], first_name=you["first"], last_name=you["last"]))
        except ValidationError as e:
            out["password1"] = " ".join(e.messages)
    if pin and not (len(pin) == 4 and pin.isdigit()):
        out["pin"] = _("A PIN is 4 digits.")
    return out


def _make_owner(you, password, pin):
    """The first account: a superuser in the Owners group, with a staff profile (and their register PIN)."""
    from django.utils import timezone

    from .models import GroupInfo, Staff
    user = User(username=you["email"], email=you["email"], first_name=you["first"], last_name=you["last"],
                is_staff=True, is_superuser=True)
    user.set_password(password)
    user.save()
    owners = GroupInfo.objects.filter(owners=True).select_related("group").first()  # made by users 0004
    if owners:
        user.groups.add(owners.group)
    staff, _created = Staff.objects.get_or_create(user=user)
    staff.since = timezone.localdate()
    if pin:
        staff.set_pin(pin)
    staff.save()
    return user


def _save_place(place, email):
    from admin_portal import store_info
    from admin_portal.models import StoreSettings
    store, _created = StoreSettings.objects.get_or_create(pk=1)
    store.name, store.phone, store.address, store.city = place["name"], place["phone"], place["address"], place["city"]
    store.time_zone = place["time_zone"]
    store.tax_rate = store_info._number(place["tax_rate"], 0, 100, 3)
    store.email = store.email or email
    store.draft = {**(store.draft or {}), **{k: place[k] for k in ("name", "phone", "address", "city") if k in (store.draft or {})}}
    store.save()
    return store
