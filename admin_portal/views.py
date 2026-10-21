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

from inventory.models import Recipe, RecipeStep
from misc_tools.email import EmailError, send as send_email
from pos_server.devices import superuser_required
from pavilion.realtime import publish
from pos_server.models import Combo, ComboSlot, Dish, Menu, Order, ReceiptLog, Refund, Station
from pos_server.refunds import RefundError, refund_order

from . import combos as combos_data
from . import dashboard as dashboard_data
from . import dishes as dishes_data
from . import menus as menus_data
from . import orders as orders_data
from . import recipes as recipes_data
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
    return page(request, "admin_portal/dishes.html", "dishes", {"l": data, "has_stations": Station.objects.exists()})


@superuser_required
@require_POST
def dish_new(request):
    station = Station.objects.first()
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
