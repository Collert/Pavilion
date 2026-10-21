"""
The admin portal (/admin/): the store's back office. Superuser only for now. Pages follow CLAUDE.md: Django
templates, forms that POST and redirect, static/js/live-page.js to keep them current.
"""
import datetime
import time
from decimal import Decimal, InvalidOperation

from urllib.parse import quote, urlencode

from django import forms
from django.contrib import messages
from django.db.models import Max
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from misc_tools.email import EmailError, send as send_email
from pos_server.devices import safe_next_url, superuser_required
from pavilion.realtime import publish
from pos_server.menu_schedule import Schedule
from pos_server.models import (CateringPrice, Combo, Component, Dish, DishCategory, DishComponent, Extra, Menu, MenuRule, Option,
                               OptionGroup, Order, ReceiptLog, Refund, Station)
from pos_server.refunds import RefundError, refund_order

from . import dashboard as dashboard_data
from . import dishes as dishes_data
from . import menus as menus_data
from . import orders as orders_data
from . import reports as reports_data
from .models import StoreSettings
from .nav import rail


def page(request, template, active, context=None):
    """Render an admin page with the rail and everything the layout needs."""
    user = request.user
    full_name = user.get_full_name() or user.username
    return render(request, template, {
        "rail": rail(active), "active": active, "store": StoreSettings.load(),
        "me": {"name": full_name, "initial": full_name[:1].upper(), "role": _("Owner") if user.is_superuser else _("Staff")},
        "now_ms": int(time.time() * 1000),
        **(context or {}),
    })


@superuser_required
def home(request):
    data = dashboard_data.dashboard(request.GET.get("range", "today"), request.GET.get("open") == "1")
    now = timezone.localtime()
    greeting = _("Good morning") if now.hour < 12 else _("Good afternoon") if now.hour < 17 else _("Good evening")
    return page(request, "admin_portal/home.html", "home", {
        "d": data, "greeting": greeting, "first_name": request.user.first_name or request.user.username})


@superuser_required
def reports(request):
    today = timezone.localdate()
    try:
        year, month = (int(part) for part in request.GET.get("month", "").split("-"))
        datetime.date(year, month, 1)
    except (ValueError, TypeError):
        year, month = today.year, today.month
    view = "calendar" if request.GET.get("view") == "calendar" else "table"
    return page(request, "admin_portal/reports.html", "reports", {
        "r": reports_data.month_report(year, month), "view": view})


@superuser_required
def report_day(request, day):
    day = _parse_day(day)
    if not day:
        last = reports_data.last_business_day()
        if not last:
            return redirect("admin_reports")
        return redirect("admin_report_day", day=last.isoformat())
    earlier, later = reports_data.neighbours(day)
    return page(request, "admin_portal/report_day.html", "reports", {
        "r": reports_data.day_report(day), "earlier": earlier, "later": later})


@superuser_required
def report_day_csv(request, day):
    day = _parse_day(day)
    if not day:
        raise Http404
    response = HttpResponse(reports_data.day_csv(reports_data.day_report(day)), content_type="text/csv")
    name = slugify(StoreSettings.load().name) or "report"
    response["Content-Disposition"] = f'attachment; filename="{name}-{day.isoformat()}.csv"'
    return response


def _parse_day(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


# ---------- retail orders ----------

@superuser_required
def orders(request):
    try:
        page_number = int(request.GET.get("page", 1))
    except ValueError:
        page_number = 1
    data = orders_data.order_list(request.GET.get("date", "today"), request.GET.get("status", "all"),
                                  request.GET.get("q", ""), page_number)
    return page(request, "admin_portal/orders.html", "retail", {"o": data})


@superuser_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    context = {"d": orders_data.order_page(order), "sheet": request.GET.get("sheet", "")}
    if context["sheet"] == "send":
        channel = request.GET.get("svc", ReceiptLog.EMAIL)
        context["send"] = {"svc": channel if channel in {key for key, _label, on in SEND_CHANNELS if on} else ReceiptLog.EMAIL,
                           "to": request.GET.get("to", context["d"]["email"]), "channels": SEND_CHANNELS}
    elif context["sheet"] == "refund" and context["d"]["can_refund"]:
        picks = {}
        for key, value in request.GET.items():
            if key.startswith("q_") and key[2:].isdigit() and value.lstrip("-").isdigit():
                picks[int(key[2:])] = int(value)
        context["refund"] = orders_data.refund_sheet(order, request.GET.get("mode", "whole"), picks,
                                                     request.GET.get("reason", ""), request.GET.get("to", ""))
    else:
        context["sheet"] = ""
    return page(request, "admin_portal/order.html", "retail", context)


# (channel, label, enabled): email works; the rest are placeholders until those integrations exist
SEND_CHANNELS = [(ReceiptLog.EMAIL, _lazy("Email"), True), (ReceiptLog.SMS, _lazy("Text"), False),
                 (ReceiptLog.WHATSAPP, _lazy("WhatsApp"), False), (ReceiptLog.TELEGRAM, _lazy("Telegram"), False),
                 (ReceiptLog.MESSENGER, _lazy("Messenger"), False)]


@superuser_required
@require_POST
def order_note(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    order.staff_note = request.POST.get("note", "").strip()[:2000]
    order.save(update_fields=["staff_note"])
    messages.success(request, _("Note saved."))
    return redirect("admin_order", order_id=order.id)


@superuser_required
@require_POST
def order_send(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    to = request.POST.get("to", "").strip()
    back = redirect(f"{reverse('admin_order', args=[order.id])}?sheet=send&to={quote(to)}")
    try:
        validate_email(to)
    except ValidationError:
        messages.error(request, _("Enter an email address."))
        return back
    try:
        sent = send_email("receipt", to, orders_data.receipt_data(order, StoreSettings.load().name))
    except EmailError as e:
        messages.error(request, _("The receipt wasn't sent: %(error)s") % {"error": e})
        return back
    if not sent:
        messages.error(request, _("Email isn't set up yet (LISTMONK_URL), so nothing was sent."))
        return back
    ReceiptLog.objects.create(order=order, channel=ReceiptLog.EMAIL, to=to, by=request.user)
    if not order.email:
        order.email = to
        order.save(update_fields=["email"])
    messages.success(request, _("Receipt emailed to %(to)s.") % {"to": to})
    return redirect("admin_order", order_id=order.id)


@superuser_required
@require_POST
def order_refund(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    whole = request.POST.get("mode", "whole") != "items"
    picks = {int(k[2:]): v for k, v in request.POST.items() if k.startswith("q_") and k[2:].isdigit()}
    try:
        refund = refund_order(order, picks=picks, whole=whole, reason=request.POST.get("reason", ""),
                              to=request.POST.get("to", Refund.ORIGINAL), user=request.user)
    except RefundError as e:
        messages.error(request, str(e))
        query = urlencode({k: v for k, v in request.POST.items() if k != "csrfmiddlewaretoken"})
        return redirect(f"{reverse('admin_order', args=[order.id])}?sheet=refund&{query}")
    where = _("in cash") if refund.to == Refund.CASH else _("to the original payment")
    messages.success(request, _("%(amount)s refunded %(where)s.") % {"amount": f"${refund.amount:.2f}", "where": where})
    return redirect("admin_order", order_id=order.id)


@superuser_required
@require_POST
def order_print(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    ReceiptLog.objects.create(order=order, channel=ReceiptLog.PRINT, by=request.user)
    return redirect("admin_order_receipt", order_id=order.id)


@superuser_required
def order_receipt(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, "admin_portal/receipt.html", {"d": orders_data.order_page(order), "store": StoreSettings.load()})


# ---------- menus ----------

@superuser_required
def menus(request):
    return page(request, "admin_portal/menus.html", "menus", {"m": menus_data.menu_list()})


@superuser_required
@require_POST
def menu_new(request):
    position = (Menu.objects.aggregate(last=Max("position"))["last"] or 0) + 1
    menu = Menu.objects.create(title=_("New menu"), draft=True, position=position)
    return redirect("admin_menu", menu_id=menu.id)


@superuser_required
def menu_edit(request, menu_id):
    menu = get_object_or_404(Menu, pk=menu_id)
    tab = "schedule" if request.GET.get("tab") == "schedule" else "items"
    schedule = Schedule()
    context = {"menu": menu, "tab": tab, "status": menus_data.status(menu, schedule),
               "items_count": menu.dishes.count() + menu.combos.count()}
    if tab == "items":
        context["c"] = menus_data.catalog(menu, request.GET.get("group", "all"), request.GET.get("q", ""))
    else:
        context["s"] = menus_data.schedule_tab(menu)
    return page(request, "admin_portal/menu.html", "menus", context)


@superuser_required
@require_POST
def menu_action(request, menu_id):
    """Every change on the menu editor (it saves as you go). POST `action` plus its fields; back to the editor."""
    menu = get_object_or_404(Menu, pk=menu_id)
    action = request.POST.get("action")
    back = safe_next_url(request, request.POST.get("back")) or reverse("admin_menu", args=[menu.id])
    post = request.POST
    if action == "rename":
        menu.title = post.get("title", "").strip()[:140] or menu.title
        menu.save(update_fields=["title"])
    elif action == "toggle":
        model, related = (Combo, menu.combos) if post.get("kind") == "combo" else (Dish, menu.dishes)
        obj = get_object_or_404(model, pk=post.get("id"))
        (related.remove if related.filter(pk=obj.pk).exists() else related.add)(obj)
    elif action in ("add_shown", "remove_shown"):
        dishes = Dish.objects.filter(pk__in=post.getlist("dish"))
        combos = Combo.objects.filter(pk__in=post.getlist("combo"))
        if action == "add_shown":
            menu.dishes.add(*dishes)
            menu.combos.add(*combos)
        else:
            menu.dishes.remove(*dishes)
            menu.combos.remove(*combos)
    elif action == "publish":
        menu.draft = False
        menu.save(update_fields=["draft"])
        messages.success(request, _("%(menu)s is published.") % {"menu": menu.title})
    elif action == "delete":
        menu.delete()
        messages.success(request, _("%(menu)s deleted.") % {"menu": menu.title})
        return redirect("admin_menus")
    elif action in ("up", "down"):
        ordered = list(Menu.objects.all())
        i = ordered.index(menu)
        j = i - 1 if action == "up" else i + 1
        if 0 <= j < len(ordered):
            ordered[i], ordered[j] = ordered[j], ordered[i]
            for position, m in enumerate(ordered):
                if m.position != position:
                    Menu.objects.filter(pk=m.pk).update(position=position)
        return redirect("admin_menus")
    elif action == "auto":
        menu.auto = not menu.auto
        menu.save(update_fields=["auto"])
        if menu.auto and not menu.rules.exists():  # a starting point, like the design
            MenuRule.objects.create(menu=menu, kind=MenuRule.ON, type=MenuRule.WEEKLY, days="01234", time=datetime.time(11))
            MenuRule.objects.create(menu=menu, kind=MenuRule.OFF, type=MenuRule.WEEKLY, days="01234", time=datetime.time(15))
    elif action == "manual":
        menu.manual_on = not menu.manual_on
        menu.save(update_fields=["manual_on"])
    elif action == "add_rule":
        kind = MenuRule.OFF if post.get("kind") == MenuRule.OFF else MenuRule.ON
        rule_type = post.get("type")
        today = timezone.localdate()
        if rule_type == MenuRule.DATES:
            MenuRule.objects.create(menu=menu, kind=kind, type=rule_type, start_date=today + datetime.timedelta(days=7),
                                    end_date=today + datetime.timedelta(days=9))
        elif rule_type == MenuRule.MENU and kind == MenuRule.OFF:
            other = Menu.objects.exclude(pk=menu.pk).first()
            MenuRule.objects.create(menu=menu, kind=kind, type=rule_type, other_menu=other)
        else:
            MenuRule.objects.create(menu=menu, kind=kind, type=MenuRule.WEEKLY, days="01234",
                                    time=datetime.time(11 if kind == MenuRule.ON else 15))
    elif action.startswith("rule_"):
        rule = get_object_or_404(MenuRule, pk=post.get("rule"), menu=menu)
        if action == "rule_remove":
            rule.delete()
        elif action == "rule_day":
            days = set(rule.day_list) ^ {int(post.get("day", -1))}
            rule.days = "".join(str(d) for d in sorted(days) if 0 <= d < 7)
            rule.save(update_fields=["days"])
        elif action == "rule_time":
            try:
                rule.time = datetime.time.fromisoformat(post.get("time", ""))
                rule.save(update_fields=["time"])
            except ValueError:
                pass
        elif action == "rule_dates":
            try:
                start = datetime.date.fromisoformat(post.get("start", ""))
                end = datetime.date.fromisoformat(post.get("end", ""))
            except ValueError:
                messages.error(request, _("Pick both dates."))
            else:
                rule.start_date, rule.end_date = min(start, end), max(start, end)
                rule.save(update_fields=["start_date", "end_date"])
        elif action == "rule_menu":
            rule.other_menu = Menu.objects.exclude(pk=menu.pk).filter(pk=post.get("other")).first() or rule.other_menu
            rule.save(update_fields=["other_menu"])
    publish("menus")
    publish("inventory")  # the register refetches its catalog on this
    return redirect(back)


# ---------- dishes ----------

def _decimal(value, low=Decimal("0"), high=Decimal("99999")):
    """A money amount from a form, or None when it isn't one (or is out of range)."""
    try:
        number = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or not low <= number <= high:
        return None
    return number.quantize(Decimal("0.01"))


def _number(value, cast=int, low=0, high=100000):
    try:
        number = cast(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


@superuser_required
def dishes(request):
    data = dishes_data.dish_list(request.GET.get("q", ""), request.GET.get("by", "station"), request.GET.get("show", "all"))
    return page(request, "admin_portal/dishes.html", "dishes", {"l": data, "has_stations": Station.objects.exists()})


@superuser_required
@require_POST
def dish_new(request):
    station = Station.objects.first()
    if station is None:
        messages.error(request, _("Add a station first: every dish is made at one."))
        return redirect("admin_dishes")
    dish = Dish.objects.create(title=_("New dish"), price=Decimal("12.00"), station=station, availability="off",
                               category=DishCategory.objects.first())
    messages.success(request, _("New dish added. It stays off until you switch it on."))
    return redirect("admin_dish", dish_id=dish.id)


@superuser_required
def dish_edit(request, dish_id):
    dish = get_object_or_404(dishes_data.editor_queryset(), pk=dish_id)
    return page(request, "admin_portal/dish.html", "dishes", {"e": dishes_data.editor(dish)})


def _duplicate(dish):
    """A copy of the dish with its recipe, modifiers and catering prices, on no menus and switched off."""
    copy = Dish.objects.get(pk=dish.pk)
    copy.pk = None
    copy.title = (_("%(name)s (copy)") % {"name": dish.title})[:140]
    copy.availability = "off"
    copy.archived = False
    copy.save()
    for dc in dish.dishcomponent_set.all():
        DishComponent.objects.create(dish=copy, component=dc.component, quantity=dc.quantity, adjustable=dc.adjustable,
                                     max_portions=dc.max_portions, position=dc.position)
    for group in dish.option_groups.all():
        new_group = OptionGroup.objects.create(dish=copy, title=group.title, note=group.note, position=group.position)
        for o in group.options.all():
            Option.objects.create(group=new_group, title=o.title, price=o.price, component=o.component, quantity=o.quantity,
                                  multiplier=o.multiplier, is_default=o.is_default, position=o.position)
    for extra in dish.extras.all():
        Extra.objects.create(dish=copy, component=extra.component, kind=extra.kind, title=extra.title, price=extra.price,
                             quantity=extra.quantity, max_count=extra.max_count, position=extra.position)
    for tier in dish.catering_prices.all():
        CateringPrice.objects.create(dish=copy, quantity=tier.quantity, price=tier.price)
    return copy


def _dish_basics(dish, post):
    """The basics form: whatever is valid is saved, the rest is left as it was."""
    title = post.get("title", "").strip()[:140]
    if title:
        dish.title = title
    price = _decimal(post.get("price"))
    if price is not None:
        dish.price = price
    dish.description = post.get("description", dish.description).strip()[:2000]
    dish.ticket_name = post.get("ticket_name", dish.ticket_name).strip().upper()[:14]
    prep = _number(post.get("prep_minutes"), low=1, high=240)
    if prep is not None:
        dish.prep_minutes = prep
    dish.station = Station.objects.filter(pk=_number(post.get("station"))).first() or dish.station
    if "category" in post:
        dish.category = DishCategory.objects.filter(pk=_number(post.get("category"))).first()
    dish.save()


def _next_position(queryset):
    return (queryset.aggregate(last=Max("position"))["last"] or 0) + 1


def _dish_parts(dish, action, post):
    """Recipe rows: add a component, change its amount, let customers adjust it, remove it."""
    if action == "part_add":
        component = Component.objects.filter(pk=_number(post.get("component"))).first()
        if component and not dish.dishcomponent_set.filter(component=component).exists():
            DishComponent.objects.create(dish=dish, component=component, quantity=1, position=_next_position(dish.dishcomponent_set))
        return
    part = get_object_or_404(DishComponent, pk=_number(post.get("part")), dish=dish)
    if action == "part_remove":
        part.delete()
    elif action == "part_adjustable":
        part.adjustable = not part.adjustable
        part.save()
    else:
        quantity = _number(post.get("quantity"), cast=float, low=0, high=100000)
        if quantity:
            part.quantity = quantity
            part.save()


def _dish_catering(dish, action, post):
    if action == "catering":
        dish.catering = not dish.catering
        dish.save(update_fields=["catering"])
        if dish.catering and not dish.catering_prices.exists():  # a starting point, like the design
            CateringPrice.objects.create(dish=dish, quantity=max(10, dish.catering_min),
                                         price=(dish.price * Decimal("0.92") * 4).quantize(Decimal("1")) / 4)
    elif action == "catering_min":
        minimum = _number(post.get("min"), low=1, high=10000)
        if minimum is not None:
            dish.catering_min = minimum
            dish.save(update_fields=["catering_min"])
    elif action == "tier_add":
        last = dish.catering_prices.last()
        if last:
            CateringPrice.objects.create(dish=dish, quantity=last.quantity * 2, price=max(Decimal("0.25"), last.price - Decimal("0.50")))
        else:
            CateringPrice.objects.create(dish=dish, quantity=dish.catering_min, price=dish.price)
    else:
        tier = get_object_or_404(CateringPrice, pk=_number(post.get("tier")), dish=dish)
        if action == "tier_remove":
            tier.delete()
        else:
            quantity, price = _number(post.get("quantity"), low=1, high=100000), _decimal(post.get("price"))
            tier.quantity = quantity if quantity is not None else tier.quantity
            tier.price = price if price is not None else tier.price
            tier.save()


def _dish_modifiers(dish, action, post):
    """Option groups (pick one) and add-ons (pick any)."""
    if action == "group_add":
        group = OptionGroup.objects.create(dish=dish, title=_("New group"), position=_next_position(dish.option_groups))
        Option.objects.create(group=group, title=_("Option"), is_default=True)
    elif action in ("group_rename", "group_remove", "option_add"):
        group = get_object_or_404(OptionGroup, pk=_number(post.get("group")), dish=dish)
        if action == "group_remove":
            group.delete()
        elif action == "option_add":
            Option.objects.create(group=group, title=_("New option"), position=_next_position(group.options),
                                  is_default=not group.options.exists())
        else:
            group.title = post.get("title", "").strip()[:80] or group.title
            group.save(update_fields=["title"])
    elif action in ("option_set", "option_default", "option_remove"):
        option = get_object_or_404(Option, pk=_number(post.get("option")), group__dish=dish)
        group = option.group
        if action == "option_remove":
            option.delete()
            rest = group.options.all()
            if rest and not any(o.is_default for o in rest):
                Option.objects.filter(pk=rest[0].pk).update(is_default=True)
        elif action == "option_default":
            group.options.update(is_default=False)
            Option.objects.filter(pk=option.pk).update(is_default=True)
        else:
            option.title = post.get("title", "").strip()[:80] or option.title
            price = _decimal(post.get("price"), low=Decimal("-999"))
            option.price = price if price is not None else option.price
            option.save(update_fields=["title", "price"])
    elif action == "addon_add":
        component = Component.objects.filter(pk=_number(post.get("component"))).first()
        if component:
            Extra.objects.create(dish=dish, component=component, kind=Extra.ADDON, price=component.extra_price,
                                 position=_next_position(dish.extras))
    elif action in ("addon_set", "addon_remove"):
        extra = get_object_or_404(Extra, pk=_number(post.get("addon")), dish=dish, kind=Extra.ADDON)
        if action == "addon_remove":
            extra.delete()
        else:
            extra.title = post.get("title", "").strip()[:80]
            price = _decimal(post.get("price"))
            extra.price = price if price is not None else extra.price
            extra.save(update_fields=["title", "price"])


@superuser_required
@require_POST
def dish_action(request, dish_id):
    """Every change on the dish editor (it saves as you go). POST `action` plus its fields; back to the editor."""
    dish = get_object_or_404(Dish, pk=dish_id)
    action, post = request.POST.get("action", ""), request.POST
    back = reverse("admin_dish", args=[dish.id])

    if action == "basics":
        _dish_basics(dish, post)
    elif action == "photo":
        upload = request.FILES.get("photo")
        try:
            forms.ImageField().clean(upload)
        except ValidationError:
            messages.error(request, _("That file isn't a picture we can use."))
        else:
            dish.image.save(upload.name, upload, save=True)
            messages.success(request, _("Photo updated."))
    elif action == "availability" and post.get("value") in ("off", "auto", "always"):
        dish.availability = post["value"]
        dish.save(update_fields=["availability"])
    elif action == "menu":
        menu = get_object_or_404(Menu, pk=_number(post.get("menu")))
        (dish.menu.remove if dish.menu.filter(pk=menu.pk).exists() else dish.menu.add)(menu)
    elif action.startswith("catering") or action.startswith("tier_"):
        _dish_catering(dish, action, post)
    elif action.startswith("part_"):
        _dish_parts(dish, action, post)
    elif action.startswith(("group_", "option_", "addon_")):
        _dish_modifiers(dish, action, post)
    elif action == "duplicate":
        copy = _duplicate(dish)
        messages.success(request, _("Duplicated. Editing the copy now."))
        back = reverse("admin_dish", args=[copy.id])
    elif action == "archive":
        dish.archived = True
        dish.save(update_fields=["archived"])
        dish.menu.clear()
        messages.success(request, _("%(dish)s archived. It's off every menu.") % {"dish": dish.title})
        back = reverse("admin_dishes")
    elif action == "restore":
        dish.archived = False
        dish.save(update_fields=["archived"])
        messages.success(request, _("%(dish)s is back. Add it to a menu to sell it.") % {"dish": dish.title})
    publish("menus")
    publish("inventory")  # the register refetches its catalog on this
    return redirect(back)
