"""
The admin portal (/admin/): the store's back office. Superuser only for now. Pages follow CLAUDE.md: Django
templates, forms that POST and redirect, static/js/live-page.js to keep them current.
"""
import datetime
import time

from urllib.parse import quote, urlencode

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
from pos_server.models import Combo, Dish, Menu, MenuRule, Order, ReceiptLog, Refund
from pos_server.refunds import RefundError, refund_order

from . import dashboard as dashboard_data
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
