"""
The admin portal (/admin/): the store's back office. Superuser only for now. Pages follow CLAUDE.md: Django
templates, forms that POST and redirect, static/js/live-page.js to keep them current.
"""
import datetime
import time
from decimal import Decimal

from urllib.parse import quote, urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Max
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _, gettext_lazy as _lazy, ngettext

from catering import flow as catering_flow
from catering.models import CateringMessage, CateringOrder
from catering.payments import PaymentFailed
from inventory.models import Recipe, RecipeStep, Restock
from misc_tools.email import EmailError, send as send_email
from pos_server.devices import superuser_required
from pavilion.realtime import publish
from pos_server.models import Combo, ComboSlot, Component, Condiment, Dish, Disposable, Extra, Ingredient, Menu, Order, ReceiptLog, Refund, Station, Supplier
from pos_server.refunds import RefundError, refund_order
from services.models import Service

from . import branding as branding_data
from . import catering as catering_data
from . import combos as combos_data
from . import audio as audio_data
from . import events as events_data
from . import hours as hours_data
from . import staff as staff_data
from . import shifts as shifts_data
from . import pars as pars_data
from . import requests as requests_data
from . import roles as roles_data
from . import library as library_data
from . import screens as screens_data
from . import stations as stations_data
from . import components as components_data
from . import condiments as condiments_data
from . import dashboard as dashboard_data
from . import dishes as dishes_data
from . import disposables as disposables_data
from . import ingredients as ingredients_data
from . import menus as menus_data
from . import orders as orders_data
from . import recipes as recipes_data
from . import reports as reports_data
from . import restocks as restocks_data
from . import services as services_data
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
    """
    The menu editor: one form (items, schedule and display tabs) with Save. Its state travels in a hidden `state`
    field (admin_portal/menus.py); POST merges the fields on screen and applies `op` (tab:…, toggle:…, add_rule:…,
    page:…, preview...) without saving. Save writes it (and publishes a draft); Back asks first when it's unsaved.
    """
    menu = get_object_or_404(Menu, pk=menu_id)
    store = StoreSettings.load().name

    def show(draft, leave_ask=False):
        context = menus_data.editor(menu, draft, leave_ask)
        tab = draft["ui"]["tab"]
        if tab == "items":
            context["c"] = menus_data.catalog(draft, draft["ui"]["group"], draft["ui"]["q"])
        elif tab == "schedule":
            context["s"] = menus_data.schedule_tab(draft, menu)
        else:
            context["v"] = menus_data.display_tab(draft, menu, store)
        return page(request, "admin_portal/menu.html", "menus", context)

    if request.method != "POST":
        draft = menus_data.draft_from_menu(menu)
        if request.GET.get("tab") in menus_data.TABS:
            draft["ui"]["tab"] = request.GET["tab"]
        return show(draft)
    op = request.POST.get("op", "")
    draft = menus_data.merge_post(menus_data.load_state(request.POST, menu), request.POST, menu)
    if op == "delete":
        menu.delete()
        publish("menus")
        messages.success(request, _("%(menu)s deleted.") % {"menu": menu.title})
        return redirect("admin_menus")
    if op == "leave":
        if not menus_data.is_dirty(draft, menu):
            return redirect("admin_menus")
        return show(draft, leave_ask=True)
    if op == "save":
        issues = menus_data.problems(draft, menu)
        if issues:
            messages.error(request, issues[0])
            return show(draft)
        published = menu.draft
        menus_data.save_draft(draft, menu)
        publish("menus")
        publish("inventory")  # the register refetches its catalog on this
        messages.success(request, (_("%(menu)s saved and published") if published else _("%(menu)s saved")) % {"menu": menu.title})
        return redirect(reverse("admin_menu", args=[menu.id]) + f"?tab={draft['ui']['tab']}")
    return show(menus_data.apply_op(draft, op, request.POST, menu))


@superuser_required
@require_POST
def menu_action(request, menu_id):
    """The list's priority arrows (POST `action` up or down)."""
    menu = get_object_or_404(Menu, pk=menu_id)
    action = request.POST.get("action")
    if action in ("up", "down"):
        ordered = list(Menu.objects.all())
        i = ordered.index(menu)
        j = i - 1 if action == "up" else i + 1
        if 0 <= j < len(ordered):
            ordered[i], ordered[j] = ordered[j], ordered[i]
            for position, m in enumerate(ordered):
                if m.position != position:
                    Menu.objects.filter(pk=m.pk).update(position=position)
        publish("menus")
    return redirect("admin_menus")


# ---------- dishes ----------

@superuser_required
def dishes(request):
    data = dishes_data.dish_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/dishes.html", "dishes", {"l": data, "has_stations": Station.objects.filter(archived=False).exists()})


@superuser_required
@require_POST
def dish_new(request):
    station = Station.objects.filter(archived=False).first()
    if station is None:
        messages.error(request, _("Add a station first: every dish is made at one."))
        return redirect("admin_dishes")
    dish = Dish.objects.create(title=_("New dish"), price=Decimal("12.00"), station=station, prep_minutes=6)
    return redirect("admin_dish", dish_id=dish.id)


@superuser_required
def dish_edit(request, dish_id):
    """
    The dish editor: one form. GET shows the saved dish. POST sends the whole form back with `op`: Save writes
    it; Duplicate saves the edited copy as a new dish; Archive / Restore act on the saved dish; Back asks first
    when there are unsaved changes; anything else (adding or removing rows, a changed field) shows the draft
    again with the sums worked out, without saving.
    """
    dish = get_object_or_404(dishes_data.editor_queryset(), pk=dish_id)
    if request.method != "POST":
        return page(request, "admin_portal/dish.html", "dishes", {"e": dishes_data.editor(dish, dishes_data.draft_from_dish(dish))})

    op = request.POST.get("op", "")
    draft = dishes_data.draft_from_post(request.POST)
    photo = bool(request.POST.get("photo_pending"))  # a photo chosen but not saved yet
    if op == "archive":
        dish.archived = True
        dish.save(update_fields=["archived"])
        dish.menu.clear()
        _dishes_changed()
        messages.success(request, _("%(dish)s archived") % {"dish": dish.title})
        return redirect("admin_dishes")
    if op == "restore":
        dish.archived = False
        dish.save(update_fields=["archived"])
        _dishes_changed()
        messages.success(request, _("%(dish)s is back. Add it to a menu to sell it.") % {"dish": dish.title})
        return redirect("admin_dish", dish_id=dish.id)
    if op == "leave":
        if not photo and not dishes_data.is_dirty(draft, dish):
            return redirect("admin_dishes")
        return page(request, "admin_portal/dish.html", "dishes", {"e": dishes_data.editor(dish, draft, leave_ask=True, photo_pending=photo)})
    if op in ("save", "duplicate"):
        issues = dishes_data.problems(draft)
        if issues:
            messages.error(request, issues[0])
        else:
            if op == "duplicate":
                draft["title"] = (_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140]
                draft["menus"] = []
                for row in draft["parts"] + draft["groups"] + [o for g in draft["groups"] for o in g["options"]]:
                    row["id"] = ""
                target = Dish(image=dish.image)
            else:
                target = dish
            target = dishes_data.save_draft(draft, target)
            if request.FILES.get("photo"):
                upload = request.FILES["photo"]
                try:
                    forms.ImageField().clean(upload)
                except ValidationError:
                    messages.error(request, _("That file isn't a picture we can use."))
                else:
                    target.image.save(upload.name, upload, save=True)
            _dishes_changed()
            messages.success(request, _("Duplicated. Editing the copy now") if op == "duplicate"
                             else _("%(dish)s saved") % {"dish": target.title})
            return redirect("admin_dish", dish_id=target.id)
    draft = dishes_data.apply_op(draft, op, request.POST)
    return page(request, "admin_portal/dish.html", "dishes", {"e": dishes_data.editor(dish, draft, photo_pending=photo)})


def _dishes_changed():
    publish("menus")
    publish("inventory")  # the register refetches its catalog on this


# ---------- combos ----------

@superuser_required
def combos(request):
    data = combos_data.combo_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/combos.html", "combos", {"l": data})


@superuser_required
@require_POST
def combo_new(request):
    combo = Combo.objects.create(title=_("New combo"), ticket_name=_("NEW CMB")[:12], active=False)
    for position, title in enumerate([_("Main"), _("Side"), _("Drink")]):
        ComboSlot.objects.create(combo=combo, title=title, position=position)
    return redirect("admin_combo", combo_id=combo.id)


@superuser_required
def combo_edit(request, combo_id):
    """The combo editor, one form like the dish editor (see dish_edit for the `op`s)."""
    combo = get_object_or_404(Combo.objects.prefetch_related("menu", "slots__options__dish"), pk=combo_id)
    if request.method != "POST":
        return page(request, "admin_portal/combo.html", "combos", {"e": combos_data.editor(combo, combos_data.draft_from_combo(combo))})
    op = request.POST.get("op", "")
    draft = combos_data.draft_from_post(request.POST)
    if op == "archive":
        combo.archived = True
        combo.save(update_fields=["archived"])
        combo.menu.clear()
        _dishes_changed()
        messages.success(request, _("%(combo)s archived") % {"combo": combo.title})
        return redirect("admin_combos")
    if op == "restore":
        combo.archived = False
        combo.save(update_fields=["archived"])
        _dishes_changed()
        messages.success(request, _("%(combo)s is back. Add it to a menu to sell it.") % {"combo": combo.title})
        return redirect("admin_combo", combo_id=combo.id)
    draft = combos_data.apply_op(draft, op, request.POST)
    if op == "leave":
        if not combos_data.is_dirty(draft, combo):
            return redirect("admin_combos")
        return page(request, "admin_portal/combo.html", "combos", {"e": combos_data.editor(combo, draft, leave_ask=True)})
    if op in ("save", "duplicate"):
        if combos_data.problems(draft):
            messages.error(request, _("Fix the marked choices first."))
        else:
            if op == "duplicate":
                draft["title"] = (_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140]
                draft["active"] = False
                for row in draft["slots"] + [o for s in draft["slots"] for o in s["options"]]:
                    row["id"] = ""
                target = Combo()
            else:
                target = combo
            target = combos_data.save_draft(draft, target)
            _dishes_changed()
            messages.success(request, _("Duplicated. Editing the copy now") if op == "duplicate"
                             else _("%(combo)s saved") % {"combo": target.title})
            return redirect("admin_combo", combo_id=target.id)
    return page(request, "admin_portal/combo.html", "combos", {"e": combos_data.editor(combo, draft)})


# ---------- recipes ----------

@superuser_required
def recipes(request):
    data = recipes_data.recipe_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/recipes.html", "recipes", {"l": data})


@superuser_required
@require_POST
def recipe_new(request):
    recipe = Recipe.objects.create(title=_("New recipe"), yield_quantity=10)
    RecipeStep.objects.create(recipe=recipe, title="")
    return redirect("admin_recipe", recipe_id=recipe.id)


@superuser_required
def recipe_edit(request, recipe_id):
    """The recipe editor, one form like the dish editor (Save; other buttons post an `op` and show the draft)."""
    recipe = get_object_or_404(Recipe, pk=recipe_id)
    if request.method != "POST":
        return page(request, "admin_portal/recipe.html", "recipes", {"e": recipes_data.editor(recipe, recipes_data.draft_from_recipe(recipe))})
    op = request.POST.get("op", "")
    draft = recipes_data.apply_op(recipes_data.draft_from_post(request.POST), op, request.POST)
    if op == "leave":
        if not recipes_data.is_dirty(draft, recipe):
            return redirect("admin_recipes")
        return page(request, "admin_portal/recipe.html", "recipes", {"e": recipes_data.editor(recipe, draft, leave_ask=True)})
    if op == "delete":
        recipe.delete()
        publish("inventory")
        messages.success(request, _("%(recipe)s deleted") % {"recipe": draft["title"] or recipe})
        return redirect("admin_recipes")
    if op == "save":
        issues = recipes_data.problems(draft)
        if issues:
            messages.error(request, issues[0])
        else:
            recipes_data.save_draft(draft, recipe)
            publish("inventory")  # Cookery refetches on this
            messages.success(request, _("%(recipe)s saved") % {"recipe": recipe.title})
            return redirect("admin_recipe", recipe_id=recipe.id)
    return page(request, "admin_portal/recipe.html", "recipes", {"e": recipes_data.editor(recipe, draft)})


# ---------- services ----------

@superuser_required
def services(request):
    data = services_data.service_list(request.GET.get("q", ""), request.GET.get("cat", "all"), request.GET.get("show", ""))
    return page(request, "admin_portal/services.html", "services", {"l": data})


@superuser_required
@require_POST
def service_new(request):
    service = Service.objects.create(title=_("New service"), category=Service.STAFF, pricing=Service.HOUR,
                                     details=dict(Service.DETAILS[Service.STAFF]), online=False, active=False)
    return redirect("admin_service", service_id=service.id)


@superuser_required
def service_edit(request, service_id):
    """The service editor, one form like the combo editor (Save; other buttons post an `op` and show the draft)."""
    service = get_object_or_404(Service, pk=service_id)
    if request.method != "POST":
        return page(request, "admin_portal/service.html", "services", {"e": services_data.editor(service, services_data.draft_from_service(service))})
    op = request.POST.get("op", "")
    draft = services_data.draft_from_post(request.POST)
    if op in ("archive", "restore"):
        service.archived = op == "archive"
        service.save(update_fields=["archived"])
        if op == "archive":
            messages.success(request, _("%(service)s archived") % {"service": service.title})
            return redirect("admin_services")
        messages.success(request, _("%(service)s is back") % {"service": service.title})
        return redirect("admin_service", service_id=service.id)
    if op == "leave":
        if not services_data.is_dirty(draft, service):
            return redirect("admin_services")
        return page(request, "admin_portal/service.html", "services", {"e": services_data.editor(service, draft, leave_ask=True)})
    if op in ("save", "duplicate"):
        issue = services_data.problems(draft)
        if issue:
            messages.error(request, issue)
        else:
            if op == "duplicate":
                draft["title"] = (_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140]
                draft["active"] = False
                target = Service()
            else:
                target = service
            services_data.save_draft(draft, target)
            messages.success(request, _("Duplicated. Editing the copy now") if op == "duplicate"
                             else _("%(service)s saved") % {"service": target.title})
            return redirect("admin_service", service_id=target.id)
    return page(request, "admin_portal/service.html", "services", {"e": services_data.editor(service, draft)})


# ---------- components ----------

@superuser_required
def components(request):
    data = components_data.component_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/components.html", "components", {"l": data})


@superuser_required
@require_POST
def component_new(request):
    component = Component.objects.create(title=_("New component"), unit_of_measurement="ea", category="other", crafting_option="craft")
    return redirect("admin_component", component_id=component.id)


@superuser_required
def component_edit(request, component_id):
    """The component editor, one form like the dish editor (Save; other buttons post an `op` and show the draft)."""
    component = get_object_or_404(Component, pk=component_id)
    if request.method != "POST":
        draft = components_data.draft_from_component(component)
        return page(request, "admin_portal/component.html", "components", {"e": components_data.editor(component, draft)})
    op = request.POST.get("op", "")
    draft = components_data.apply_op(components_data.draft_from_post(request.POST), op, request.POST, component)
    if op == "archive":
        parts, addons = components_data.usage(component)
        if parts or addons:
            messages.error(request, _("Take it out of its dishes first."))
        else:
            component.archived = True
            component.save(update_fields=["archived"])
            _dishes_changed()
            messages.success(request, _("%(component)s archived") % {"component": component.title})
            return redirect("admin_components")
    elif op == "restore":
        component.archived = False
        component.save(update_fields=["archived"])
        _dishes_changed()
        messages.success(request, _("%(component)s is back") % {"component": component.title})
        return redirect("admin_component", component_id=component.id)
    elif op == "leave":
        if not components_data.is_dirty(draft, component):
            return redirect("admin_components")
        return page(request, "admin_portal/component.html", "components", {"e": components_data.editor(component, draft, leave_ask=True)})
    elif op in ("save", "duplicate"):
        target = Component() if op == "duplicate" else component
        if op == "duplicate":
            draft.update(title=(_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140], recipe="", inventory="0",
                         inventory_was="0", offer=[])
        issue = components_data.problems(draft, target)
        if issue:
            messages.error(request, issue)
        else:
            components_data.save_draft(draft, target, copy_of=component if op == "duplicate" else None)
            _dishes_changed()
            messages.success(request, _("Duplicated. Editing the copy now") if op == "duplicate"
                             else _("%(component)s saved") % {"component": target.title})
            return redirect("admin_component", component_id=target.id)
    return page(request, "admin_portal/component.html", "components", {"e": components_data.editor(component, draft)})


# ---------- ingredients ----------

@superuser_required
def ingredients(request):
    data = ingredients_data.ingredient_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/ingredients.html", "ingredients", {"l": data})


@superuser_required
@require_POST
def ingredient_new(request):
    ingredient = Ingredient.objects.create(title=_("New ingredient"), unit_of_measurement="g", category="produce", pack_size=1000)
    return redirect("admin_ingredient", ingredient_id=ingredient.id)


@superuser_required
def ingredient_edit(request, ingredient_id):
    """The ingredient editor, one form like the dish editor (Save; other buttons post an `op` and show the draft)."""
    ingredient = get_object_or_404(Ingredient.objects.select_related("supplier"), pk=ingredient_id)
    if request.method != "POST":
        draft = ingredients_data.draft_from_ingredient(ingredient)
        return page(request, "admin_portal/ingredient.html", "ingredients", {"e": ingredients_data.editor(ingredient, draft)})
    op = request.POST.get("op", "")
    draft = ingredients_data.draft_from_post(request.POST)
    if op == "archive":
        if ingredients_data.uses(ingredient):
            messages.error(request, _("Take it out of its recipes and components first."))
        else:
            ingredient.archived = True
            ingredient.save(update_fields=["archived"])
            messages.success(request, _("%(ingredient)s archived") % {"ingredient": ingredient.title})
            return redirect("admin_ingredients")
    elif op == "restore":
        ingredient.archived = False
        ingredient.save(update_fields=["archived"])
        messages.success(request, _("%(ingredient)s is back") % {"ingredient": ingredient.title})
        return redirect("admin_ingredient", ingredient_id=ingredient.id)
    elif op == "leave":
        if not ingredients_data.is_dirty(draft, ingredient):
            return redirect("admin_ingredients")
        return page(request, "admin_portal/ingredient.html", "ingredients", {"e": ingredients_data.editor(ingredient, draft, leave_ask=True)})
    elif op in ("save", "duplicate"):
        target = Ingredient() if op == "duplicate" else ingredient
        if op == "duplicate":
            draft.update(title=(_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140], inventory="0", inventory_was="0")
        issue = ingredients_data.problems(draft, target)
        if issue:
            messages.error(request, issue)
        else:
            ingredients_data.save_draft(draft, target)
            _dishes_changed()  # component and dish costs, allergens and diets follow the ingredient
            changed = ingredients_data.component_costs_changed(target)
            if op == "duplicate":
                messages.success(request, _("Duplicated. Editing the copy now"))
            elif changed:
                messages.success(request, ngettext("%(ingredient)s saved. %(n)d component updated", "%(ingredient)s saved. %(n)d components updated", changed)
                                 % {"ingredient": target.title, "n": changed})
            else:
                messages.success(request, _("%(ingredient)s saved") % {"ingredient": target.title})
            return redirect("admin_ingredient", ingredient_id=target.id)
    return page(request, "admin_portal/ingredient.html", "ingredients", {"e": ingredients_data.editor(ingredient, draft)})


# ---------- condiments ----------

@superuser_required
def condiments(request):
    data = condiments_data.condiment_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/condiments.html", "condiments", {"l": data})


@superuser_required
@require_POST
def condiment_new(request):
    component = Component.objects.create(title=_("New condiment"), unit_of_measurement="ea", category="sauce", crafting_option="craft")
    condiment = Condiment.objects.create(component=component, served_as="cup", serving=_("2 oz cup"), price=Decimal("0.50"))
    return redirect("admin_condiment", condiment_id=condiment.id)


@superuser_required
def condiment_edit(request, condiment_id):
    """The condiment editor, one form like the dish editor (Save; other buttons post an `op` and show the draft)."""
    condiment = get_object_or_404(Condiment.objects.select_related("component__storage", "component__recipe"), pk=condiment_id)
    if request.method != "POST":
        draft = condiments_data.draft_from_condiment(condiment)
        return page(request, "admin_portal/condiment.html", "condiments", {"e": condiments_data.editor(condiment, draft)})
    op = request.POST.get("op", "")
    draft = condiments_data.apply_op(condiments_data.draft_from_post(request.POST), op)
    if op in ("archive", "restore"):
        component = condiment.component
        component.archived = op == "archive"
        component.save(update_fields=["archived"])
        if op == "archive":
            Extra.objects.filter(component=component).delete()  # off every dish
            _dishes_changed()
            messages.success(request, _("%(condiment)s archived") % {"condiment": component.title})
            return redirect("admin_condiments")
        messages.success(request, _("%(condiment)s is back. Pick the dishes to suggest it with.") % {"condiment": component.title})
        return redirect("admin_condiment", condiment_id=condiment.id)
    if op == "leave":
        if not condiments_data.is_dirty(draft, condiment):
            return redirect("admin_condiments")
        return page(request, "admin_portal/condiment.html", "condiments", {"e": condiments_data.editor(condiment, draft, leave_ask=True)})
    if op in ("save", "duplicate"):
        target = Condiment() if op == "duplicate" else condiment
        if op == "duplicate":
            # A new component (and, if bought, its own ingredient) for the copy; a recipe makes one thing
            draft.update(title=(_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140], inventory="0", inventory_was="0", recipe="")
        issue = condiments_data.problems(draft, target)
        if issue:
            messages.error(request, issue)
        else:
            condiments_data.save_draft(draft, target)
            _dishes_changed()
            messages.success(request, _("Duplicated. Editing the copy now") if op == "duplicate"
                             else _("%(condiment)s saved") % {"condiment": target.component.title})
            return redirect("admin_condiment", condiment_id=target.id)
    return page(request, "admin_portal/condiment.html", "condiments", {"e": condiments_data.editor(condiment, draft)})


# ---------- disposables ----------

@superuser_required
def disposables(request):
    data = disposables_data.disposable_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/disposables.html", "disposables", {"l": data})


@superuser_required
@require_POST
def disposable_new(request):
    ingredient = Ingredient.objects.create(title=_("New item"), unit_of_measurement="ea", category="other", pack_size=100)
    disposable = Disposable.objects.create(ingredient=ingredient, order_types=["pickup", "delivery"])
    return redirect("admin_disposable", disposable_id=disposable.id)


@superuser_required
def disposable_edit(request, disposable_id):
    """The disposable editor, one form like the dish editor (Save; other buttons post an `op` and show the draft)."""
    disposable = get_object_or_404(Disposable.objects.select_related("ingredient__supplier", "ingredient__storage"), pk=disposable_id)
    if request.method != "POST":
        draft = disposables_data.draft_from_disposable(disposable)
        return page(request, "admin_portal/disposable.html", "disposables", {"e": disposables_data.editor(disposable, draft)})
    op = request.POST.get("op", "")
    draft = disposables_data.apply_op(disposables_data.draft_from_post(request.POST), op)
    if op in ("archive", "restore"):
        ingredient = disposable.ingredient
        ingredient.archived = op == "archive"
        ingredient.save(update_fields=["archived"])
        if op == "archive":
            messages.success(request, _("%(item)s archived") % {"item": disposable.title})
            return redirect("admin_disposables")
        messages.success(request, _("%(item)s is back") % {"item": disposable.title})
        return redirect("admin_disposable", disposable_id=disposable.id)
    if op == "leave":
        if not disposables_data.is_dirty(draft, disposable):
            return redirect("admin_disposables")
        return page(request, "admin_portal/disposable.html", "disposables", {"e": disposables_data.editor(disposable, draft, leave_ask=True)})
    if op in ("save", "duplicate"):
        target = Disposable() if op == "duplicate" else disposable
        if op == "duplicate":  # its own ingredient, with nothing on hand
            draft.update(title=(_("%(name)s (copy)") % {"name": draft["title"].strip()})[:140], inventory="0", inventory_was="0")
        issue = disposables_data.problems(draft, target)
        if issue:
            messages.error(request, issue)
        else:
            disposables_data.save_draft(draft, target)
            messages.success(request, _("Duplicated. Editing the copy now") if op == "duplicate" else _("%(item)s saved") % {"item": target.title})
            return redirect("admin_disposable", disposable_id=target.id)
    return page(request, "admin_portal/disposable.html", "disposables", {"e": disposables_data.editor(disposable, draft)})


# ---------- restocks ----------

@superuser_required
def restocks(request):
    if request.method == "POST":  # "Draft order" for a supplier with items under par
        supplier = get_object_or_404(Supplier, pk=request.POST.get("supplier"))
        restock = restocks_data.draft_order(supplier, request.user)
        messages.success(request, _("Drafted %(number)s from %(supplier)s") % {"number": restock.number, "supplier": supplier.name})
        return redirect("admin_restock", restock_id=restock.id)
    data = restocks_data.restock_list(request.GET.get("q", ""), request.GET.get("show", "all"))
    return page(request, "admin_portal/restocks.html", "restocks", {"l": data})


@superuser_required
@require_POST
def restock_new(request):
    supplier = Supplier.objects.order_by("name").first()
    if supplier is None:
        messages.error(request, _("Add a supplier on an ingredient first."))
        return redirect("admin_restocks")
    restock = Restock.objects.create(supplier=supplier, created_by=request.user,
                                     expected_on=timezone.localdate() + datetime.timedelta(days=3))
    return redirect("admin_restock", restock_id=restock.id)


@superuser_required
def restock_edit(request, restock_id):
    """The restock editor, one form like the dish editor (Save; other buttons post an `op` and show the draft)."""
    restock = get_object_or_404(Restock.objects.select_related("supplier"), pk=restock_id)
    if request.method != "POST":
        return page(request, "admin_portal/restock.html", "restocks", {"e": restocks_data.editor(restock, restocks_data.draft_from_restock(restock))})
    op = request.POST.get("op", "")
    # A receipt is kept as soon as it's picked (it isn't part of the draft)
    if request.FILES.get("receipt"):
        upload = request.FILES["receipt"]
        if upload.size > 20 * 1024 * 1024:
            messages.error(request, _("That file is too big (20 MB at most)."))
        else:
            restock.receipt.save(upload.name, upload, save=True)
    if op == "remove_receipt" and restock.receipt:
        restock.receipt.delete(save=True)
    draft = restocks_data.apply_op(restocks_data.draft_from_post(request.POST), op, request.POST, restock)
    if op == "delete":
        if restock.status == Restock.DELIVERED:
            messages.error(request, _("Delivered orders stay on record."))
        else:
            number = restock.number
            restock.delete()
            messages.success(request, _("%(number)s deleted") % {"number": number})
            return redirect("admin_restocks")
    elif op == "duplicate":
        copy = restocks_data.copy_as_draft(restock, request.user)
        messages.success(request, _("Copied into a new draft, %(number)s") % {"number": copy.number})
        return redirect("admin_restock", restock_id=copy.id)
    elif op == "leave":
        if not restocks_data.is_dirty(draft, restock):
            return redirect("admin_restocks")
        return page(request, "admin_portal/restock.html", "restocks", {"e": restocks_data.editor(restock, draft, leave_ask=True)})
    elif op == "save":
        issue = restocks_data.problems(draft, restock)
        if issue:
            messages.error(request, issue)
        else:
            taken = restocks_data.save_draft(draft, restock, request.user)
            if taken:
                _dishes_changed()  # stock and costs moved
                messages.success(request, ngettext("Stock added for %(n)d item", "Stock added for %(n)d items", taken) % {"n": taken})
            else:
                messages.success(request, _("%(number)s saved") % {"number": restock.number})
            return redirect("admin_restock", restock_id=restock.id)
    return page(request, "admin_portal/restock.html", "restocks", {"e": restocks_data.editor(restock, draft)})


# ---------- catering ----------

def _catering_page(request, tab, order, draft=None, sheet=""):
    data = catering_data.inbox(tab, order)
    order = data["selected"]
    return page(request, "admin_portal/catering.html", "catering", {
        "c": data, "d": catering_data.detail(order, request.user, draft, sheet) if order else None, "view": "orders"})


def _catering_tab(request, default="new"):
    tab = request.GET.get("tab", "")
    return tab if tab in dict((key, 1) for key, *_rest in catering_data.TABS) else default


@superuser_required
def catering(request):
    return _catering_page(request, _catering_tab(request), None)


@superuser_required
def catering_order(request, order_id):
    """One catering order. POST ops: reply, accept, reject, remind, finish, keep, send (the proposed changes), and
    the editing draft's buttons (anything else), which come back as the page with the draft."""
    order = get_object_or_404(CateringOrder, pk=order_id)
    tab = _catering_tab(request, catering_data.tab_of(order.status))
    if request.method != "POST":
        editing = request.GET.get("edit") == "1" and order.status in (CateringOrder.NEW, CateringOrder.WAITING)
        draft = catering_data.draft_from_order(order) if editing else None
        return _catering_page(request, tab, order, draft, request.GET.get("sheet", ""))
    op, post, first = request.POST.get("op", ""), request.POST, order.first_name
    editor_ops = ("preview", "less", "more", "hless", "hmore", "remove", "undo", "add_kind", "add")
    if op.partition(":")[0] in editor_ops:
        draft = catering_data.apply_op(catering_data.draft_from_post(post), op, order)
        return _catering_page(request, tab, order, draft)
    try:
        if op == "reply":
            catering_flow.write(order, CateringMessage.STAFF, post.get("text", ""), request.user, request)
            messages.success(request, _("Sent to %(name)s.") % {"name": first})
        elif op == "accept":
            catering_flow.accept(order, request.user, post.get("message", ""), request)
            messages.success(request, _("Accepted. %(total)s charged and %(name)s gets a confirmation.") % {"total": catering_data.money(order.charged), "name": first})
            tab = "upcoming"
        elif op == "reject":
            was_new = order.status == CateringOrder.NEW
            catering_flow.reject(order, request.user, post.get("reason", ""), post.get("message", ""), request)
            messages.success(request, (_("Order rejected. %(name)s gets an email with your reason.") if was_new else _("Order cancelled. %(name)s gets an email with your reason.")) % {"name": first})
            tab = "past"
        elif op == "remind":
            catering_flow.remind(order, request)
            messages.success(request, _("Reminder sent to %(name)s.") % {"name": first})
        elif op in ("finish", "keep"):
            catering_flow.finish(order, request.user, keep_deposit=op == "keep")
            messages.success(request, _("Deposit kept and the order is completed.") if op == "keep" else _("Order completed."))
            tab = "past"
        elif op == "send":
            draft = catering_data.draft_from_post(post)
            catering_flow.propose(order, request.user, draft["lines"], draft["msg"], request)
            messages.success(request, _("Changes sent to %(name)s. Nothing is charged until they accept.") % {"name": first})
            tab = "waiting"
    except (catering_flow.CateringError, PaymentFailed) as exc:
        messages.error(request, str(exc))
        if op == "send":
            return _catering_page(request, tab, order, catering_data.draft_from_post(post))
        if op in ("accept", "reject"):
            return _catering_page(request, tab, order, None, op)
    return redirect(reverse("admin_catering_order", args=[order.id]) + f"?tab={tab}")


@superuser_required
def catering_settings(request):
    """Catering settings: one form with Save; switches and day buttons post an `op` and come back as the draft."""
    from catering.models import CateringSettings
    if request.method == "POST":
        draft, op = catering_data.settings_from_post(request.POST), request.POST.get("op", "")
        if op == "save":
            problem = catering_data.settings_problems(draft)
            if problem:
                messages.error(request, problem)
            else:
                catering_data.save_settings(draft)
                messages.success(request, _("Catering settings saved.") if draft["on"] else _("Saved. Catering is off for customers."))
                return redirect("admin_catering_settings")
        else:
            draft = catering_data.settings_op(draft, op)
    else:
        draft = catering_data.settings_draft(CateringSettings.load())
    return page(request, "admin_portal/catering_settings.html", "catering", {
        "s": catering_data.settings_page(draft), "view": "settings",
        "c": {"new_count": CateringOrder.objects.filter(status=CateringOrder.NEW).count(), "off": not draft["on"]}})


# ---------- store hours ----------

@superuser_required
def store_hours(request):
    """Store hours: one form with Save; every change posts the draft back (admin_portal/hours.py). Pausing and resuming
    online orders happen right away."""
    if request.method == "POST":
        draft, op = hours_data.from_post(request.POST), request.POST.get("op", "")
        if op == "save":
            if hours_data.problem_count(draft):
                messages.error(request, _("Fix the hours marked in red first."))
            else:
                hours_data.save(draft)
                messages.success(request, _("Store hours saved. The online store and register are updated."))
                return redirect("admin_hours")
        elif op.startswith("pause:"):
            hours_data.pause(min(max(hours_data._int(op[6:]), 0), 240))
            messages.success(request, _("Online orders paused. The store shows “Back soon”."))
        elif op == "resume":
            hours_data.resume()
            messages.success(request, _("Online orders are back on"))
        else:
            copied = hours_data.copy_days(draft, request.POST)
            if copied:
                messages.success(request, copied)
            draft = hours_data.apply_op(draft, op)
    else:
        draft = hours_data.draft_of()
    return page(request, "admin_portal/hours.html", "hours", {"h": hours_data.page(draft)})


# ---------- stations ----------

@superuser_required
def stations(request):
    """Stations: the list and the selected station's settings, one form with Save (admin_portal/stations.py)."""
    move_to = ""
    if request.method == "POST":
        draft, op = stations_data.from_post(request.POST), request.POST.get("op", "")
        move_to = request.POST.get("move_to", "")
        if op == "save":
            problems = stations_data.problems(draft)
            if problems:
                messages.error(request, problems[0])
            else:
                stations_data.save(draft)
                publish("orders")
                messages.success(request, _("Stations saved. Order marking screens pick up the changes on their next refresh."))
                return redirect("admin_stations")
        else:
            draft, toast = stations_data.apply_op(draft, op, request.POST)
            if toast:
                messages.success(request, toast)
    else:
        draft = stations_data.draft_of()
    return page(request, "admin_portal/stations.html", "stations", {"s": stations_data.page(draft, move_to)})


# ---------- events ----------

@superuser_required
def events(request):
    """Events: the calendar, what's coming up and the selected event, one form with Save (admin_portal/events.py)."""
    if request.method == "POST":
        draft, op = events_data.from_post(request.POST), request.POST.get("op", "")
        if op == "save":
            if any(events_data.errors(x) for x in draft["events"]):
                messages.error(request, _("Fix the events marked in red first."))
            else:
                events_data.save(draft)
                messages.success(request, _("Events saved. Store hours, the online store and the register are updated."))
                return redirect("admin_events")
        else:
            draft, toast = events_data.apply_op(draft, op)
            if toast:
                messages.success(request, toast)
    else:
        draft = events_data.draft_of()
    return page(request, "admin_portal/events.html", "events", {"v": events_data.page(draft)})


# ---------- screens ----------

@superuser_required
def screens(request):
    """The screens, each with a thumbnail of its layout; ?new=1 opens "New screen" (name and orientation)."""
    from screens.models import Screen
    new = {"open": request.GET.get("new") == "1", "name": "", "orient": "land"}
    if request.method == "POST":
        new.update(open=True, name=request.POST.get("name", "").strip(), orient=(request.POST.getlist("orient") or ["land"])[-1])
        if new["orient"] not in screens_data.SIZES:
            new["orient"] = "land"
        if request.POST.get("op") == "create" and new["name"]:
            screen = screens_data.create(new["name"], new["orient"])
            state = screens_data.load(request, screen)
            state["sel"] = "r"  # straight to the widget library
            screens_data.store(request, screen, state)
            return redirect("admin_screen", id=screen.id)
    return page(request, "admin_portal/screens.html", "screens", {"l": screens_data.list_page(), "new": new})


@superuser_required
def screen_edit(request, id):
    """The screen editor: the draft (in the session, with undo) is changed by each button's op and saved with Save."""
    from screens.models import Screen
    screen = get_object_or_404(Screen, pk=id)
    state = screens_data.load(request, screen)
    if request.method == "POST":
        op = request.POST.get("op", "")
        if op == "leave":
            dirty = screens_data.is_dirty(screen, state)
            screens_data.forget(request, screen)
            if dirty:
                messages.success(request, _("Left without saving. Changes were discarded."))
            return redirect("admin_screens")
        for problem in screens_data.from_post(state, request.POST, request.FILES):
            messages.error(request, problem)
        if op.startswith("sdel:"):  # removing a session happens right away
            from screens.models import ScreenSession
            gone = ScreenSession.objects.filter(screen=screen, pk=screens_data._int(op[5:])).first()
            if gone:
                gone.delete()
                messages.success(request, _("%(name)s removed. Its display goes back to the session list.") % {"name": gone.name})
        elif op == "save":
            problems = screens_data.save(screen, state)
            if problems:
                messages.error(request, problems[0])
            else:
                state["hist"] = []
                screens_data.store(request, screen, state)
                publish("screens")
                messages.success(request, _("Saved. Open screens update within a few seconds."))
                return redirect("admin_screen", id=screen.id)
        else:
            toast = screens_data.apply_op(state, op, request.POST.get("editing", ""))
            if toast:
                messages.success(request, toast)
        screens_data.store(request, screen, state)
    return page(request, "admin_portal/screen.html", "screens", {"e": screens_data.editor_page(screen, state)})


@superuser_required
def screen_preview(request, id):
    """The editor's preview: the draft rendered like the real screen, with each node marked so it can be picked."""
    import time as time_
    from screens.layout import render_screen
    from screens.models import Screen
    screen = get_object_or_404(Screen, pk=id)
    draft = screens_data.load(request, screen)["draft"]
    width, height = screens_data.SIZES[draft["orient"]]
    shown = Screen(id=screen.id, name=draft["name"], slug=screen.slug, layout=draft["layout"], width=width, height=height,
                   background=draft["bg"], padding=screen.padding, public=draft["public"])
    shown.popups = draft.get("popups", [])
    rendered = render_screen(shown, request, editing={"sel": request.GET.get("sel", ""), "popup": request.GET.get("popup", "")})
    return render(request, "screens/screen.html", {"screen": shown, "rendered": rendered, "topics": [], "preview": True,
                                                   "now_ms": int(time_.time() * 1000)})


# ---------- audio channels ----------

@superuser_required
def audio_channels(request):
    """Audio channels: the list and the selected channel, one form with Save (admin_portal/audio.py)."""
    new = None
    if request.method == "POST":
        draft, op = audio_data.from_post(request.POST), request.POST.get("op", "")
        if op == "save":
            problems = audio_data.problems(draft)
            if problems:
                messages.error(request, problems[0])
            else:
                audio_data.save(draft)
                publish("screens")
                messages.success(request, _("Audio channels saved. Screens pick up the changes on their next refresh."))
                return redirect("admin_audio")
        elif op == "new" or op.startswith("newkind:"):
            kind = op.partition(":")[2] or request.POST.get("new_kind") or "music"
            new = {"name": request.POST.get("new_name", ""), "kind": kind if kind in audio_data.TYPES else "music"}
        elif op != "closenew":
            toast = audio_data.apply_op(draft, op, request.POST)
            if toast:
                messages.success(request, toast)
    else:
        draft = audio_data.draft_of()
    return page(request, "admin_portal/audio.html", "audio", {"a": audio_data.page(draft, new)})


@superuser_required
def library(request):
    """Library: songs and playlists (admin_portal/library.py). The view lives in the address; buttons post an op
    and come back to it."""
    if request.method == "POST":
        toast, go = library_data.apply(request.POST, request.FILES)
        if toast:
            messages.success(request, toast)
        st = {**library_data.state(request.POST), **{k: v for k, v in go.items() if k in ("view", "q", "sort")}}
        return redirect(reverse("admin_library") + library_data.url(st))
    st = library_data.state(request.GET)
    data = library_data.page(st, song=request.GET.get("song"), add=request.GET.get("add") == "1", aq=request.GET.get("aq", ""))
    return page(request, "admin_portal/library.html", "library", {"l": data})


@superuser_required
@require_POST
def library_upload(request):
    """One song from the Add music dialog: the file as is, plus the tags the browser read."""
    status, body = library_data.upload(request.POST, request.FILES)
    return JsonResponse(body, status=status)


# ---------- team ----------

@superuser_required
def staff(request):
    """Staff: the list and the selected profile, one form with Save (admin_portal/staff.py). Adding someone and their
    photo take effect straight away."""
    users = staff_data.staff_users()
    st = staff_data.state(request.session)
    if request.method == "POST":
        op = request.POST.get("op", "preview")
        staff_data.take_post(st, request.POST, users, request.user)
        if request.POST.get("profile") == "1" and st.get("sel") is not None:
            person = next((u for u in users if u.id == st["sel"]), None)
            if person and request.FILES.get("photo"):
                upload = request.FILES["photo"]
                if upload.content_type in ("image/jpeg", "image/png", "image/webp") and upload.size <= 10 * 1024 * 1024:
                    p = staff_data.profile(person)
                    old = p.photo.name if p.photo else ""
                    p.photo.save(f"{person.id}.{upload.content_type.split('/')[1].replace('jpeg', 'jpg')}", upload)
                    if old:
                        p.photo.storage.delete(old)
            elif person and op == "photoclear":
                p = staff_data.profile(person)
                if p.photo:
                    p.photo.storage.delete(p.photo.name)
                    p.photo = ""
                    p.save(update_fields=["photo"])
        msg = st.pop("msg", "")
        if op == "save":
            current = staff_data.rows(st, users)
            errs = staff_data.problems(current, users, staff_data.edited(st))
            if errs:
                messages.error(request, next(iter(errs.values())))
            else:
                staff_data.save(st, users)
                st = {**staff_data.state({}), "sel": st["sel"], "q": st["q"], "role": st["role"]}
                staff_data.store(request.session, st)
                messages.success(request, _("Staff saved."))
                return redirect("admin_staff")
        elif op == "add":
            st["add"] = {"tab": "find", "aq": "", "pick": None, "first": "", "last": "", "email": "", "phone": "", "invite": True}
        elif op.startswith("addtab:") and st.get("add") is not None:
            st["add"]["tab"] = "new" if op == "addtab:new" else "find"
        elif op.startswith("acct:") and st.get("add") is not None:
            uid = int(op.partition(":")[2] or 0)
            st["add"]["pick"] = None if st["add"].get("pick") == uid else uid
        elif op == "closeadd":
            st["add"] = None
        elif op == "promote" and st.get("add"):
            person = User.objects.filter(pk=st["add"].get("pick"), is_staff=False).first()
            if person:
                staff_data.promote(person)
                st.update(add=None, sel=person.id, q="", role="all")
                messages.success(request, _("%(name)s added to staff. Pick their roles and groups.") % {"name": person.first_name or person.username})
        elif op == "create" and st.get("add"):
            person, problem = staff_data.create(st["add"])
            if problem:
                messages.error(request, problem)
            else:
                sent = st["add"].get("invite", True)
                if sent:
                    try:
                        staff_data.invite(request, person)
                    except EmailError:
                        sent = False
                        messages.error(request, _("The invite email couldn’t be sent. Check listmonk, then try again from their profile."))
                st.update(add=None, sel=person.id, q="", role="all")
                messages.success(request, (_("%(name)s added to staff, invite sent to %(email)s. Pick their roles and groups.") if sent
                                           else _("%(name)s added to staff. Pick their roles and groups.")) % {"name": person.first_name, "email": person.email})
        else:
            toast = staff_data.apply_op(st, op, users, request.user)
            msg = msg or toast
        if msg:
            messages.success(request, msg)
        staff_data.store(request.session, st)
        users = staff_data.staff_users()
    else:
        st["add"] = st["confirm"] = None  # a fresh visit: the draft stays, the dialog and the "are you sure" don't
        staff_data.store(request.session, st)
    return page(request, "admin_portal/staff.html", "staff", {"s": staff_data.page(st, users, request.user)})


@superuser_required
def shifts(request):
    """Shifts: the week's schedule (admin_portal/shifts.py). Shifts save as they're made, moved or deleted; Publish
    tells staff."""
    from team import schedule
    from team.models import Shift
    if request.method == "POST":
        st = shifts_data.state(request.POST)
        op = request.POST.get("op", "")
        shift = Shift.objects.filter(pk=shifts_data._int(request.POST.get("id"))).select_related("user").first()
        if op == "preview":  # the drawer, worked out again from what's been picked (nothing saved)
            draft = shifts_data.from_post(request.POST)
            data = shifts_data.page(st, open_shift=shift, new={} if not shift else None, draft=draft)
            return page(request, "admin_portal/shifts.html", "shifts", {"h": data})
        if op == "save":
            draft = shifts_data.from_post(request.POST)
            if draft["end"] <= draft["start"]:
                messages.error(request, _("The shift ends before it starts."))
                return redirect(reverse("admin_shifts") + shifts_data.url(st, shift=draft["id"], new=None if draft["id"] else 1))
            messages.success(request, shifts_data.save(st, draft))
        elif op == "delete" and shift:
            messages.success(request, shifts_data.delete(shift))
        elif op == "move" and shift:
            messages.success(request, shifts_data.move(shift, shifts_data._int(request.POST.get("user")), shifts_data._int(request.POST.get("start"), shift.start),
                                                       shifts_data._int(request.POST.get("role"))))
        elif op == "publish":
            n = schedule.publish(request)
            messages.success(request, ngettext("Schedule published. %(n)d staff member told about their changes.",
                                               "Schedule published. %(n)d staff told about their changes.", n) % {"n": n})
        return redirect(reverse("admin_shifts") + shifts_data.url(st))
    st = shifts_data.state(request.GET)
    shift = Shift.objects.filter(pk=shifts_data._int(request.GET.get("shift")), deleted=False).first() if request.GET.get("shift") else None
    new = request.GET if request.GET.get("new") == "1" else None
    return page(request, "admin_portal/shifts.html", "shifts", {"h": shifts_data.page(st, open_shift=shift, new=new)})


@superuser_required
def shift_pars(request):
    """Shift pars: how many people each role needs (admin_portal/pars.py), one form with Save."""
    st = pars_data.state(request.session)
    if request.method == "POST":
        pars_data.take_post(st, request.POST)
        op = request.POST.get("op", "preview")
        if op == "save":
            bad = [pars_data.problem(x) for x in st["sets"] if pars_data.problem(x)]
            if bad:
                messages.error(request, bad[0])
            else:
                pars_data.save(st)
                request.session.pop(pars_data.KEY, None)
                messages.success(request, _("Pars saved. The coverage strip on Shifts uses them now."))
                return redirect("admin_shift_pars")
        elif op != "preview":
            toast = pars_data.apply_op(st, op, request.POST)
            if toast:
                messages.success(request, toast)
        request.session[pars_data.KEY] = st
    return page(request, "admin_portal/pars.html", "shifts", {"x": pars_data.page(st)})


@superuser_required
def availability_requests(request):
    """Availability requests: time off and availability changes to approve or decline (admin_portal/requests.py)."""
    from team import requests as team_requests
    if request.method == "POST":
        st = requests_data.state(request.POST)
        req = requests_data.find(request.POST.get("key", ""))
        op = request.POST.get("op")
        if req and op in ("approve", "decline") and req.status == "pending":
            approve = op == "approve"
            opened = team_requests.decide(req, approve, request.user, request.POST.get("message", ""), request.POST.get("handle", "open"))
            first = req.user.first_name or req.user.username
            if approve:
                messages.success(request, (ngettext("Approved. %(name)s gets a notification and %(n)d shift moved to open shifts.",
                                                    "Approved. %(name)s gets a notification and %(n)d shifts moved to open shifts.", opened)
                                           if opened else _("Approved. %(name)s gets a notification.")) % {"name": first, "n": opened})
            else:
                messages.success(request, (_("Declined. %(name)s gets a notification with your message.") if req.message
                                           else _("Declined. %(name)s gets a notification.")) % {"name": first})
        elif req and op == "reopen":
            team_requests.reopen(req)
            st["tab"] = "pending"
        return redirect(reverse("admin_requests") + requests_data.url(st, sel=req and requests_data.key_of(req)))
    return page(request, "admin_portal/requests.html", "requests", {"q": requests_data.page(requests_data.state(request.GET), request.user)})


@superuser_required
def roles(request):
    """Roles: the jobs people do (admin_portal/roles.py), one form with Save."""
    st = roles_data.state(request.session)
    if request.method == "POST":
        roles_data.take_post(st, request.POST)
        op = request.POST.get("op", "preview")
        toast = st.pop("msg", "")
        if op == "save":
            errs = roles_data.problems(st)
            if errs:
                messages.error(request, next(iter(errs.values())))
            else:
                roles_data.save(st)
                request.session.pop(roles_data.KEY, None)
                messages.success(request, _("Roles saved. Staff profiles show the changes."))
                return redirect("admin_roles")
        elif op != "preview":
            toast = roles_data.apply_op(st, op) or toast
        if toast:
            messages.success(request, toast)
        request.session[roles_data.KEY] = st
    return page(request, "admin_portal/roles.html", "roles", {"x": roles_data.page(st)})


# ---------- branding ----------

@superuser_required
def branding(request):
    """Branding: the theme's settings form (every change is kept as a draft, see branding.py) and its preview."""
    from online_store import themes as store_themes
    theme = store_themes.get(request.GET.get("theme") or StoreSettings.load().theme)
    request.session[store_themes.PREVIEW_KEY] = theme.name  # the preview iframe shows this theme's draft
    device = request.GET.get("device", "desktop")
    if request.method == "POST":
        values, store = branding_data.from_post(theme, request.POST)
        for key, file in request.FILES.items():
            target = key.removeprefix("upload-")
            saved = branding_data.upload(file, "logo" if target == "s.logo" else target)
            if not saved:
                messages.error(request, _("Use a PNG, JPEG, WebP, GIF or SVG image under 8 MB."))
            elif target == "s.logo":
                store["logo"] = saved
            elif target in theme.params and theme.params[target].type == "image":
                values[target] = saved
        op = request.POST.get("op", "")
        values, store = branding_data.apply_op(theme, values, store, op)
        if op == "publish":
            if not store["name"].strip():
                messages.error(request, _("Give the store a name first."))
            else:
                branding_data.publish(theme, values, store)
                messages.success(request, _("%(name)s is live on your online store") % {"name": theme.title})
        else:
            branding_data.save_draft(theme, values, store)
        if op in ("publish", "reset"):
            return redirect(reverse("admin_branding") + f"?theme={theme.name}&device={device}")
    else:
        values, store = branding_data.draft_of(theme)
    return page(request, "admin_portal/branding.html", "branding", {"b": branding_data.page(theme, values, store, device)})
