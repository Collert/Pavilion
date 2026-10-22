"""
What the online store's pages show, worked out once for any theme: opening hours (from events.Event until the Hours
page exists), the menus on now and coming up, best sellers, the catering and services teasers, the signed-in
customer's order in progress, and a drawing for dishes without a photo. Themes get these through
online_store/views.py and decide how they look.
"""
import datetime
from decimal import Decimal

from django.db.models import Avg, Sum
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from events.models import Event
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

def _events(start, days):
    end = start + datetime.timedelta(days=days)
    tz = timezone.get_current_timezone()
    since = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min), tz)
    until = timezone.make_aware(datetime.datetime.combine(end, datetime.time.min), tz)
    by_day = {}
    for event in Event.objects.filter(restaurant_open=True, end__gt=since, start__lt=until).order_by("start"):
        day = timezone.localtime(event.start).date()
        by_day.setdefault(day, []).append(event)
    return by_day


def hours_text(events) -> str:
    if not events:
        return _("Closed")
    first, last = timezone.localtime(events[0].start).time(), timezone.localtime(max(e.end for e in events)).time()
    return f"{clock(first)} – {clock(last)}"


def week_hours(today=None) -> list[dict]:
    """The next seven days, Monday first: [{"day", "time", "today"}]."""
    today = today or timezone.localdate()
    by_day = _events(today, 7)
    days = [today + datetime.timedelta(days=n) for n in range(7)]
    days.sort(key=lambda d: d.weekday())
    return [{"day": DAY_NAMES[d.weekday()], "time": hours_text(by_day.get(d, [])), "today": d == today} for d in days]


def open_now(now=None) -> dict:
    """{"open": bool, "text": "Open now · till 9 PM today", "short": "7–9"}"""
    now = timezone.localtime(now or timezone.now())
    by_day = _events(now.date(), 2)
    today = by_day.get(now.date(), [])
    short = ""
    if today:
        start, end = timezone.localtime(today[0].start).time(), timezone.localtime(max(e.end for e in today)).time()
        short = f"{clock(start, True)}–{clock(end, True)}"
    current = next((e for e in today if e.start <= now < e.end), None)
    if current:
        end = timezone.localtime(current.end).time()
        if end >= datetime.time(23, 59):
            return {"open": True, "short": short, "text": _("Open now · till midnight")}
        return {"open": True, "short": short, "text": _("Open now · till %(time)s today") % {"time": clock(end)}}
    later = next((e for e in today if e.start > now), None)
    if later:
        return {"open": False, "short": short, "text": _("Closed now · opens at %(time)s") % {"time": clock(timezone.localtime(later.start).time())}}
    tomorrow = by_day.get(now.date() + datetime.timedelta(days=1), [])
    if tomorrow:
        return {"open": False, "short": short, "text": _("Closed now · opens tomorrow at %(time)s") % {"time": clock(timezone.localtime(tomorrow[0].start).time())}}
    return {"open": False, "short": short, "text": _("Closed today")}


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


# ---------- catering and services ----------

def catering_teaser() -> dict | None:
    from catering import pricing as cpricing
    from catering.models import CateringOrder, CateringSettings
    cfg = CateringSettings.load()
    if not cfg.on:
        return None
    lead = ngettext("Order at least %(n)d day ahead", "Order at least %(n)d days ahead", cfg.lead) % {"n": cfg.lead} \
        if cfg.lead_unit == cfg.DAYS else ngettext("Order at least %(n)d hour ahead", "Order at least %(n)d hours ahead", cfg.lead) % {"n": cfg.lead}
    ways = [label for on, label in ((cfg.pickup, _("pickup")), (cfg.delivery, _("delivery")), (cfg.full_service, _("full service with staff"))) if on]
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

def live_order(user) -> dict | None:
    """The signed-in customer's order being made today: {"order", "label", "ready"}."""
    if not user or not user.is_authenticated:
        return None
    today = timezone.localdate()
    order = Order.objects.filter(customer=user, picked_up=False, voided_at__isnull=True, timestamp__date=today).order_by("-timestamp").first()
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
