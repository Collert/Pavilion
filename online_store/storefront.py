"""
What the online store's pages show, worked out once for any theme: opening hours (events/hours.py), the menus on now and coming up, best sellers, the catering and services teasers, the signed-in
customer's order in progress, and a drawing for dishes without a photo. Themes get these through
online_store/views.py and decide how they look.
"""
import datetime
from decimal import Decimal

from django.db.models import Avg, Sum
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from events.hours import Hours, ranges_text, week_ranges
from pos_server import dish_info
from pos_server.menu_schedule import Schedule, days_text, minute_of_week
from pos_server.models import Dish, Menu, Order, OrderItem

DAY_NAMES = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
# Drawings for dishes without a photo, picked from words in the title
DRAWINGS = [("burger", ("burger", "smash", "melt", "patty")), ("sandwich", ("sandwich", "wrap", "sub")),
            ("bowl", ("bowl", "salad", "plate", "pork", "corn")), ("fries", ("fries", "chips", "bites", "rings")),
            ("latte", ("latte", "coffee", "brew", "americano", "matcha", "chai", "white", "espresso")),
            ("lemonade", ("lemonade", "spritz", "soda", "juice", "tea")),
            ("croissant", ("croissant", "bun", "loaf", "cookie", "brownie", "bread")), ("tart", ("tart", "cake", "pie"))]


def money(value) -> str:
    value = Decimal(value or 0)
    return f"${value:,.2f}"


def clock(t, short=False) -> str:
    """7 AM, 3:30 PM; 11:59 PM reads as 12 AM (open till midnight). short: 7, 3:30."""
    if t.hour == 23 and t.minute == 59:
        t = datetime.time(0, 0)
    hour = t.hour % 12 or 12
    text = f"{hour}:{t.minute:02d}" if t.minute else str(hour)
    return text if short else f"{text} {'AM' if t.hour < 12 else 'PM'}"


def drawing(title) -> str:
    title = title.lower()
    return next((kind for kind, words in DRAWINGS if any(w in title for w in words)), "plate")


def picture(dish, pictures="photos") -> dict:
    """{"photo": url or "", "drawing": kind}: the dish's photo unless the theme draws every dish."""
    photo = dish.image.url if pictures == "photos" and getattr(dish, "image", None) else ""
    return {"photo": photo, "drawing": drawing(dish.title)}


def link(name, *args) -> str:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


# ---------- hours ----------

def week_hours(today=None, hours=None) -> list[dict]:
    """The next seven days, Monday first, events included: [{"day", "time", "today", "note": the day's events}]."""
    today = today or timezone.localdate()
    hours = hours or Hours()
    days = sorted((today + datetime.timedelta(days=n) for n in range(7)), key=lambda d: d.weekday())
    out = []
    for d in days:
        out.append({"day": DAY_NAMES[d.weekday()], "time": ranges_text(hours.ranges(d)) or _("Closed"), "today": d == today,
                    "note": ", ".join(e.title for e in hours.events_on(d))})
    return out


def regular_hours(hours=None) -> list[dict]:
    """The usual week, Monday first: [{"day", "time"}]."""
    hours = hours or Hours()
    return [{"day": DAY_NAMES[i], "time": ranges_text(week_ranges(hours.cfg.week, i)) or _("Closed")} for i in range(7)]


def open_now(now=None, hours=None) -> dict:
    """{"open": bool, "text": "Open now · till 9 PM today", "short": "7–9", "note": today's events}"""
    now = timezone.localtime(now or timezone.now())
    hours = hours or Hours()
    today = hours.ranges(now.date())
    short = f"{clock_minutes(today[0][0], True)}–{clock_minutes(today[-1][1], True)}" if today else ""
    out = {"short": short, "note": ", ".join(e.title for e in hours.events_on(now.date()))}
    paused = hours.paused_until(now)
    if paused:
        return {**out, "open": False, "paused": True,
                "text": _("Back soon · online orders resume at %(time)s") % {"time": clock(timezone.localtime(paused).time())}}
    current = hours.current(now)
    if current:
        end = timezone.localtime(current[1])
        if end.time() == datetime.time.min:
            return {**out, "open": True, "text": _("Open now · till midnight")}
        if end.date() != now.date():
            return {**out, "open": True, "text": _("Open now · till %(time)s") % {"time": clock(end.time())}}
        return {**out, "open": True, "text": _("Open now · till %(time)s today") % {"time": clock(end.time())}}
    nxt = hours.next_start(now)
    if nxt:
        nxt = timezone.localtime(nxt)
        if nxt.date() == now.date():
            return {**out, "open": False, "text": _("Closed now · opens at %(time)s") % {"time": clock(nxt.time())}}
        if nxt.date() == now.date() + datetime.timedelta(days=1):
            return {**out, "open": False, "text": _("Closed now · opens tomorrow at %(time)s") % {"time": clock(nxt.time())}}
    return {**out, "open": False, "text": _("Closed today")}


def event_banner(today=None, hours=None) -> dict | None:
    """The soonest event with a banner that's due to show (from its banner_days before until it ends):
    {"kind": "open"|"closed", "head": "Trivia night · Tue, Nov 3, 7–10 PM.", "text": its description}."""
    from events.models import Event
    today = today or timezone.localdate()
    hours = hours or Hours()
    due = [e for e in hours.events if e.banner and e.end_date >= today and e.start_date - datetime.timedelta(days=e.banner_days) <= today]
    if not due:
        return None
    e = min(due, key=lambda e: (max(e.start_date, today), e.opens))
    day = max(e.start_date, today)
    if e.kind == Event.OPEN:
        head = e.title
    else:
        head = closed_label(e)
    when = "" if e.kind == Event.CLOSED and e.all_day else f", {clock_minutes(e.opens)}–{clock_minutes(e.closes)}"
    day_text = _("today") if day == today else _("tomorrow") if day == today + datetime.timedelta(days=1) else f"{day:%a, %b} {day.day}"
    return {"kind": e.kind, "head": f"{head} · {day_text}{when}.", "text": e.description or ""}


def closed_label(e) -> str:
    """Closed, or what a closure stops: "No delivery", "No pickup or delivery"."""
    if e.closes_everything():
        return _("Closed")
    names = [str(label) for on, label in ((e.dine_in, _("dine-in")), (e.pickup, _("pickup")), (e.delivery, _("delivery"))) if on]
    return _("No %(what)s") % {"what": _(" or ").join(names)} if names else _("Closed")


def clock_minutes(minutes, short=False) -> str:
    m = int(minutes) % 1440
    return clock(datetime.time(m // 60, m % 60), short)


# ---------- menus ----------

def _segment_text(segment):
    a, b = segment
    start, end = datetime.time((a % 1440) // 60, a % 60), datetime.time((b % 1440) // 60, b % 60)
    return f"{clock(start)} – {clock(end)}"


def menus_now(limit=3, now=None) -> list[dict]:
    """Live menus, then the next ones to start: [{"menu", "when", "pill", "state": now|soon|later}]."""
    now = timezone.localtime(now or timezone.now())
    menus = [m for m in Menu.objects.filter(draft=False).order_by("position", "id") if m.dishes.exists()]
    schedule = Schedule(menus)
    minute = minute_of_week(now)
    out = []
    for menu in menus:
        segments = schedule.segments(menu) if menu.auto else []
        if segments:
            days = sorted({a // 1440 for a, _b in segments})
            when = f"{days_text(days)}, {_segment_text(segments[0])}"
        else:
            when = _("All day")
        if schedule.is_live(menu, now):
            out.append({"menu": menu, "when": when, "pill": _("Serving now"), "state": "now", "order": -1})
            continue
        starts = sorted((a - minute) % (7 * 1440) for a, _b in segments)
        if not starts:
            continue
        wait = starts[0]
        at = now + datetime.timedelta(minutes=wait)
        if at.date() == now.date():
            pill, state = _("Starts at %(time)s") % {"time": clock(at.time())}, "soon"
        elif at.date() == now.date() + datetime.timedelta(days=1):
            pill, state = _("Tomorrow, %(time)s") % {"time": clock(at.time())}, "later"
        else:
            pill, state = f"{DAY_NAMES[at.weekday()]}, {clock(at.time())}", "later"
        out.append({"menu": menu, "when": when, "pill": pill, "state": state, "order": wait})
    out.sort(key=lambda m: m["order"])
    return out[:limit]


def best_sellers(count=4, days=7, pictures="photos") -> list[dict]:
    """The dishes sold most over `days` that are on a live menu now: [{"dish", "price", "tag", "picture", "url"}]."""
    live = Menu.live_menus()
    dishes = Dish.objects.filter(menu__in=live, archived=False, visible_in_menu=True).distinct()
    since = timezone.now() - datetime.timedelta(days=days)
    sold = dict(OrderItem.objects.filter(order__timestamp__gte=since, dish__in=dishes)
                .values_list("dish").annotate(n=Sum("quantity")).values_list("dish", "n"))
    ranked = sorted(dishes, key=lambda d: (-sold.get(d.id, 0), d.id))[:count]
    out = []
    for n, dish in enumerate(ranked):
        diets = {d["key"] for d in dish_info.dish_diets(dish)}
        tag = _("Most loved") if n == 0 and sold.get(dish.id) else ""
        if not tag and "vegan" in diets:
            tag = _("Vegan")
        elif not tag and "vegetarian" in diets:
            tag = _("Vegetarian")
        out.append({"dish": dish, "price": money(dish.price), "tag": tag, "picture": picture(dish, pictures),
                    "url": link("dish", dish.id)})
    return out


def pickup_minutes() -> int:
    """About how long pickup takes: the last two weeks' average prep time, to the 5 minutes (20 without history)."""
    since = timezone.now() - datetime.timedelta(days=14)
    avg = Order.objects.filter(timestamp__gte=since, prep_time__isnull=False).aggregate(a=Avg("prep_time"))["a"]
    if not avg:
        return 20
    return max(5, int(round(avg.total_seconds() / 60 / 5) * 5))


# ---------- gift cards ----------

def gift_designs() -> list:
    """The gift card designs sold online right now (their season included)."""
    from gift_cards.models import GiftCardDesign
    return GiftCardDesign.on_sale("online")


def gift_teaser(designs=None) -> dict | None:
    """The home page's gift cards section: up to three designs and the smallest amount, or None when none are sold."""
    designs = gift_designs() if designs is None else designs
    if not designs:
        return None
    amounts = [int(a) for d in designs for a in (d.amounts or [])] + [d.min_amount for d in designs if d.custom]
    return {"designs": designs[:3], "from": min(amounts) if amounts else None}


# ---------- catering and services ----------

def catering_teaser() -> dict | None:
    from catering import pricing as cpricing
    from catering.models import CateringOrder, CateringSettings
    cfg = CateringSettings.load()
    if not cfg.on:
        return None
    lead = ngettext("Order at least %(n)d day ahead", "Order at least %(n)d days ahead", cfg.lead) % {"n": cfg.lead} \
        if cfg.lead_unit == cfg.DAYS else ngettext("Order at least %(n)d hour ahead", "Order at least %(n)d hours ahead", cfg.lead) % {"n": cfg.lead}
    ways = [label for on, label in ((cfg.pickup, _("pickup")), (cfg.delivery, _("delivery"))) if on]
    rules = [lead]
    if cfg.min_subtotal:
        rules.append(_("Quotes from %(amount)s before tax") % {"amount": money(cfg.min_subtotal).replace(".00", "")})
    if ways:
        rules.append((", ".join(ways[:-1]) + _(" or ") + ways[-1] if len(ways) > 1 else ways[0]).capitalize())
    rules.append(_("Your card is held, not charged, until we confirm"))
    # An example quote: four catering dishes for 20
    lines, total = [], Decimal("0")
    for dish in Dish.objects.filter(catering=True, archived=False).prefetch_related("catering_prices").order_by("id")[:4]:
        each = dish.catering_price(20)
        lines.append({"name": dish.title, "qty": "× 20", "price": money(each * 20)})
        total += each * 20
    if not lines:
        return None
    total = (total * (1 + cpricing.tax_rate())).quantize(Decimal("0.01"))
    return {"rules": rules, "quote": lines, "total": money(total), "settings": cfg}


SERVICE_ICONS = {
    "venue": "M3 21h18 M5 21V8l7-4 7 4v13 M9 21v-6h6v6",
    "experience": "M4 14h16 M6 14a6 6 0 0 1 12 0 M3 18h18",
    "staff": "M8 7a3 3 0 1 0 0 .01 M16 7a3 3 0 1 0 0 .01 M3 20c0-3 2.5-5 5-5s5 2 5 5 M11 20c0-3 2.5-5 5-5s5 2 5 5",
    "equipment": "M4 7h16v4H4z M6 11v9 M18 11v9 M4 7l3-3h10l3 3",
    "delivery": "M3 7h11v9H3z M14 10h4l3 3v3h-7z M7 19a2 2 0 1 0 0-.01 M17 19a2 2 0 1 0 0-.01",
    "extra": "M12 3l2.7 5.5 6 .9-4.4 4.2 1 6-5.3-2.8-5.3 2.8 1-6L3.3 9.4l6-.9z",
}


def service_price(service) -> str:
    from services.models import Service
    each = {Service.HOUR: _("%(p)s an hour"), Service.DAY: _("%(p)s a day"), Service.PERSON: _("%(p)s per person"),
            Service.UNIT: _("%(p)s each"), Service.FLAT: _("%(p)s")}[service.pricing] % {"p": money(service.price).replace(".00", "")}
    return _("From %(price)s") % {"price": each} if service.pricing != Service.PERSON else each


def services_teaser(count=3) -> list[dict]:
    """A venue, a class and staff first (what people book most), then anything else bookable online."""
    from services.models import Service
    services = list(Service.objects.filter(online=True, active=True, archived=False).order_by("title"))
    order = [Service.VENUE, Service.EXPERIENCE, Service.STAFF, Service.EQUIPMENT, Service.DELIVERY, Service.EXTRA]
    picked, seen = [], set()
    for category in order:
        for s in services:
            if s.category == category and category not in seen:
                picked.append(s)
                seen.add(category)
                break
    return [{"service": s, "price": service_price(s), "icon": SERVICE_ICONS.get(s.category, SERVICE_ICONS["extra"])} for s in picked[:count]]


# ---------- the customer ----------

def live_order(user, ids=()) -> dict | None:
    """The order being made today for this visitor: the signed-in customer's, or one this browser placed as a guest
    (`ids`: the session's "orders", kept when an order is placed). {"order", "label", "ready", "done", "url"}."""
    from django.db.models import Q
    signed_in = bool(user and user.is_authenticated)
    if not signed_in and not ids:
        return None
    mine = (Q(customer=user) if signed_in else Q(pk__in=[])) | Q(pk__in=list(ids))
    today = timezone.localdate()
    order = Order.objects.filter(mine, picked_up=False, voided_at__isnull=True, timestamp__date=today).order_by("-timestamp").first()
    if order is None:
        return None
    ready = order.all_stations_done()
    eta = timezone.localtime(order.start_time + datetime.timedelta(minutes=pickup_minutes()))
    return {"order": order, "label": _("Ready for pickup") if ready else _("Cooking · #%(n)s") % {"n": order.id},
            "ready": _("Ready now") if ready else _("Ready %(time)s") % {"time": clock(eta.time())}, "done": ready,
            "url": link("order_status", order.id)}


def cart_count(request) -> int:
    cart = request.session.get("cart") or {}
    return sum(int(line.get("quantity", 1) or 1) for line in cart.get("lines", []) if isinstance(line, dict))


def initials(user) -> str:
    name = (user.get_full_name() or user.username).split()
    return "".join(w[0] for w in name[:2]).upper()


def hours_summary(week) -> list[dict]:
    """week_hours() with neighbouring days that keep the same hours together: [{"day": "Mon–Fri", "time"}]."""
    out = []
    for h in week:
        short = h["day"][:3]
        if out and out[-1]["time"] == h["time"]:
            out[-1]["last"] = short
        else:
            out.append({"first": short, "last": "", "time": h["time"]})
    return [{"day": f"{g['first']}–{g['last']}" if g["last"] else g["first"], "time": g["time"]} for g in out]


def kit_needed(dish) -> bool:
    """Whether the dish has a required choice with nothing picked by default (it opens its sheet to be added)."""
    from pos_server.models import OptionGroup
    groups = list(dish.option_groups.all())
    for dc in dish.dishcomponent_set.select_related("component").all():
        groups += list(dc.component.option_groups.all())
    return any(g.required and not (g.pick == OptionGroup.ONE and g.default_option()) for g in groups)


def order_card(order, full=False) -> dict:
    """An order as the store shows it: number, state, the steps it goes through, when it's ready, its receipt."""
    delivery = order.channel == "delivery"
    progress = order.progress_status() or 2
    started = timezone.localtime(order.start_time)
    eta = started + datetime.timedelta(minutes=pickup_minutes() + (20 if delivery else 0))
    scheduled = order.start_time > order.timestamp + datetime.timedelta(minutes=10)
    steps = [(_("Received"), clock(timezone.localtime(order.timestamp).time())),
             (_("Cooking"), _("We’re on it") if progress >= 3 else ""),
             ((_("On the way") if delivery else _("Ready")), "" if progress < 4 else _("On the shelf") if not delivery else ""),
             ((_("Delivered") if delivery else _("Picked up")), "")]
    done = {1: 1, 2: 1, 3: 1, 4: 3 if not delivery else 2, 5: 4, 6: 4}.get(progress, 1)
    current = min(done, 3) if progress < 5 else 4
    out = {"order": order, "number": order.id, "delivery": delivery, "progress": progress, "scheduled": scheduled,
           "placed": clock(timezone.localtime(order.timestamp).time()), "eta": clock(eta.time()) if not scheduled else clock(started.time()),
           "eta_day": "" if started.date() == timezone.localdate() else f"{started:%a, %b} {started.day}",
           "steps": [{"n": i + 1, "label": label, "sub": sub, "done": i < done, "now": i == current and progress < 5} for i, (label, sub) in enumerate(steps)],
           "label": _("Ready for pickup") if progress == 4 and not delivery else _("Done") if progress >= 5 else _("Scheduled") if progress == 1 else _("Cooking"),
           "url": link("order_status", order.id)}
    if full:
        items = [i for i in order.items.all() if i.parent_id is None]
        out["items"] = [{"qty": i.quantity, "name": i.title, "mods": ", ".join(i.mods or []) or "", "total": i.line_total} for i in items]
        totals = [(_("Subtotal"), order.subtotal)]
        if order.discount:
            totals.append((_("Discount"), -order.discount))
        totals += [(f.label, f.amount) for f in order.fee_lines.all()]
        if order.tax:
            totals.append((_("Tax"), order.tax))
        if order.tip:
            totals.append((_("Tip"), order.tip))
        totals += [(_("Gift card •• %(last4)s") % {"last4": c.last4}, c.original) for c in order.gift_cards_sold.all()]
        out["totals"] = [(label, ("−" if value < 0 else "") + money(abs(value))) for label, value in totals]
        dest = order.delivery.first() if delivery else None
        out["where"] = dest.destination if dest else ""
        out["where_note"] = (dest.instructions or "") if dest else ""
    return out
