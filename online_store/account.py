"""
The store's customer account (design: StoreLogin, StoreAccount): sign in and join (drawn by the store's theme),
then /account/: orders (reorder, receipt), catering requests and the profile and wallet (saved cards, addresses,
gift cards, preferences). The account pages are Pavilion's own look, like the mockup; the store name and links
come from the store settings. Password sign-in only for now (email codes and Apple/Google come later).
"""
import datetime

import logging
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from admin_portal.models import StoreSettings
from catering.models import CateringOrder
from gift_cards.models import GiftCard
from payments import square
from pos_server.devices import safe_next_url
from pos_server.models import Order
from users.models import Customer, SavedAddress, SavedCard

from . import cart as store_cart, storefront
from .views import theme_page

logger = logging.getLogger(__name__)


def _next(request):
    return safe_next_url(request, request.POST.get("next") or request.GET.get("next")) or ""


def _customer(user) -> Customer:
    profile, _created = Customer.objects.get_or_create(user=user)
    return profile


# ---------- sign in and join ----------

def signin(request):
    nxt = _next(request)
    form = {"email": "", "failed": False}
    if request.method == "POST":
        identifier, password = request.POST.get("email", "").strip(), request.POST.get("password", "")
        username = identifier
        if "@" in identifier:
            matches = list(User.objects.filter(email__iexact=identifier).values_list("username", flat=True)[:2])
            if len(matches) == 1:
                username = matches[0]
        user = authenticate(request, username=username, password=password) if identifier and password else None
        if user is not None:
            login(request, user)
            return redirect(nxt or "account")
        form = {"email": identifier, "failed": True}
    return theme_page(request, "login.html", {"mode": "signin", "form": form, "next": nxt})


def join(request):
    nxt = _next(request)
    post = request.POST
    form = {"first": post.get("first", "").strip(), "last": post.get("last", "").strip(), "email": post.get("email", "").strip(),
            "phone": post.get("phone", "").strip(), "news": post.get("news") == "1" if request.method == "POST" else True}
    problem = ""
    if request.method == "POST":
        password = post.get("password", "")
        try:
            validate_email(form["email"])
        except ValidationError:
            problem = _("Check your email address.")
        if not problem and (not form["first"] or not form["last"]):
            problem = _("Add your first and last name.")
        elif not problem and len(password) < 8:
            problem = _("Use at least 8 characters for your password.")
        elif not problem and User.objects.filter(email__iexact=form["email"]).exists():
            problem = _("There’s already an account with that email. Sign in instead.")
        if not problem:
            username = form["email"].lower()[:150]
            if User.objects.filter(username=username).exists():
                problem = _("There’s already an account with that email. Sign in instead.")
            else:
                user = User.objects.create_user(username=username, email=form["email"], password=password,
                                                first_name=form["first"][:150], last_name=form["last"][:150])
                profile = _customer(user)
                digits = "".join(c for c in form["phone"] if c.isdigit())
                profile.phone = int(digits) if digits else None
                profile.newsletter = form["news"]
                profile.save()
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                from users.accounts import send_verification
                try:
                    send_verification(request, user)
                except Exception:  # the account works either way; the admin's Customers page can resend it
                    logger.warning("Couldn't send the verification email to %s", user.email, exc_info=True)
                messages.success(request, _("Welcome! Your account is ready."))
                return redirect(nxt or "account")
    return theme_page(request, "login.html", {"mode": "join", "form": form, "next": nxt, "problem": problem})


def signout(request):
    logout(request)
    return redirect("store_home")


# ---------- the account ----------

def _required(view):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(reverse("store_signin") + f"?next={request.path}")
        return view(request, *args, **kwargs)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _page(request, template, view, context):
    user = request.user
    store = StoreSettings.load()
    from . import work
    waiting = CateringOrder.objects.filter(_catering_filter(user), status=CateringOrder.WAITING).count()
    staff = work.side(user) if work.is_worker(user) else None
    return render(request, f"online_store/account/{template}", {
        "store": store, "view": view, "me": {"name": user.get_full_name() or user.username, "email": user.email,
                                            "initials": storefront.initials(user)}, "staff": staff,
        "catering_badge": waiting, "links": {"home": reverse("store_home"), "menu": reverse("menu"), "catering": reverse("store_catering")},
        **context})


def _catering_filter(user):
    from django.db.models import Q
    return Q(customer=user) | Q(email__iexact=user.email) if user.email else Q(customer=user)


def order_row(order):
    items = [i for i in order.items.all() if i.parent_id is None]
    names = [i.title for i in items]
    what = ", ".join(names[:2]) + (" " + _("and %(n)d more") % {"n": len(names) - 2} if len(names) > 2 else "")
    progress = order.progress_status() or 5
    refunded = sum((r.amount for r in order.refunds.all()), 0)
    if refunded:
        state, kind = _("Refunded %(a)s") % {"a": storefront.money(refunded)}, "refund"
    elif progress < 4:
        state, kind = _("Scheduled") if progress == 1 else _("Cooking"), "live"
    elif progress == 4:
        state, kind = _("Ready"), "live"
    else:
        state, kind = _("Delivered") if order.channel == "delivery" else _("Picked up"), "done"
    when = timezone.localtime(order.start_time)
    way = _("Delivery") if order.channel == "delivery" else _("Pickup")
    return {"o": order, "what": what or _("Order"), "state": state, "kind": kind, "mon": f"{when:%b}".upper(), "day": when.day,
            "meta": f"#{order.id} · {way} · {when:%a} {storefront.clock(when.time())}"}


@_required
def orders(request):
    user = request.user
    rows = [order_row(o) for o in Order.objects.filter(customer=user).prefetch_related("items").order_by("-timestamp")[:60]]
    chosen = next((r for r in rows if str(r["o"].id) == request.GET.get("o", "")), rows[0] if rows else None)
    detail = storefront.order_card(chosen["o"], full=True) if chosen else None
    if detail:
        payment = chosen["o"].payments.filter(method="card").first()
        detail["paid"] = (_("Paid with %(card)s") % {"card": f"{payment.card_brand.title()} •• {payment.card_last4}"}) if payment and payment.card_last4 else _("Paid online")
    for r in rows:
        r["on"] = chosen is r
    return _page(request, "orders.html", "orders", {"rows": rows, "chosen": chosen, "d": detail, "live": storefront.live_order(user)})


@_required
def reorder(request, order_id):
    order = get_object_or_404(Order, pk=order_id, customer=request.user)
    if request.method != "POST":
        return redirect("account")
    added = 0
    for item in order.items.filter(parent__isnull=True):
        selection = item.selection or {}
        if item.combo_id:
            slots = {k: v for k, v in selection.items() if k.isdigit()}
            line = {"type": "combo", "id": item.combo_id, "qty": item.quantity, "slots": slots, "note": item.note or ""}
        elif item.dish_id:
            cust = {k: v for k, v in selection.items() if k in ("parts", "swaps", "options", "condiments", "allergies")}
            line = {"type": "dish", "id": item.dish_id, "qty": item.quantity, "cust": cust, "note": item.note or ""}
        else:
            continue
        try:
            store_cart.price_cart([line], strict=False)
        except store_cart.OrderError:
            continue
        store_cart.add(request, line)
        added += 1
    if added:
        messages.success(request, _("%(n)d items added to your cart.") % {"n": added})
        return redirect("store_cart")
    messages.error(request, _("Those items aren’t on the menu right now."))
    return redirect(reverse("account") + f"?o={order.id}")


@_required
def receipt(request, order_id):
    from admin_portal.orders import order_page
    order = get_object_or_404(Order, pk=order_id, customer=request.user)
    return render(request, "admin_portal/receipt.html", {"d": order_page(order), "store": StoreSettings.load()})


@_required
def catering(request):
    from catering import pricing
    user = request.user
    rows = []
    for o in CateringOrder.objects.filter(_catering_filter(user)).order_by("-event_date"):
        last = o.messages.exclude(text="").exclude(who="system").order_by("-at").first()
        total = o.charged or pricing.order_totals(o)["total"]
        attention = o.status == CateringOrder.WAITING
        rows.append({"o": o, "attention": attention, "mon": f"{o.event_date:%b}".upper(), "day": o.event_date.day,
                     "meta": f"{o.number} · {o.event_date:%a} {storefront.clock(o.event_time)} · {o.get_fulfilment_display()}",
                     "msg": last.text if last else "", "from": (_("You") if last and last.who == "customer" else store_name()) + (f", {timezone.localtime(last.at):%b} {timezone.localtime(last.at).day}" if last else ""),
                     "total": pricing.money(total), "pay": _("Charged") if o.charged else _("Card on file") if o.status in CateringOrder.OPEN else _("Not charged"),
                     "btn": _("Review changes") if attention else _("Open"), "url": reverse("catering_order", args=[o.token])})
    return _page(request, "catering.html", "catering", {"rows": rows})


def store_name():
    return StoreSettings.load().name


@_required
def profile(request):
    user = request.user
    prof = _customer(user)
    if request.method == "POST":
        op = request.POST.get("op", "save")
        post = request.POST
        if op == "save":
            user.first_name, user.last_name = post.get("first", "").strip()[:150], post.get("last", "").strip()[:150]
            email = post.get("email", "").strip()
            try:
                validate_email(email)
                if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    raise ValidationError("taken")
                user.email = email
            except ValidationError:
                messages.error(request, _("That email can’t be used."))
            user.save()
            digits = "".join(c for c in post.get("phone", "") if c.isdigit())
            prof.phone = int(digits) if digits else None
            if not user.is_staff:
                prof.organization = post.get("org", "").strip()[:120]
            staff = getattr(user, "staff_profile", None) if user.is_staff else None
            if staff is not None:
                staff.emergency_name = post.get("ec_name", "").strip()[:60]
                staff.emergency_relation = post.get("ec_rel", "").strip()[:40]
                staff.emergency_phone = post.get("ec_phone", "").strip()[:20]
                staff.phone = post.get("phone", "").strip()[:20]  # staff keep their phone on the staff profile
                staff.save(update_fields=["emergency_name", "emergency_relation", "emergency_phone", "phone"])
            for key in ("order_texts", "catering_emails", "newsletter"):
                setattr(prof, key, post.get(key) == "1")
            prof.save()
            messages.success(request, _("Profile saved."))
        elif op.startswith("toggle:") and op[7:] in ("order_texts", "catering_emails", "newsletter"):
            setattr(prof, op[7:], not getattr(prof, op[7:]))
            prof.save()
        elif op.startswith("card_remove:"):
            card = SavedCard.objects.filter(user=user, pk=op[12:]).first()
            if card:
                if not card.square_card_id.startswith("demo:"):
                    try:
                        square.disable_card(card.square_card_id)
                    except square.SquareError:
                        pass
                card.delete()
                if not user.saved_cards.filter(is_default=True).exists():
                    user.saved_cards.filter(pk=(user.saved_cards.values_list("pk", flat=True).first())).update(is_default=True)
                messages.success(request, _("Card removed."))
        elif op.startswith("card_default:"):
            user.saved_cards.update(is_default=False)
            user.saved_cards.filter(pk=op[13:]).update(is_default=True)
        elif op == "address_save":
            pk = post.get("address_id", "")
            address = user.saved_addresses.filter(pk=pk).first() if pk else SavedAddress(user=user)
            if address is None or not post.get("line", "").strip():
                messages.error(request, _("Add the street address."))
            else:
                for key, size in (("label", 40), ("line", 160), ("unit", 60), ("city", 80), ("postal", 12), ("note", 160)):
                    setattr(address, key, post.get(key, "").strip()[:size])
                address.label = address.label or _("Home")
                address.save()
                messages.success(request, _("Address saved."))
                return redirect("account_profile")
        elif op.startswith("address_remove:"):
            user.saved_addresses.filter(pk=op[15:]).delete()
            return redirect("account_profile")
        return redirect("account_profile")
    edit = request.GET.get("address")
    editing = user.saved_addresses.filter(pk=edit).first() if edit and edit != "new" else (SavedAddress(user=user, label="") if edit == "new" else None)
    gift_cards = list(GiftCard.objects.filter(email__iexact=user.email, available_balance__gt=0)) if user.email else []
    work = _work_card(user)
    return _page(request, "profile.html", "profile", {
        "phone": work["profile"].phone if work and work["profile"] else prof.phone_text,
        "prof": prof, "cards": list(user.saved_cards.all()), "addresses": list(user.saved_addresses.all()), "editing": editing,
        "adding_card": request.GET.get("card") == "1", "gift_cards": gift_cards, "work": work,
        "toggles": [("order_texts", _("Order texts"), _("When your order is ready or on the way"), prof.order_texts),
                    ("catering_emails", _("Catering updates by email"), _("New messages and proposed changes"), prof.catering_emails),
                    ("newsletter", _("Monthly menu email"), _("New dishes and seasonal menus"), prof.newsletter)]})


def _work_card(user):
    """Staff: their emergency contact, roles and staff cards (StoreAccount, staff profile)."""
    if not user.is_staff:
        return None
    from gift_cards import staff_cards
    from gift_cards.models import StaffCard, StaffCardType
    profile = getattr(user, "staff_profile", None)
    cards = []
    for c in StaffCard.objects.filter(user=user, revoked_at__isnull=True, type__archived=False).select_related("type"):
        t = c.type
        if t.kind == StaffCardType.BALANCE:
            refill = staff_cards.refill_text(t)
            sub = (_("%(amount)s %(when)s") % {"amount": storefront.money(t.amount), "when": refill}) if refill else _("One-time balance")
            if t.refill == "shift":
                sub += " · " + _("resets when you clock in")
            value = storefront.money(c.balance)
        else:
            value = (f"{t.discount_value.normalize():f}% " + _("off")) if t.discount_kind == "pct" else storefront.money(t.discount_value) + " " + _("off")
            sub = _("Comes off automatically when you order")
        cards.append({"name": t.name, "sub": sub, "value": value, "color": t.color})
    return {"profile": profile, "roles": [{"name": r.name, "color": r.color} for r in profile.roles.filter(archived=False)] if profile else [],
            "cards": cards}


def save_customer_card(user, card) -> SavedCard:
    """Keep a card saved through payments/save-card on a signed-in customer (the first one is the default)."""
    prof = _customer(user)
    if card.get("customer") and not prof.square_customer_id:
        prof.square_customer_id = card["customer"]
        prof.save(update_fields=["square_customer_id"])
    first = not user.saved_cards.exists()
    return SavedCard.objects.create(user=user, square_customer_id=card["customer"], square_card_id=card["id"],
                                    brand=card.get("brand", ""), last4=card.get("last4", ""), is_default=first,
                                    exp_month=card.get("exp_month"), exp_year=card.get("exp_year"))
