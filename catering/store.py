"""
The online store's catering quote (design: StoreCatering): what the customer is building, kept in the session
until it's sent, checked against CateringSettings (notice, days and hours, minimums, how they get it), and turned
into a CateringOrder with the card on file when it's sent (catering.flow.place does the rest).

    request.session["catering_quote"] = {"title", "date": "2026-11-13", "time": "17:00", "ful": "delivery",
        "address", "dishes": {"<dish id>": qty}, "services": {"<service id>": {"q": people/units, "h": hours/days}},
        "note", "name", "org", "email", "phone", "cat": "all"}
"""
import datetime
import math
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from pos_server.models import Dish
from services.models import Service

from . import pricing
from .models import CateringLine, CateringOrder, CateringSettings

KEY = "catering_quote"
FULS = {CateringOrder.PICKUP: "pickup", CateringOrder.DELIVERY: "delivery", CateringOrder.FULL: "full_service"}


def load(request) -> dict:
    q = request.session.get(KEY) or {}
    user = request.user
    defaults = {"title": "", "date": "", "time": "", "ful": "", "address": "", "dishes": {}, "services": {}, "note": "",
                "name": user.get_full_name() if user.is_authenticated else "", "org": "",
                "email": user.email if user.is_authenticated else "", "phone": "", "cat": "all", "agree": False, "month": ""}
    return {**defaults, **{k: v for k, v in q.items() if k in defaults}}


def save(request, q):
    request.session[KEY] = q
    request.session.modified = True


def clear(request):
    request.session.pop(KEY, None)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def dishes():
    return list(Dish.objects.filter(catering=True, archived=False).select_related("station").prefetch_related("catering_prices")
                .order_by("station__position", "title"))


def services(q=None):
    """Services offered on catering requests, plus any already on this quote (added from the services page)."""
    on_quote = [int(k) for k in (q or {}).get("services", {}) if str(k).isdigit()]
    return list(Service.objects.filter(Q(catering=True) | Q(pk__in=on_quote), active=True, archived=False).order_by("title"))


def delivery_service():
    """The flat delivery fee added to delivered and full-service orders."""
    return Service.objects.filter(catering=True, active=True, archived=False, category=Service.DELIVERY,
                                  pricing=Service.FLAT).order_by("price").first()


def staffing(service) -> bool:
    return service.category == Service.STAFF


def lines(q) -> list[dict]:
    """The quote as catering.pricing line dicts."""
    out = []
    by_id = {str(d.id): d for d in Dish.objects.filter(pk__in=[_int(k) for k in q["dishes"]]).prefetch_related("catering_prices")}
    for key, qty in q["dishes"].items():
        dish = by_id.get(str(key))
        if dish and qty > 0:
            out.append({"id": "", "kind": CateringLine.DISH, "ref": dish.id, "quantity": qty, "amount": 0, "price": str(dish.catering_price(qty))})
    svc = {str(s.id): s for s in Service.objects.filter(pk__in=[_int(k) for k in q["services"]])}
    for key, x in q["services"].items():
        s = svc.get(str(key))
        if s:
            timed = s.pricing in (Service.HOUR, Service.DAY)
            out.append({"id": "", "kind": CateringLine.SERVICE, "ref": s.id, "quantity": max(1, x.get("q", 1)),
                        "amount": max(1, x.get("h", 1)) if timed else 0, "price": str(s.price)})
    return out


def apply_op(q, op, post):
    """add/inc/dec:<dish>, sadd/sinc/sdec/hinc/hdec:<service>, ful:<way>, date:<iso>, hup/hdown/mup/mdown/am/pm, cat:<x>."""
    name, _sep, arg = op.partition(":")
    for key in ("title", "address", "note", "name", "org", "email", "phone"):
        if key in post:
            q[key] = post.get(key, "").strip()[:600 if key == "note" else 200]
    if "hour" in post and "minute" in post:
        h, m, ap = _int(post.get("hour"), 12), _int(post.get("minute")), post.get("ampm", "PM")
        if 1 <= h <= 12 and 0 <= m <= 59:
            q["time"] = f"{(h % 12) + (12 if ap == 'PM' else 0):02d}:{m:02d}"
    for key, value in post.items():  # typed quantities
        if key.startswith("qty-"):
            dish_id, n = key[4:], _int(value, -1)
            if n >= 0 and dish_id in q["dishes"]:
                q["dishes"][dish_id] = min(n, 5000)
    dish = Dish.objects.filter(pk=_int(arg), catering=True).first() if name in ("add", "inc", "dec") else None
    if dish:
        key, minimum = str(dish.id), max(dish.catering_min, 1)
        step = 6 if minimum >= 12 else 5
        current = q["dishes"].get(key, 0)
        if name == "add":
            q["dishes"][key] = minimum
        elif name == "inc":
            q["dishes"][key] = current + step
        else:
            q["dishes"][key] = 0 if current - step < minimum else current - step
    service = Service.objects.filter(Q(catering=True) | Q(online=True), pk=_int(arg), active=True, archived=False).first() \
        if name in ("sadd", "sinc", "sdec", "hinc", "hdec") else None
    if service:
        key = str(service.id)
        x = q["services"].get(key, {"q": 0, "h": 0})
        timed = service.pricing in (Service.HOUR, Service.DAY)
        if name == "sadd":
            x = {"q": max(1, service.minimum if service.pricing in (Service.UNIT, Service.PERSON) else 1) if not service.counts_units else 1,
                 "h": max(service.minimum, 1) if timed else 0}
        elif name == "sinc":
            x["q"] += 1
        elif name == "sdec":
            x["q"] -= 1
        elif name == "hinc":
            x["h"] += 1
        elif name == "hdec":
            x["h"] = max(1, x["h"] - 1)
        if x["q"] <= 0:
            q["services"].pop(key, None)
        else:
            q["services"][key] = x
    if name == "ful" and arg in FULS:
        q["ful"] = arg
    elif name == "date":
        q["date"], q["month"] = arg[:10], arg[:10]
    elif name == "cat":
        q["cat"] = arg
    elif name == "month":
        q["month"] = arg[:10]
    elif name in ("hup", "hdown", "mup", "mdown", "am", "pm"):
        h, m = (int(x) for x in (q["time"] or "12:00").split(":"))
        minutes = h * 60 + m + {"hup": 60, "hdown": -60, "mup": 15, "mdown": -15}.get(name, 0)
        if name == "am" and minutes >= 720:
            minutes -= 720
        if name == "pm" and minutes < 720:
            minutes += 720
        minutes %= 1440
        q["time"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
    elif name == "agree":
        q["agree"] = not q["agree"]
    # Delivery and full service come with the delivery fee; pickup doesn't
    fee = delivery_service()
    if fee:
        if q["ful"] in (CateringOrder.DELIVERY, CateringOrder.FULL):
            q["services"][str(fee.id)] = {"q": 1, "h": 0}
        else:
            q["services"].pop(str(fee.id), None)
    return q


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def state(q) -> dict:
    """What the page shows: the calendar, notice, totals, the quote and what's still stopping it from being sent."""
    cfg = CateringSettings.load()
    today = timezone.localdate()
    ls = lines(q)
    catalog = pricing.Catalog(ls)
    totals = pricing.totals(ls, catalog)
    extra = cfg.extra_days(totals["minutes"])
    need = math.ceil(cfg.lead_days) + extra
    earliest = today + datetime.timedelta(days=need)
    soonest = today + datetime.timedelta(days=1 if cfg.rush else need)
    latest = today + datetime.timedelta(days=cfg.max_ahead)
    day = _parse_date(q["date"])
    # the calendar: this month and the next few
    try:
        month = datetime.date.fromisoformat((q.get("month") or (day or earliest).isoformat())[:7] + "-01")
    except ValueError:
        month = earliest.replace(day=1)
    cells = []
    first = month
    for _n in range(first.weekday()):
        cells.append(None)
    d = first
    while d.month == month.month:
        open_day = bool(cfg.days[d.weekday()]) if cfg.days else True
        cells.append({"date": d, "iso": d.isoformat(), "on": d == day, "today": d == today,
                      "off": d < soonest or d > latest or not open_day, "short": soonest <= d < earliest})
        d += datetime.timedelta(days=1)
    next_month = (month + datetime.timedelta(days=32)).replace(day=1)
    prev_month = (month - datetime.timedelta(days=1)).replace(day=1)
    # time
    t = q["time"]
    time_ok = False
    if t:
        h, m = (int(x) for x in t.split(":"))
        at = datetime.time(h, m)
        time_ok = cfg.earliest <= at <= cfg.latest
    h, m = (int(x) for x in (t or "12:00").split(":"))
    picker = {"hour": (h % 12) or 12, "minute": f"{m:02d}", "ampm": "PM" if h >= 12 else "AM"}
    from online_store.storefront import clock
    window = f"{clock(cfg.earliest)} – {clock(cfg.latest)}"
    if not t:
        time_msg = _("Set a time for your event.")
    elif not time_ok:
        time_msg = _("Events can be between %(window)s.") % {"window": window}
    elif q["ful"] == CateringOrder.PICKUP:
        time_msg = _("Ready for pickup at %(t)s.") % {"t": clock(datetime.time(h, m))}
    elif q["ful"] == CateringOrder.FULL:
        time_msg = _("We arrive about 45 minutes before %(t)s.") % {"t": clock(datetime.time(h, m))}
    else:
        early = (datetime.datetime.combine(today, datetime.time(h, m)) - datetime.timedelta(minutes=30)).time()
        time_msg = _("We arrive between %(a)s and %(b)s.") % {"a": clock(early), "b": clock(datetime.time(h, m))}
    # notice
    rush = False
    if day:
        n = (day - today).days
        if n >= need:
            lead = ngettext("%(day)s is %(n)d day away. Your order needs %(need)d days’ notice, so you’re good.",
                            "%(day)s is %(n)d days away. Your order needs %(need)d days’ notice, so you’re good.", n) % {"day": f"{day:%A}", "n": n, "need": need}
            lead_ok = True
        elif cfg.rush and n >= 1:
            lead = _("That’s less than the %(need)d days’ notice this order needs, so it goes in as a request. We’ll tell you quickly if we can do it.") % {"need": need}
            lead_ok, rush = True, True
        else:
            lead = _("This order needs %(need)d days’ notice. Pick %(day)s or later.") % {"need": need, "day": f"{earliest:%B} {earliest.day}"}
            lead_ok = False
        if extra:
            lead += " " + _("(About %(h)d hours of kitchen time.)") % {"h": round(totals["minutes"] / 60)}
    else:
        lead, lead_ok = _("Pick a date. Bigger orders need a little more notice, and the calendar updates as you add food."), None
    # the blocker
    dish_rows = [l for l in ls if l["kind"] == CateringLine.DISH]
    under = [catalog.get(l["kind"], l["ref"]) for l in dish_rows if l["quantity"] < catalog.get(l["kind"], l["ref"]).catering_min]
    staff = [catalog.get(l["kind"], l["ref"]) for l in ls if l["kind"] == CateringLine.SERVICE and staffing(catalog.get(l["kind"], l["ref"]))]
    full_warn = q["ful"] == CateringOrder.FULL and not staff
    fee = delivery_service()
    paid_services = [l for l in ls if l["kind"] == CateringLine.SERVICE and not (fee and l["ref"] == fee.id)]
    if not day:
        blocker = _("Pick a date for your event.")
    elif not lead_ok:
        blocker = _("Your date is too soon for this order.")
    elif day > latest or (cfg.days and not cfg.days[day.weekday()]):
        blocker = _("We don’t cater on that day.")
    elif not time_ok:
        blocker = _("Set a time between %(window)s.") % {"window": window}
    elif not q["ful"]:
        blocker = _("Choose pickup, delivery or full service.")
    elif q["ful"] != CateringOrder.PICKUP and not q["address"].strip():
        blocker = _("Add the address.")
    elif not dish_rows and not paid_services:
        blocker = _("Add some food or a service.")
    elif under:
        blocker = _("%(dish)s has a minimum of %(n)d.") % {"dish": under[0].title, "n": under[0].catering_min}
    elif totals["subtotal"] < cfg.min_subtotal:
        blocker = _("Catering starts at %(min)s before tax.") % {"min": pricing.money(cfg.min_subtotal)}
    elif full_warn:
        blocker = _("Add a server or bartender for full service.")
    elif not q["name"].strip() or "@" not in q["email"]:
        blocker = _("Add your name and email so we can reply.")
    else:
        blocker = ""
    return {"cfg": cfg, "lines": ls, "catalog": catalog, "totals": totals, "need": need, "earliest": earliest, "rush": rush,
            "cells": cells, "month": month, "next_month": next_month.isoformat(), "prev_month": prev_month.isoformat(),
            "no_prev": prev_month < today.replace(day=1), "day": day, "time_ok": time_ok, "time_msg": time_msg,
            "picker": picker, "window": window, "lead": lead, "lead_ok": lead_ok, "blocker": blocker, "full_warn": full_warn,
            "delivery_fee": fee, "min_pct": min(100, int(totals["subtotal"] / cfg.min_subtotal * 100)) if cfg.min_subtotal else 100}


def create(q, card, user=None) -> CateringOrder:
    """The order from a quote that's ready, with the saved card; catering.flow.place takes it from there."""
    cfg = CateringSettings.load()
    h, m = (int(x) for x in q["time"].split(":"))
    order = CateringOrder.objects.create(
        customer=user, name=q["name"].strip()[:120], organization=q["org"].strip()[:120], email=q["email"].strip()[:254],
        phone=q["phone"].strip()[:40], title=(q["title"].strip() or _("Catering order"))[:140],
        event_date=_parse_date(q["date"]), event_time=datetime.time(h, m), fulfilment=q["ful"],
        address=q["address"].strip()[:240] if q["ful"] != CateringOrder.PICKUP else "", note=q["note"].strip(),
        square_customer_id=card["customer"], square_card_id=card["id"], card_brand=card.get("brand", ""), card_last4=card.get("last4", ""))
    for position, line in enumerate(lines(q)):
        CateringLine.objects.create(order=order, kind=line["kind"], quantity=line["quantity"], amount=line["amount"],
                                    price=Decimal(line["price"]), position=position, **{CateringLine.FIELDS[line["kind"]] + "_id": line["ref"]})
    del cfg
    return order
