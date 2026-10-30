"""
The themed store's ordering pages: the menu (with the dish sheet and the cart beside it), checkout and the placed
order. They follow CLAUDE.md: forms post and redirect or come back as the page; static/js/live-page.js submits them
in the background and swaps the regions that changed. The cart and the checkout's choices live in the session
(online_store/cart.py); what's on the menu and when we're open come from online_store/storefront.py.

Orders can be for as soon as possible (while the store takes pickup or delivery orders and they aren't paused) or,
when the store lets customers order ahead, scheduled up to StoreHours.ahead_days ahead: inside the store's hours
after the pickup or delivery cutoffs (events/hours.py) and while a menu with everything in the cart is live.
"""
import datetime
import logging
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction as db_transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy, ngettext

from deliveries.models import Delivery
from events.hours import DELIVERY, DINE, PICKUP, Hours
from gift_cards.models import GiftCard
from payments.models import PaymentError, Transaction
from pavilion import throttle
from pos_server import dish_info
from pos_server.menu_schedule import Schedule
from pos_server.models import Combo, Dish, Menu, Order, OrderPayment, OrderStation
from pos_server.orders import OrderError, create_order
from pos_server.pricing import badge_for, badges, price_cart
from users.models import SavedAddress

from . import cart as store_cart, guest_orders, storefront, themes

logger = logging.getLogger(__name__)
from .views import menu_sections, theme_page

CENT = Decimal("0.01")
TIPS = [0, 10, 15, 18]
SCHEDULE_DAYS = 15  # the most the store can take orders ahead (StoreHours.ahead_days ≤ 14)
STEP_MINUTES = 5


def _picture(pictures):
    return lambda item: storefront.picture(item, pictures)


# ---------- the menu ----------

def _menu_tabs(selected_id):
    now = timezone.localtime()
    menus = [m for m in storefront.menus_now(limit=6) if m["state"] == "now" or m["order"] < 24 * 60]
    selected = next((m for m in menus if str(m["menu"].id) == str(selected_id)), menus[0] if menus else None)
    for m in menus:
        m["on"] = m is selected
    return menus, selected


def _item(obj, pictures, index, deals=None):
    is_combo = isinstance(obj, Combo)
    diets = [] if is_combo else [d["short"] for d in dish_info.dish_diets(obj)]
    available = obj.available
    return {"obj": obj, "is_combo": is_combo, "price": store_cart.money(obj.standard_price() if is_combo else obj.price),
            "diets": [d for d in diets if d in ("V", "VG", "GF")], "sold_out": not available,
            "picture": storefront.picture(obj, pictures), "tone": index % 4, "deal": badge_for(deals or {}, obj),
            "url": f"?{'combo' if is_combo else 'dish'}={obj.id}"}


STORE_LIMIT, STORE_WINDOW = 20, 10  # menu-sheet and checkout posts per visitor per window (seconds)


def too_fast(request, scope) -> bool:
    """Count a post and say so when this visitor is sending them faster than anyone tapping would
    (pavilion/throttle.py). The caller skips the action and redirects back with the message."""
    ok, _wait = throttle.allow(scope, throttle.client_key(request), STORE_LIMIT, STORE_WINDOW)
    if not ok:
        messages.error(request, _("That’s a lot of taps. Give it a second and try again."))
    return not ok


def menu_page(request, sheet_post=None):
    if request.method == "POST" and sheet_post is None:
        if too_fast(request, "store-menu"):
            return redirect(request.get_full_path())
        return _menu_post(request)
    t = themes.current(request).values
    pictures = t.get("pictures", "photos")
    actual = (sheet_post or request.GET).get("actual")
    tabs, selected = _menu_tabs(actual)
    menu = selected["menu"] if selected else None
    sections = []
    deals = badges("online")  # automatic discounts running now (the admin's Discounts page)
    for n, (station, items) in enumerate(menu_sections(menu)):
        visible = [obj for obj in items if getattr(obj, "visible_in_menu", True) and not getattr(obj, "archived", False)]
        if visible:
            sections.append({"id": f"s{station.id}", "title": station.menu_heading or station.name,
                             "items": [_item(obj, pictures, i + n, deals) for i, obj in enumerate(visible)]})
    notice, notice_kind = "", "good"
    if selected and selected["state"] == "now":
        notice = _("%(menu)s is being served now. Choose pickup or delivery, and when, at checkout.") % {"menu": menu.title}
    elif selected:
        notice = _("%(menu)s starts %(when)s. Add what you like and schedule it for then or later at checkout.") % {
            "menu": menu.title, "when": selected["pill"][:1].lower() + selected["pill"][1:]}
        notice_kind = "warn"
    if not storefront.open_now()["open"]:
        notice += " " + _("We’re closed right now, so orders are for later.")
        notice_kind = "warn"
    priced, problems = store_cart.priced(request)
    for problem in problems:
        messages.error(request, problem)
    rows = store_cart.rows(request, priced, _picture(pictures))
    return theme_page(request, "menu.html", {
        "tabs": tabs, "menu": menu, "sections": sections, "notice": notice, "notice_kind": notice_kind,
        "cart_rows": rows, "subtotal": priced.subtotal if priced else Decimal("0"), "sheet": _sheet(request, sheet_post),
        "count": sum(r["qty"] for r in rows), "query": actual or "",
    }, active="menu")


def _sheet(request, post):
    """The dish or combo sheet asked for in ?dish= / ?combo= (or in the posted form)."""
    source = post if post is not None else request.GET
    dish_id, combo_id = source.get("dish"), source.get("combo")
    t = themes.current(request).values
    if dish_id:
        kit = store_cart.kit_of(_int(dish_id))
        if not kit or not kit.dish.available:
            return None
        data = store_cart.sheet(kit, post)
        diets = [d["label"] for d in dish_info.dish_diets(kit.dish)]
        allergens = dish_info.dish_allergens(kit.dish)
        return {"kind": "dish", "obj": kit.dish, "id": kit.dish.id, "picture": storefront.picture(kit.dish, t.get("pictures", "photos")),
                "diets": diets, "allergens": ", ".join(allergens), **data}
    if combo_id:
        combo = store_cart.combo_of(_int(combo_id))
        if not combo or not combo.available:
            return None
        first = next((p.dish for p in combo.default_picks()), None)
        return {"kind": "combo", "obj": combo, "id": combo.id, "picture": storefront.picture(combo if combo.image or not first else first, t.get("pictures", "photos")),
                "diets": [], "allergens": "", **store_cart.combo_sheet(combo, post)}
    return None


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _menu_post(request):
    post = request.POST
    sheet_open = bool(post.get("dish") or post.get("combo"))
    op = post.get("op") or ("preview" if sheet_open else "")
    back = reverse("menu") + (f"?actual={post.get('actual')}" if post.get("actual") else "")
    if sheet_open and op in ("add", "preview", "more", "less"):
        sheet_post = post.copy()
        if op in ("more", "less"):
            sheet_post["qty"] = str(max(1, min(store_cart.MAX_QTY, _int(post.get("qty"), 1) + (1 if op == "more" else -1))))
        if op != "add":
            return menu_page(request, sheet_post)  # the sheet as it now is (prices follow the choices)
        if post.get("dish"):
            kit = store_cart.kit_of(_int(post.get("dish")))
            if kit is None:
                raise Http404
            data = store_cart.sheet(kit, sheet_post)
            if data["missing"] or data["error"]:
                messages.error(request, data["error"] or _("Choose %(what)s to add this.") % {"what": " and ".join(data["missing"]).lower()})
                return menu_page(request, sheet_post)
            store_cart.add(request, store_cart.line_from_post(kit, sheet_post))
            title = kit.dish.title
        else:
            combo = store_cart.combo_of(_int(post.get("combo")))
            if combo is None:
                raise Http404
            store_cart.add(request, store_cart.combo_line_from_post(combo, sheet_post))
            title = combo.title
        messages.success(request, _("%(item)s added to your order.") % {"item": title})
        return redirect(back)
    if op.startswith(("inc:", "dec:", "remove:")):
        name, _sep, key = op.partition(":")
        store_cart.change(request, key, delta=1 if name == "inc" else -1, remove=name == "remove")
    return redirect(post.get("next") or back)


def dish_redirect(request, id):
    """Old dish links open the dish's sheet on the menu."""
    return redirect(reverse("menu") + f"?dish={id}")


# ---------- checkout ----------

def _windows(day, delivery=False, hours=None):
    """When online orders can be for on `day` (the store's hours after the pickup or delivery cutoffs): [(start, end)]."""
    return (hours or Hours()).on_day(day, DELIVERY if delivery else PICKUP)


def _state(request) -> dict:
    """The checkout's choices (kept in the session). They survive signing in or out halfway through, except the
    card and saved address picked, which belonged to whoever was signed in (`who`)."""
    user = request.user
    state = request.session.get("checkout") or {}
    who = user.id if user.is_authenticated else None
    defaults = {"method": "pickup", "eat": "togo", "when": "asap", "date": timezone.localdate().isoformat(), "time": "",
                "tip": 15, "name": (user.get_full_name() if user.is_authenticated else ""), "phone": "",
                "email": user.email if user.is_authenticated else "", "address": "", "unit": "", "city": "",
                "postal": "", "driver_note": "", "note": "", "pay": "", "address_id": "", "save_card": True, "save_address": True, "address_label": "",
                "who": who}
    state = {**defaults, **{k: v for k, v in state.items() if k in defaults}}
    if state["who"] != who:
        state.update(who=who, pay="", address_id="")
        if user.is_authenticated:
            state["email"] = user.email
            state["name"] = state["name"] or user.get_full_name()
        request.session["checkout"] = state
    return state


def account_for(email):
    """Whether someone signs in with `email` (checkout offers to sign them in)."""
    email = (email or "").strip()
    if "@" not in email:
        return False
    return User.objects.filter(email__iexact=email, is_active=True).exists()


def _use_address(state, saved):
    state.update(address_id=str(saved.id), address=saved.line, unit=saved.unit, city=saved.city, postal=saved.postal,
                 driver_note=saved.note)


def _save_address(user, state):
    """Keep the new address a signed-in customer delivered to (unless it's one of theirs already)."""
    if user.saved_addresses.filter(line__iexact=state["address"], unit__iexact=state["unit"]).exists():
        return
    first = not user.saved_addresses.exists()
    saved = SavedAddress.objects.create(user=user, label=state["address_label"][:40] or _("Home"), line=state["address"][:160],
                                        unit=state["unit"][:60], city=state["city"][:80], postal=state["postal"][:12],
                                        note=state["driver_note"][:160], is_default=first)
    state["address_id"] = str(saved.id)


def _dine_ok(start) -> bool:
    """Whether the dining room is open at `start` (an order to eat here); unknown (no time yet) counts as open."""
    return start is None or Hours().current(start, DINE) is not None


def _from_post(state, post):
    if post.get("pay") and (post["pay"] == "new" or post["pay"].startswith("card:")):
        state["pay"] = post["pay"]
    for key in ("name", "phone", "email", "address", "unit", "city", "postal", "driver_note", "note", "address_label"):
        if key in post:
            state[key] = post.get(key, "").strip()[:200]
    if post.get("hour") is not None and post.get("minute") is not None:
        h, m, ap = _int(post.get("hour"), 12), _int(post.get("minute"), 0), post.get("ampm", "PM")
        if 1 <= h <= 12 and 0 <= m <= 59:
            state["time"] = f"{(h % 12) + (12 if ap == 'PM' else 0):02d}:{m:02d}"
    return state


def _apply_op(request, state, op, post):
    name, _sep, arg = op.partition(":")
    cart = store_cart.load(request)
    if name == "method" and arg in ("pickup", "delivery"):
        state["method"] = arg
    elif name == "eat" and arg in ("here", "togo"):
        state["eat"] = arg
    elif name == "save_card":
        state["save_card"] = not state["save_card"]
    elif name == "when" and arg in ("asap", "later"):
        state["when"] = arg
    elif name == "date":
        try:
            day = datetime.date.fromisoformat(arg)
            if 0 <= (day - timezone.localdate()).days < SCHEDULE_DAYS:
                state["date"] = arg
        except ValueError:
            pass
    elif name == "pay" and (arg == "new" or arg.startswith("card")):
        state["pay"] = arg
    elif name == "address" and arg == "new":
        state.update(address_id="new", address="", unit="", city="", postal="", driver_note="")
    elif name == "address":
        saved = request.user.saved_addresses.filter(pk=_int(arg)).first() if request.user.is_authenticated else None
        if saved:
            _use_address(state, saved)
    elif name == "save_address":
        state["save_address"] = not state["save_address"]
    elif name == "tip" and _int(arg, -1) in TIPS:
        state["tip"] = _int(arg)
    elif name in ("hup", "hdown", "mup", "mdown", "am", "pm"):
        current = state.get("time") or "12:00"
        h, m = (int(x) for x in current.split(":"))
        minutes = h * 60 + m + {"hup": 60, "hdown": -60, "mup": STEP_MINUTES, "mdown": -STEP_MINUTES}.get(name, 0)
        if name == "am" and minutes >= 720:
            minutes -= 720
        if name == "pm" and minutes < 720:
            minutes += 720
        minutes %= 1440
        state["time"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
    elif name == "code":
        code = post.get("code", "").strip()
        if not code:
            pass
        elif (gift := GiftCard.find(code)) is not None:
            if gift.frozen:
                messages.error(request, _("That gift card is frozen. Ask the store about it."))
            else:
                if gift.id not in cart["gifts"]:
                    cart["gifts"].append(gift.id)
                cart["skipped_gifts"] = [g for g in cart["skipped_gifts"] if g != gift.id]
                messages.success(request, _("Gift card added."))
        else:
            priced, _p = store_cart.priced(request)
            try:
                tried = price_cart(store_cart.plain(cart["lines"]), order_discount={"code": code}, channel="web", to_go=True, strict=True,
                                   customer=request.user if request.user.is_authenticated else None)
                cart["coupon"] = code.upper()
                if tried.order_discount:
                    messages.success(request, _("%(code)s applied.") % {"code": code.upper()})
                else:  # an automatic discount saves more
                    messages.info(request, tried.order_discount_error)
            except OrderError as e:
                messages.error(request, str(e))
        store_cart.save(request, cart)
    elif name == "drop_code":
        cart["coupon"] = ""
        store_cart.save(request, cart)
    elif name == "drop_gift":
        cart["gifts"] = [g for g in cart["gifts"] if str(g) != arg]
        if _int(arg) and _int(arg) not in cart["skipped_gifts"]:  # their own cards would come straight back
            cart["skipped_gifts"].append(_int(arg))
        store_cart.save(request, cart)
    elif name in ("inc", "dec", "remove"):
        store_cart.change(request, arg, delta=1 if name == "inc" else -1, remove=name == "remove")
    elif name == "upsell":
        dish = Dish.objects.filter(pk=_int(arg), archived=False).first()
        if dish and dish.available:
            store_cart.add(request, {"type": "dish", "id": dish.id, "qty": 1, "cust": {}, "note": ""})
    return state


def _clock(minutes):
    return storefront.clock(datetime.time(minutes // 60 % 24, minutes % 60))


def _schedule(state, cart_items, delivery):
    """When the order can be: today's/the chosen day's windows, the calendar, and whether the time works."""
    now = timezone.localtime()
    hours = Hours()
    cfg = hours.cfg
    lead = storefront.delivery_minutes() if delivery else storefront.pickup_minutes()
    paused = hours.paused_until(now)
    open_now = not paused and hours.current(now, DELIVERY if delivery else PICKUP) is not None
    if state["when"] == "asap" and not open_now and cfg.ahead:
        state["when"] = "later"
    if not cfg.ahead:
        state["when"] = "asap"
    today = now.date()
    days = []
    for n in range(cfg.ahead_days + 1 if cfg.ahead else 1):
        day = today + datetime.timedelta(days=n)
        days.append({"date": day, "iso": day.isoformat(), "open": bool(_windows(day, delivery, hours)), "on": state["date"] == day.isoformat(), "today": n == 0})
    try:
        day = datetime.date.fromisoformat(state["date"])
        if not any(d["date"] == day for d in days):
            raise ValueError
    except ValueError:
        day = today
        state["date"] = today.isoformat()
    windows = _windows(day, delivery, hours)
    ok, message, start = False, "", None
    if state["when"] == "asap":
        if paused:
            message = _("Online orders are paused until %(time)s.") % {"time": storefront.clock(timezone.localtime(paused).time())}
            if cfg.ahead:
                message += " " + _("Schedule it for later.")
        elif open_now:
            start, ok = now, True
            message = (_("Arrives around %(time)s") if delivery else _("Ready around %(time)s")) % {"time": storefront.clock((now + datetime.timedelta(minutes=lead)).time())}
        else:
            message = _("We’re closed right now. Schedule it for later.") if cfg.ahead else _("We’re closed right now.")
    else:
        if not state["time"] and windows:
            first = max(windows[0][0], now + datetime.timedelta(minutes=lead)) if day == today else windows[0][0]
            first = timezone.localtime(first)
            exact = first.hour * 60 + first.minute + (1 if first.second or first.microsecond else 0)  # never before `first`
            minute = (exact + STEP_MINUTES - 1) // STEP_MINUTES * STEP_MINUTES
            state["time"] = f"{minute // 60 % 24:02d}:{minute % 60:02d}"
        h, m = (int(x) for x in (state["time"] or "12:00").split(":"))
        at = timezone.make_aware(datetime.datetime.combine(day, datetime.time(h % 24, m)), timezone.get_current_timezone())
        earliest = max(now + datetime.timedelta(minutes=cfg.lead + (storefront.drive_minutes() if delivery else 0)), paused or now)
        inside = next(((a, b) for a, b in windows if a <= at <= b - datetime.timedelta(minutes=15)), None)
        if not windows:
            message = _("We’re closed that day.")
        elif at < earliest:
            message = _("The earliest is %(time)s.") % {"time": storefront.clock(timezone.localtime(max(earliest, windows[0][0])).time())}
        elif not inside:
            a, b = windows[0][0], windows[-1][1] - datetime.timedelta(minutes=15)
            message = _("We take orders for %(from)s to %(to)s that day.") % {"from": storefront.clock(timezone.localtime(a).time()), "to": storefront.clock(timezone.localtime(b).time())}
        else:
            ok, start = True, at
            when = _("today") if day == today else _("tomorrow") if day == today + datetime.timedelta(days=1) else f"{day:%a, %b} {day.day}"
            message = (_("Arrives %(when)s at %(time)s, give or take 10 min") if delivery else _("Ready for pickup %(when)s at %(time)s")) % {
                "when": when, "time": storefront.clock(at.time())}
    # Everything in the cart has to be on a menu that's live then: the dish, or the combo (not the dishes picked in it,
    # which may only be sold as part of the combo)
    if ok and cart_items:
        schedule = Schedule(list(Menu.objects.filter(draft=False)))
        live = {m.id for m in schedule.live(start)}
        off = [item.title for item in cart_items if not any(m.id in live for m in item.menu.all())]
        if off:
            ok = False
            message = _("%(items)s isn’t on the menu then.") % {"items": ", ".join(off[:2])}
    h, m = (int(x) for x in (state["time"] or "12:00").split(":"))
    picker = {"hour": (h % 12) or 12, "minute": f"{m:02d}", "ampm": "PM" if h >= 12 else "AM"}
    hint = " · ".join(f"{storefront.clock(timezone.localtime(a).time())} – {storefront.clock(timezone.localtime(b).time())}" for a, b in windows) or _("Closed")
    return {"ok": ok, "message": message, "start": start, "open_now": open_now, "days": days, "picker": picker, "ahead": cfg.ahead,
            "paused": storefront.clock(timezone.localtime(paused).time()) if paused else "",
            "hint": hint, "lead": lead}


def _gift_cover(request, total):
    """[(GiftCard, amount)] the cart's gift cards pay, in order, up to `total`; a signed-in customer's own gift
    cards (sent to their email) are used too, unless they removed them (cart["skipped_gifts"])."""
    out, left = [], total
    cart = store_cart.load(request)
    ids = [g for g in cart["gifts"] if isinstance(g, int)]
    user = request.user
    if user.is_authenticated and user.email:
        ids += [n for n in GiftCard.objects.filter(email__iexact=user.email, available_balance__gt=0).values_list("id", flat=True)
                if n not in ids and n not in cart["skipped_gifts"]]
    cards = GiftCard.objects.in_bulk(ids)
    for card_id in ids:
        card = cards.get(card_id)
        if card is None or card.frozen or card.available_balance <= 0 or left <= 0:
            continue
        amount = min(card.available_balance, left)
        out.append((card, amount))
        left -= amount
    return out


def _staff_cover(request, priced, left, channel):
    """(StaffCard, amount) a signed-in staff member's balance card pays of what's `left`, or (None, 0)."""
    from gift_cards import staff_cards
    card = next((c for c in staff_cards.cards_for(request.user, channel) if c.type.kind == c.type.BALANCE), None)
    if card is None or priced is None or left <= 0:
        return None, Decimal("0")
    return card, staff_cards.payable(card, priced, left)


def checkout(request):
    state = _state(request)
    t = themes.current(request).values
    pictures = t.get("pictures", "photos")
    pay = None
    if request.method == "POST":
        op = request.POST.get("op", "")
        if op != "place" and too_fast(request, "store-checkout"):  # placing is never skipped
            return redirect("store_cart")
        state = _apply_op(request, _from_post(state, request.POST), op, request.POST)
        request.session["checkout"] = state
        if op == "place":
            result = _place(request, state)
            if isinstance(result, Order):
                return redirect("order_status", id=result.id)
            pay = result  # card still to pay: the payment sheet
        elif op in ("inc", "dec", "remove") or op.startswith(("inc:", "dec:", "remove:", "upsell:", "code", "drop_")):
            return redirect("store_cart")
    delivery = state["method"] == "delivery"
    user = request.user
    saved_addresses = list(user.saved_addresses.all()) if user.is_authenticated else []
    if delivery and saved_addresses and not any(str(a.id) == state["address_id"] for a in saved_addresses):
        if state["address"] and state["address_id"] in ("", "new"):
            state["address_id"] = "new"  # typed before signing in: keep it
        elif state["address_id"] != "new":
            _use_address(state, saved_addresses[0])  # the default one
    priced, problems, when = _priced(request, state)
    for problem in problems:
        messages.error(request, problem)
    rows = store_cart.rows(request, priced, _picture(pictures))
    if priced is None:
        return theme_page(request, "cart.html", {"empty": True, "menus_now": storefront.menus_now(limit=2),
                                                 "orders": _recent(request)}, active="")
    cart_dishes = [p.dish for line in priced.lines for p in line.picks]  # for "Goes well with"
    base = priced.subtotal - priced.discount
    tip = (base * Decimal(state["tip"]) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    total = priced.total + tip
    gifts = _gift_cover(request, total)
    gift_total = sum((a for _c, a in gifts), Decimal("0"))
    staff_card, staff_amount = _staff_cover(request, priced, total - gift_total, "delivery" if delivery else "web")
    lines = [(_("Subtotal"), priced.subtotal, "")]
    for _line, applied in priced.applied_discounts():
        lines.append((applied.label, -applied.amount, "deal"))
    lines += [(f.label, f.amount, "") for f in priced.fees]
    if priced.tax:
        lines.append((_("Tax"), priced.tax, ""))
    lines.append((_("Tip"), tip, ""))
    for card, amount in gifts:
        lines.append((_("Gift card •• %(last4)s") % {"last4": card.last4}, -amount, "deal"))
    if staff_amount > 0:
        lines.append((f"{staff_card.type.name} •• {staff_card.number[-4:]}", -staff_amount, "deal"))
    lines = [(label, ("−" if value < 0 else "") + store_cart.money(abs(value)), kind) for label, value, kind in lines]
    upsell = _upsell(cart_dishes, pictures)
    delivery_open = any(d["open"] for d in _schedule({**state, "when": "later"}, [], True)["days"])
    to_pay = total - gift_total - staff_amount
    saved_cards = list(user.saved_cards.all()) if user.is_authenticated else []
    for card in saved_cards:
        card.key = f"card:{card.id}"
    if not state["pay"]:
        state["pay"] = f"card:{saved_cards[0].id}" if saved_cards else "new"
    request.session["checkout"] = state
    return theme_page(request, "cart.html", {
        "saved_cards": saved_cards, "saved_addresses": saved_addresses,
        "empty": False, "rows": rows, "count": sum(r["qty"] for r in rows), "state": state, "when": when,
        "priced": priced, "tip": tip, "tips": [{"pct": p, "amount": (base * p / 100).quantize(CENT, rounding=ROUND_HALF_UP), "on": state["tip"] == p} for p in TIPS],
        "total": total, "to_pay": to_pay, "lines": lines, "upsell": upsell, "delivery": delivery, "delivery_open": delivery_open,
        "coupon": store_cart.load(request)["coupon"], "gifts": gifts, "pay": pay, "ready": when["ok"] and _contact_ok(request, state)[0],
        "contact_problem": _contact_ok(request, state)[1], "pickup_minutes": storefront.pickup_minutes(),
        "delivery_minutes": storefront.delivery_minutes(),
        "store_line": ", ".join(filter(None, [themes.current(request).store.get("address")])),
        "dine_open": _dine_ok(when["start"]), "has_account": not user.is_authenticated and account_for(state["email"]),
        "signin_url": reverse("store_signin") + "?" + urlencode({"next": reverse("store_cart"), "email": state["email"]}),
    }, active="")


def _priced(request, state):
    """The cart priced for how it's being taken (delivery, take-out or eating here: packaging differs), its
    problems, and when it can be (_schedule). Eating here falls back to take-out while the dining room is closed then."""
    delivery = state["method"] == "delivery"
    channel = "delivery" if delivery else "web"
    to_go = delivery or state["eat"] != "here"
    priced, problems = store_cart.priced(request, channel=channel, to_go=to_go)
    when = _schedule(state, [line.item for line in priced.lines] if priced else [], delivery)
    if not to_go and not _dine_ok(when["start"]):
        state["eat"] = "togo"
        priced, problems = store_cart.priced(request, channel=channel, to_go=True)
    return priced, problems, when


def _contact_ok(request, state):
    if not state["name"].strip():
        return False, _("Add your name so we know whose order it is.")
    if not request.user.is_authenticated and "@" not in state["email"]:
        return False, _("Add your email for the receipt.")
    if state["method"] == "delivery":
        if not state["address"].strip():
            return False, _("Add the address to deliver to.")
        if len("".join(c for c in state["phone"] if c.isdigit())) < 7:
            return False, _("Add a phone number for the driver.")
    return True, ""


def _upsell(cart_dishes, pictures, count=3):
    taken = {d.id for d in cart_dishes}
    out = []
    for f in storefront.best_sellers(count=12, pictures=pictures):
        if f["dish"].id not in taken and not storefront.kit_needed(f["dish"]):
            out.append(f)
        if len(out) == count:
            break
    return out


def _recent(request):
    """The last three orders this visitor placed: as a guest in this browser, or on their account."""
    from django.db.models import Q
    user = request.user
    mine = Q(pk__in=guest_orders.ids(request)[-3:]) | (Q(customer=user) if user.is_authenticated else Q(pk__in=[]))
    return [storefront.order_card(o) for o in Order.objects.filter(mine).order_by("-id")[:3]]


def _place(request, state):
    """Create the order, or return {"amount", "url"} for the card still to pay (the payment sheet)."""
    delivery = state["method"] == "delivery"
    priced, problems, when = _priced(request, state)
    if priced is None:
        messages.error(request, _("Your cart is empty."))
        return None
    ok, problem = _contact_ok(request, state)
    if not when["ok"] or not ok:
        messages.error(request, problem if not ok else when["message"])
        return None
    base = priced.subtotal - priced.discount
    tip = (base * Decimal(state["tip"]) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    total = priced.total + tip
    gifts = _gift_cover(request, total)
    remaining = total - sum((a for _c, a in gifts), Decimal("0"))
    from gift_cards import staff_cards
    cards = staff_cards.cards_for(request.user, "delivery" if delivery else "web")
    if priced.staff_error:  # e.g. a discount card with nothing on the order to cover
        cards = [c for c in cards if c.type.kind == c.type.BALANCE]
    _card, staff_amount = _staff_cover(request, priced, remaining, "delivery" if delivery else "web")
    remaining -= staff_amount
    uuid_ = request.POST.get("transaction_uuid", "")
    if not state.get("pay") and request.user.is_authenticated:
        default = request.user.saved_cards.first()
        state["pay"] = f"card:{default.id}" if default else "new"
    if remaining > 0 and not uuid_ and state.get("pay", "").startswith("card:") and request.user.is_authenticated:
        card = request.user.saved_cards.filter(pk=_int(state["pay"][5:])).first()
        if card is None:
            messages.error(request, _("Pick a card."))
            return None
        try:
            uuid_ = str(_authorize_saved(card, remaining).uuid)
        except Exception:
            messages.error(request, _("That card was declined. Try another one."))
            return None
    if remaining > 0 and not uuid_:
        save = "&save_card=1" if request.user.is_authenticated and state["save_card"] else ""
        return {"amount": remaining, "url": reverse("web-payment-window") + f"?amount={remaining}&confirmation_needed=1{save}"}
    cart = store_cart.load(request)
    payment = {}

    def verify_payment(left):
        if left > 0:
            try:
                transaction = Transaction.consume(uuid_, left)
            except PaymentError as e:
                raise OrderError(str(e))
            payment["authorization"] = transaction.authorization.first()

    phone = "".join(c for c in state["phone"] if c.isdigit())
    user = request.user if request.user.is_authenticated else None
    try:
        with db_transaction.atomic():
            order = create_order(
                lines=store_cart.plain(cart["lines"]), order_discount={"code": cart["coupon"]} if cart["coupon"] else None,
                channel="delivery" if delivery else "web", initial_status=OrderStation.STATUS_PENDING,
                gift_card_payments=[(c, a) for c, a in gifts], verify_payment=verify_payment, tip=tip,
                staff_cards=cards, staff_card_amount=staff_amount,
                special_instructions=state["note"] or None, to_go_order=delivery or state["eat"] != "here", phone=int(phone) if phone else None,
                start_time=when["start"], name=state["name"].strip()[:140], customer=user,
                email=(user.email if user else state["email"]).strip(),
            )
            if payment.get("authorization"):
                authorization = payment["authorization"]
                order.authorization = authorization
                order.save(update_fields=["authorization"])
                order.payments.filter(method=OrderPayment.CARD).update(
                    square_payment_id=authorization.payment_id, card_brand=authorization.card_brand, card_last4=authorization.card_last4)
            if delivery:
                address = ", ".join(filter(None, [state["address"], state["city"], state["postal"]]))
                Delivery.objects.create(order=order, destination=address, address_2=state["unit"], phone=int(phone),
                                        instructions=state["driver_note"])
                if user and state["address_id"] in ("", "new") and state["save_address"]:
                    _save_address(user, state)
    except OrderError as e:
        messages.error(request, str(e))
        return None
    store_cart.clear(request)
    guest_orders.remember(request, order)
    return order


def _authorize_saved(card, amount):
    """Hold `amount` on a saved card (charged when the kitchen accepts, like the card form's payments): a paid
    Transaction with its PaymentAuthorization, ready for Transaction.consume. Test cards ("demo:") skip Square."""
    import uuid as uuid_lib
    from payments import square
    from payments.models import PaymentAuthorization
    transaction = Transaction.objects.create(amount=amount)
    if card.square_card_id.startswith("demo:"):
        payment_id = f"demo:pay-{uuid_lib.uuid4().hex[:12]}"
    else:
        payment_id = square.charge_card(card.square_customer_id, card.square_card_id, amount, key=f"ord-{transaction.uuid}",
                                        note="Online order", autocomplete=False)
    PaymentAuthorization.objects.create(payment_id=payment_id, amount=square.to_cents(amount), currency="CAD",
                                        status=PaymentAuthorization.AUTHORIZED, transaction=transaction,
                                        card_brand=card.brand, card_last4=card.last4)
    transaction.successful = True
    transaction.save(update_fields=["successful"])
    return transaction


# ---------- gift cards ----------

def _default_amount(design):
    return int(design.amounts[min(1, len(design.amounts) - 1)]) if design.amounts else "other"


def _gift_form(request, designs):
    """The gift card form's state: on a POST, what was sent (checked against the design picked); else the defaults.
    Picking a design, an amount or who it's for happens in the browser (themes/neighbourhood/gift-cards.js), so the
    server only reads the form when someone buys."""
    user = request.user
    post = request.POST if request.method == "POST" else {}
    design = next((d for d in designs if str(d.id) == post.get("design", "")), designs[0] if designs else None)
    st = {"amt": None, "other": "", "to": "", "email": "", "from": user.get_full_name() if user.is_authenticated else "",
          "msg": design.message if design else "", "self": False}
    if design is None:
        return st, None
    st["amt"] = _default_amount(design)
    if post:
        for f, size in (("to", 60), ("email", 64), ("from", 60), ("msg", 120)):
            st[f] = post.get(f, "").strip()[:size]
        st["other"] = "".join(c for c in post.get("other", "") if c.isdigit())[:4]
        amt = post.get("amt", "")
        if amt == "other" and design.custom:
            st["amt"] = "other"
        elif amt.isdigit() and int(amt) in [int(a) for a in design.amounts]:
            st["amt"] = int(amt)
        st["self"] = post.get("self") == "1"
        if st["self"] and user.is_authenticated:
            st["email"], st["to"] = st["email"] or user.email, st["to"] or user.get_full_name()
    return st, design


def _gift_sale(st, design):
    amount = st["other"] if st["amt"] == "other" else st["amt"]
    return {"design": design.id if design else None, "amount": str(amount or 0), "to": st["to"], "email": st["email"],
            "message": "" if st["self"] else st["msg"], "from": "" if st["self"] else st["from"]}


def gift_cards_page(request):
    """
    Buy a gift card (the designs sold online: gift_cards.GiftCardDesign): pick a design and amount, who it's for and a
    message, and pay. It's an order of its own (pricing's gift_sales, no food), charged straight away, and the card is
    emailed to the recipient with its number; the buyer sees the card's link once, on the page after paying.
    The page carries every design (json_script) and the browser switches between them; only Buy posts.
    """
    from gift_cards.giftcards import email_sold
    from gift_cards.models import GiftCardDesign
    designs = GiftCardDesign.on_sale("online")
    st, design = _gift_form(request, designs)
    pay, done = None, None
    if request.method == "POST" and design and request.POST.get("op") == "place":
        result = _buy_gift(request, st, design)
        if isinstance(result, dict) and "amount" in result:
            pay = result
        elif result:
            order, card, number = result
            sent = email_sold(request, card, number, sender=_gift_sale(st, design)["from"])
            if not st["self"] and request.user.is_authenticated:
                from gift_cards.giftcards import email_receipt
                email_receipt(card, request.user)
            done = {"card": card, "link": reverse("card_display", args=[number]), "sent": sent, "order": order}
    amount = st["other"] if st["amt"] == "other" else st["amt"]
    user = request.user
    return theme_page(request, "gift_cards.html", {
        "designs": designs, "design": design, "st": st, "amount": amount, "pay": pay, "done": done,
        "can_buy": bool(design and amount and st["email"]), "signed_in": user.is_authenticated,
        "designs_data": [{"id": d.id, "name": d.name, "color": d.color, "ink": d.ink, "image": d.image.url if d.image else "",
                          "showText": d.show_text, "amounts": [int(a) for a in d.amounts], "custom": d.custom,
                          "min": d.min_amount, "max": d.max_amount, "message": d.message} for d in designs],
        "buyer": {"name": user.get_full_name(), "email": user.email} if user.is_authenticated else None,
    }, active="giftcards")


def _buy_gift(request, st, design):
    """Pay for the card: the order with its card and number, {"amount", "url"} for the card form, or None (a message says why)."""
    sale = _gift_sale(st, design)
    try:
        priced = price_cart([], channel="web", gift_sales=[sale])
    except OrderError as e:
        messages.error(request, str(e))
        return None
    total = priced.total
    uuid_ = request.POST.get("transaction_uuid", "")
    user = request.user if request.user.is_authenticated else None
    if not uuid_ and user and user.saved_cards.exists():
        card = user.saved_cards.first()
        try:
            uuid_ = str(_authorize_saved(card, total).uuid)
        except Exception:
            messages.error(request, _("That card was declined. Try another one."))
            return None
    if not uuid_:
        return {"amount": total, "url": reverse("web-payment-window") + f"?amount={total}"}  # charged straight away
    payment = {}

    def verify_payment(left):
        if left > 0:
            try:
                transaction = Transaction.consume(uuid_, left)
            except PaymentError as e:
                raise OrderError(str(e))
            payment["authorization"] = transaction.authorization.first()

    try:
        with db_transaction.atomic():
            order = create_order(lines=[], channel="web", initial_status=OrderStation.STATUS_APPROVED, gift_sales=[sale],
                                 verify_payment=verify_payment, name=(st["from"] or st["to"] or "")[:140], customer=user,
                                 email=(user.email if user else st["email"]).strip())
            authorization = payment.get("authorization")
            if authorization:
                order.authorization = authorization
                order.save(update_fields=["authorization"])
                order.payments.filter(method=OrderPayment.CARD).update(
                    square_payment_id=authorization.payment_id, card_brand=authorization.card_brand, card_last4=authorization.card_last4)
    except OrderError as e:
        messages.error(request, str(e))
        return None
    if payment.get("authorization"):  # nothing waits on a kitchen: take the money now
        _capture_now(payment["authorization"])
    card, number = order.new_gift_cards[0]
    guest_orders.remember(request, order)
    return order, card, number


def _capture_now(authorization):
    from payments import square
    from payments.models import PaymentAuthorization
    if authorization.payment_id.startswith("demo:"):
        authorization.status = PaymentAuthorization.CAPTURED
        authorization.save(update_fields=["status"])
        return
    try:
        square.capture_authorization(authorization)
    except Exception:
        logger.exception("Capturing gift card payment %s failed", authorization.payment_id)


# ---------- the placed order ----------

def order_page(request, id, from_placing=None):
    """A placed order, for whoever placed it (online_store/guest_orders.py): anyone else gets a page saying it isn't theirs."""
    from .models import RejectedOrder
    order = Order.objects.filter(pk=id).prefetch_related("items", "fee_lines").first()
    if order is None:
        rejected = RejectedOrder.objects.filter(order_id=id).first()
        if rejected is None:
            raise Http404
        if not guest_orders.can_see(request, id, rejected.customer_id):
            return _not_yours(request, id)
        return theme_page(request, "order.html", {"rejected": rejected, "order_id": id})
    if not guest_orders.can_see(request, order.id, order.customer_id):
        return _not_yours(request, id)
    return theme_page(request, "order.html", {"o": storefront.order_card(order, full=True)})


def _not_yours(request, id):
    response = theme_page(request, "order.html", {"not_yours": True, "order_id": id,
                                                  "signin": reverse("store_signin") + "?" + urlencode({"next": request.get_full_path()})})
    response.status_code = 403
    return response


# ---------- events and services ----------

SERVICE_ICONS = [  # words in a service's title -> icon
    (("class", "workshop", "lesson"), "M4 14h16 M6 14a6 6 0 0 1 12 0 M3 18h18 M12 5v3"),
    (("coffee", "tasting", "barista"), "M5 9h11v6a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z M16 11h2a2 2 0 0 1 0 4h-2 M8 3v3 M12 3v3"),
    (("bartender", "bar"), "M7 3h10l-1 8a4 4 0 0 1-8 0z M12 15v6 M8 21h8"),
    (("chef", "cook"), "M6 13a4 4 0 1 1 2-7.5A4 4 0 0 1 16 5.5 4 4 0 1 1 18 13v6H6z M6 16h12"),
    (("chafing", "dish", "warmer"), "M4 10h16v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z M2 10h20 M9 6h6 M12 6V4"),
    (("urn",), "M7 4h10v14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2z M17 8h2v4h-2 M10 14h4"),
    (("grill", "flat-top", "bbq"), "M4 9h16a8 8 0 0 1-16 0z M8 17l-2 4 M16 17l2 4 M8 5c0-1 1-1 1-2 M12 5c0-1 1-1 1-2 M16 5c0-1 1-1 1-2"),
    (("table", "chair"), "M3 9h18 M6 9v11 M18 9v11 M3 9l3-4h12l3 4"),
    (("cake", "bakery"), "M4 21h16 M5 21v-7h14v7 M5 16c2 1 4 1 7 0s5-1 7 0 M12 14V9 M12 7a1.5 1.5 0 0 0 0-3"),
    (("menu", "print", "card"), "M6 3h12v18H6z M9 8h6 M9 12h6 M9 16h4"),
    (("delivery", "setup", "teardown"), "M3 7h11v9H3z M14 10h4l3 3v3h-7 M7 19a2 2 0 1 0 0-.01 M17 19a2 2 0 1 0 0-.01"),
    (("server", "staff", "host"), "M12 8a3.5 3.5 0 1 0 0-.01 M5 20c0-4 3-6.5 7-6.5s7 2.5 7 6.5"),
]
GROUP_NOTES = {"experience": gettext_lazy("Small groups · add food for after"), "staff": gettext_lazy("Serving, cooking and pouring at your event"),
               "equipment": gettext_lazy("Delivered with your food, picked up after"), "extra": gettext_lazy("Small things that make it feel planned")}


def _service_icon(service):
    title = service.title.lower()
    return next((path for words, path in SERVICE_ICONS if any(w in title for w in words)), storefront.SERVICE_ICONS.get(service.category, storefront.SERVICE_ICONS["extra"]))


def _service_sub(service):
    from services.models import Service
    if service.category == Service.EQUIPMENT and service.deposit and service.deposit_amount:
        each = _("%(d)s deposit per booking") if service.deposit_per == Service.PER_BOOKING else _("%(d)s deposit each")
        return each % {"d": storefront.money(service.deposit_amount).replace(".00", "")}
    if service.category == Service.EQUIPMENT:
        return _("No deposit")
    if service.minimum and service.pricing != Service.FLAT:
        return service.amount_text(service.minimum) + " " + _("minimum")
    if service.category == Service.EXPERIENCE and service.detail("minutes"):
        return _("%(n)d minutes") % {"n": int(service.detail("minutes"))}
    if service.needs_approval:
        return _("We confirm availability")
    return ""


def services_page(request):
    from django.db.models import Count
    from services.models import Service
    services = list(Service.objects.filter(online=True, active=True, archived=False)
                    .annotate(booked=Count("bookings")).order_by("-booked", "title"))
    venues = [s for s in services if s.category == Service.VENUE][:2]
    groups = []
    for key, title in ((Service.EXPERIENCE, _("Experiences")), (Service.STAFF, _("Staff")), (Service.EQUIPMENT, _("Equipment")), ("extra", _("Extras"))):
        items = [s for s in services if s.category == key or (key == "extra" and s.category in (Service.EXTRA, Service.DELIVERY))]
        if not items:
            continue
        top = max(items, key=lambda s: s.booked)
        groups.append({"key": key, "title": title, "note": GROUP_NOTES.get(key, ""), "cols": 4 if len(items) % 4 == 0 else 3,
                       "items": [{"s": s, "icon": _service_icon(s), "price": storefront.service_price(s), "sub": _service_sub(s),
                                  "tag": _("On request") if s.needs_approval else _("Popular") if s is top and s.booked else "",
                                  "first": i == 0 and not groups} for i, s in enumerate(items)]})
    venue_cards = []
    for n, v in enumerate(venues):
        facts = []
        if v.detail("seated"):
            facts.append(_("Seats %(s)d, or %(t)d standing") % {"s": int(v.detail("seated")), "t": int(v.detail("standing") or v.detail("seated"))})
        facts += [str(x) for x in (v.includes or [])[:2]]
        if v.minimum and v.pricing != Service.FLAT:
            facts.append(v.amount_text(v.minimum) + " " + _("minimum"))
        if v.needs_approval:
            facts.append(_("We confirm availability first"))
        venue_cards.append({"s": v, "facts": facts[:4], "price": storefront.service_price(v),
                            "tag": _("On request") if v.needs_approval else _("Most booked") if n == 0 and v.booked else ""})
    jumps = ([("space", _("The space"), len(venue_cards))] if venue_cards else []) + [(g["key"], g["title"], len(g["items"])) for g in groups]
    return theme_page(request, "services.html", {"venues": venue_cards, "groups": groups, "jumps": jumps}, active="services")
