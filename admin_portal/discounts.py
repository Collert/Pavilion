"""
Discounts (design: AdminDiscounts): discounts without a code (pos_server.Discount without `code`; the ones with
one are the Coupons page's). Each one either applies by itself to every order it fits (`automatic`) or is listed on
the register for the cashier to pick. One form with Save: the list (All, Active, Upcoming, Ended) and the picked
discount's name, on/off, badge, when it runs (days, hours, dates), the discount and what it comes off (as on
Coupons), conditions (minimum subtotal, order types, who gets it, working with coupons), a checkout preview of a
sample order and how it's done. How it prices is pos_server/pricing.py.

    request.session["admin_discounts"] = {"rows": [{"id": 3 or "n…", "title", "auto", "kind", "value", "cap",
        "target", "items": ["d<id>"|"c<id>"], "menus": [ids], "min", "chans", "days": [1-7], "all_day", "from": "16:00",
        "to": "18:00", "start", "end", "who", "stack", "badge", "paused"}], "sel", "gone": [ids], "tab",
        "pv": "pickup"|"delivery"|"store"}
"""
import datetime
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext

from pos_server.models import Combo, Discount, Dish, Menu, OrderDiscount, Station
from pos_server.pricing import tax_rate

from .coupons import CHANNELS, TABS, TARGETS, _date, _money, _num, _short, _text, item_keys, summary

KEY = "admin_discounts"
WHO = [("everyone", _l("Everyone"), _l("Guests included")), ("members", _l("Signed-in customers"), _l("Needs an account")),
       ("staff", _l("Staff only"), _l("Orders with a staff card"))]
DAYS = [_l("Mon"), _l("Tue"), _l("Wed"), _l("Thu"), _l("Fri"), _l("Sat"), _l("Sun")]
DAYS_FULL = [_l("Monday"), _l("Tuesday"), _l("Wednesday"), _l("Thursday"), _l("Friday"), _l("Saturday"), _l("Sunday")]
PRESETS = [("all", _l("Every day"), [1, 2, 3, 4, 5, 6, 7]), ("week", _l("Weekdays"), [1, 2, 3, 4, 5]), ("weekend", _l("Weekends"), [6, 7])]
CLOSE = "23:59"
TIMES = [f"{m // 60:02d}:{m % 60:02d}" for m in range(6 * 60, 24 * 60, 30)]
ALL_CHANS = ["pickup", "delivery", "store"]


def _mins(t) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def row_of(d: Discount) -> dict:
    timed = bool(d.start_time or d.end_time)
    return {"id": d.id, "title": d.name, "auto": d.automatic, "kind": d.kind, "value": _text(d.value), "cap": _text(d.max_amount),
            "target": d.target, "items": sorted([f"d{x.id}" for x in d.dishes.all()] + [f"c{x.id}" for x in d.combos.all()]),
            "menus": sorted(m.id for m in d.menus.all()), "min": _text(d.min_subtotal),
            "chans": [c for c, _label in CHANNELS if c in (d.channels or [])] or ["store"],
            "days": sorted(int(c) for c in d.weekdays) if d.weekdays else [1, 2, 3, 4, 5, 6, 7], "all_day": not timed,
            "from": d.start_time.strftime("%H:%M") if d.start_time else ("00:00" if timed else "16:00"),
            "to": d.end_time.strftime("%H:%M") if d.end_time else (CLOSE if timed else "18:00"),
            "start": d.starts_on.isoformat() if d.starts_on else "", "end": d.ends_on.isoformat() if d.ends_on else "",
            "who": d.audience, "stack": d.stacks, "badge": d.badge, "paused": not d.active}


def discounts():
    return Discount.objects.filter(code__isnull=True).prefetch_related("dishes", "combos", "menus")


def saved() -> list:
    return [row_of(d) for d in discounts()]


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        rows = saved()
        st = {"rows": rows, "sel": rows[0]["id"] if rows else None, "gone": [], "tab": "all", "pv": "pickup"}
        follow(st)
    return st


def follow(st):
    """Preview an order type the picked discount works on."""
    e = selected(st)
    if e and st["pv"] not in e["chans"]:
        st["pv"] = e["chans"][0]


def selected(st):
    return next((r for r in st["rows"] if str(r["id"]) == str(st["sel"])), st["rows"][0] if st["rows"] else None)


def take_post(st, post):
    e = selected(st)
    if not e or post.get("editing") != str(e["id"]):
        return
    for f, size in (("title", 40), ("value", 8), ("cap", 8), ("min", 8)):
        if f in post:
            e[f] = post.get(f, "").strip()[:size]
    for f in ("start", "end"):
        if f in post and (post[f] == "" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", post[f])):
            e[f] = post[f] if (f == "end" or post[f]) else e[f]
    if post.get("from") in TIMES and post["from"] != e["from"]:
        e["from"] = post["from"]
        if _mins(e["to"]) <= _mins(e["from"]):  # keep two hours after it
            e["to"] = _hhmm(min(_mins(e["from"]) + 120, _mins(TIMES[-1])))
    elif post.get("to") in TIMES + [CLOSE] and _mins(post["to"]) > _mins(e["from"]):
        e["to"] = post["to"]


def blank() -> dict:
    return {"id": f"n{timezone.now().strftime('%H%M%S%f')}", "title": _("New discount"), "auto": True, "kind": "pct", "value": "10",
            "cap": "", "target": "order", "items": [], "menus": [], "min": "", "chans": list(ALL_CHANS),
            "days": [1, 2, 3, 4, 5, 6, 7], "all_day": True, "from": "16:00", "to": "18:00",
            "start": timezone.localdate().isoformat(), "end": "", "who": "everyone", "stack": False, "badge": False, "paused": True}


def apply_op(st, op) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    if name == "pick":
        found = next((r for r in st["rows"] if str(r["id"]) == arg), None)
        if found:
            st["sel"] = found["id"]
            follow(st)
    elif name == "tab" and arg in dict(TABS):
        st["tab"] = arg
    elif name == "pv" and arg in ALL_CHANS:
        st["pv"] = arg
    elif name == "new":
        row = blank()
        st["rows"].insert(0, row)
        st.update(sel=row["id"], tab="all")
    elif not e:
        return ""
    elif name == "how" and arg in ("auto", "pick"):
        e["auto"] = arg == "auto"
        if not e["auto"]:  # the register: in store, and nothing off a delivery fee
            e["chans"] = ["store"]
            if e["target"] == "delivery":
                e["target"] = "order"
            follow(st)
    elif name == "kind" and arg in ("pct", "amt") and arg != e["kind"]:
        e.update(kind=arg, value="10" if arg == "pct" else "2")
    elif name == "target" and arg in dict((t[0], 1) for t in TARGETS) and not (arg == "delivery" and not e["auto"]):
        if arg == "delivery":
            e["chans"] = ["delivery"]
            follow(st)
        elif e["target"] == "delivery":
            e["chans"] = list(ALL_CHANS)
        e["target"] = arg
    elif name == "free":
        e.update(kind="pct", value="100")
    elif name == "item":
        e["items"] = sorted(set(e["items"]) ^ {arg})
    elif name == "cat":
        keys = set(item_keys(arg))
        e["items"] = sorted(set(e["items"]) - keys if keys <= set(e["items"]) else set(e["items"]) | keys)
    elif name == "menu" and arg.isdigit():
        e["menus"] = sorted(set(e["menus"]) ^ {int(arg)})
    elif name == "chan" and arg in dict(CHANNELS) and e["target"] != "delivery" and e["auto"]:
        chans = [c for c in e["chans"] if c != arg] if arg in e["chans"] else e["chans"] + [arg]
        if chans:
            e["chans"] = [c for c, _label in CHANNELS if c in chans]
    elif name == "day" and arg.isdigit() and 1 <= int(arg) <= 7:
        e["days"] = sorted(set(e["days"]) ^ {int(arg)})
    elif name == "days":
        e["days"] = list(dict((k, days) for k, _label, days in PRESETS).get(arg, e["days"]))
    elif name == "hours" and arg in ("all", "set"):
        e["all_day"] = arg == "all"
    elif name == "who" and arg in dict((k, 1) for k, *_r in WHO):
        e["who"] = arg
    elif name == "flip" and arg in ("paused", "badge", "stack"):
        e[arg] = not e[arg]
    elif name == "end":
        e["end"] = "" if e["end"] else max(e["start"] or "", timezone.localdate().isoformat())
    elif name == "dup":
        copy = {**e, "id": f"n{timezone.now().strftime('%H%M%S%f')}", "title": (e["title"] + " " + _("copy"))[:40], "paused": True}
        st["rows"].insert(0, copy)
        st.update(sel=copy["id"], tab="all")
        return _("Copied as “%(name)s”. It’s off until you turn it on.") % {"name": copy["title"]}
    elif name == "delete":
        st["rows"] = [r for r in st["rows"] if r is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = st["rows"][0]["id"] if st["rows"] else None
        return _("%(name)s deleted. Save to keep it.") % {"name": e["title"] or _("Discount")}
    return ""


def errors(r) -> dict:
    out = {}
    if not r["title"].strip():
        out["name"] = _("Give it a name. Customers see it on their receipt.")
    if not r["days"]:
        out["date"] = _("Pick at least one day.")
    elif r["end"] and r["start"] and r["end"] < r["start"]:
        out["date"] = _("The end date is before the start date.")
    elif not r["all_day"] and _mins(r["to"]) <= _mins(r["from"]):
        out["date"] = _("It has to end after it starts.")
    value = _num(r["value"])
    if not value or value <= 0:
        out["value"] = _("Enter how much it takes off.")
    elif r["kind"] == "pct" and value > 100:
        out["value"] = _("Percent off can’t be more than 100.")
    elif r["target"] == "items" and not r["items"]:
        out["value"] = _("Pick at least one dish.")
    elif r["target"] == "menus" and not r["menus"]:
        out["value"] = _("Pick at least one menu.")
    elif r["cap"] and (_num(r["cap"]) is None or _num(r["cap"]) < 0):
        out["value"] = _("Check the highlighted amounts.")
    if r["min"] and (_num(r["min"]) is None or _num(r["min"]) < 0):
        out["min"] = _("The minimum is an amount, like 40.")
    return out


def problems(st) -> dict:
    return {str(r["id"]): errs for r in st["rows"] if (errs := errors(r))}


def is_dirty(st) -> bool:
    return bool(st["gone"]) or st["rows"] != saved()


@transaction.atomic
def save(st):
    Discount.objects.filter(pk__in=st["gone"], code__isnull=True).delete()
    for r in st["rows"]:
        d = Discount.objects.get(pk=r["id"]) if isinstance(r["id"], int) else Discount()
        if r["auto"] or r["target"] in ("menus", "delivery"):
            d.scope = Discount.ORDER
        elif not d.pk or d.automatic or d.target != r["target"]:
            d.scope = Discount.ANY  # the cashier picks it for an item or the order; offers already in use keep theirs
        d.code, d.name, d.automatic, d.kind, d.value = None, r["title"].strip(), r["auto"], r["kind"], _num(r["value"])
        d.max_amount = _num(r["cap"]) if r["kind"] == "pct" and r["cap"] else None
        d.target = r["target"]
        d.min_subtotal = _num(r["min"]) if r["min"] else None
        d.channels = list(r["chans"]) if r["auto"] else ["store"]
        d.weekdays = "" if len(r["days"]) == 7 else "".join(str(x) for x in sorted(r["days"]))
        d.start_time = None if r["all_day"] else datetime.time.fromisoformat(r["from"])
        d.end_time = None if r["all_day"] or r["to"] == CLOSE else datetime.time.fromisoformat(r["to"])
        d.starts_on = r["start"] or None
        d.ends_on = r["end"] or None
        d.audience, d.stacks, d.badge, d.active = r["who"], r["stack"], r["badge"], not r["paused"]
        d.save()
        d.dishes.set([int(k[1:]) for k in r["items"] if k.startswith("d")] if r["target"] == "items" else [])
        d.combos.set([int(k[1:]) for k in r["items"] if k.startswith("c")] if r["target"] == "items" else [])
        d.menus.set(r["menus"] if r["target"] == "menus" else [])


# ---------- when it runs ----------

def clock(t) -> str:
    if t == CLOSE:
        return _("Close")
    m = _mins(t)
    h, mm = divmod(m, 60)
    return f"{(h + 11) % 12 + 1}{f':{mm:02d}' if mm else ''} {'AM' if h < 12 else 'PM'}"


def day_text(days) -> str:
    days = sorted(days)
    if len(days) == 7:
        return _("Every day")
    if days == [1, 2, 3, 4, 5]:
        return _("Weekdays")
    if days == [6, 7]:
        return _("Weekends")
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        return f"{DAYS[days[0] - 1]}–{DAYS[days[-1] - 1]}"
    return ", ".join(str(DAYS[d - 1]) for d in days)


def sched(r, low=False) -> str:
    days = day_text(r["days"])
    if low and days in (_("Every day"), _("Weekdays"), _("Weekends")):
        days = days[0].lower() + days[1:]
    return days + (", " + _("all day") if r["all_day"] else f", {clock(r['from'])}–{clock(r['to'])}")


def running_now(r, now) -> bool:
    local = timezone.localtime(now)
    minutes = local.hour * 60 + local.minute
    return local.isoweekday() in r["days"] and (r["all_day"] or _mins(r["from"]) <= minutes < _mins(r["to"]))


def next_run(r, now) -> str:
    local = timezone.localtime(now)
    minutes = local.hour * 60 + local.minute
    for i in range(8):
        day = (local.isoweekday() - 1 + i) % 7 + 1
        if day not in r["days"]:
            continue
        if i == 0 and not r["all_day"] and minutes < _mins(r["from"]):
            return _("today at %(time)s") % {"time": clock(r["from"])}
        if i == 0:
            continue
        when = _("tomorrow") if i == 1 else str(DAYS_FULL[day - 1])
        return when if r["all_day"] else _("%(day)s at %(time)s") % {"day": when, "time": clock(r["from"])}
    return ""


def status(r, now) -> tuple[str, str]:
    today = timezone.localdate(now).isoformat()
    if r["end"] and r["end"] < today:
        return _("Ended"), "ended"
    if r["paused"]:
        return _("Off"), "paused"
    if r["start"] and r["start"] > today:
        return _("Scheduled"), "scheduled"
    if running_now(r, now):
        return _("On now"), "now"
    return _("Active"), "active"


def group(key) -> str:
    return {"paused": "active", "active": "active", "now": "active", "scheduled": "scheduled"}.get(key, "ended")


def big(r) -> str:
    value = _num(r["value"]) or 0
    if r["target"] == "delivery" and r["kind"] == "pct" and value >= 100:
        return _("FREE")
    return f"{_text(value)}%" if r["kind"] == "pct" else f"${_text(value)}"


# ---------- the page ----------

def page(st, now=None) -> dict:
    now = now or timezone.now()
    errs = problems(st)
    dirty = is_dirty(st)
    if errs:
        bad = next(r for r in st["rows"] if str(r["id"]) in errs)
        status_text, kind = _("Fix %(name)s to save") % {"name": bad["title"].strip() or _("the new discount")}, "bad"
    else:
        status_text, kind = (_("Unsaved changes"), "dirty") if dirty else (_("All changes saved"), "")
    used = dict(Discount.objects.filter(code__isnull=True).values_list("id", "times_used"))
    menus = {m.id: m.title for m in Menu.objects.all()}
    colors = dict((t[0], t[4]) for t in TARGETS)
    e = selected(st)
    rows, counts = [], {"all": len(st["rows"]), "active": 0, "scheduled": 0, "ended": 0}
    for r in st["rows"]:
        label, key = status(r, now)
        counts[group(key)] += 1
        if st["tab"] != "all" and group(key) != st["tab"]:
            continue
        rows.append({"r": r, "on": r is e, "st": label, "st_key": key, "big": big(r), "desc": f"{summary(r, menus)} · {sched(r)}",
                     "used": str(used.get(r["id"], 0) if isinstance(r["id"], int) else 0), "color": colors[r["target"]],
                     "dim": group(key) == "ended", "auto": r["auto"]})
    return {"st": st, "status": status_text, "status_kind": kind, "can_save": dirty and not errs, "rows": rows,
            "tabs": [(k, label, counts[k], st["tab"] == k) for k, label in TABS],
            "d": editor(st, e, errs.get(str(e["id"]), {}), menus, now) if e else None}


def editor(st, e, err, menus, now) -> dict:
    label, key = status(e, now)
    value = _num(e["value"]) or Decimal(0)
    is_pct = e["kind"] == "pct"
    nxt = next_run(e, now)
    now_text = {"now": _("Running now"), "active": _("Next: %(when)s") % {"when": nxt} if nxt else "", "paused": _("Turned off"),
                "scheduled": _("Starts %(date)s") % {"date": _date(e["start"])} if e["start"] else "",
                "ended": _("Ended %(date)s") % {"date": _date(e["end"])} if e["end"] else ""}[key]
    off_text = f"{_text(value)}%" if is_pct else _money(value).replace(".00", "")
    badge_hint = (_("Only for discounts that apply by themselves.") if not e["auto"]
                  else _("Delivery discounts show at checkout only.") if e["target"] == "delivery"
                  else _("“%(off)s off” on dishes on menu boards and online, while it runs.") % {"off": off_text} if e["badge"]
                  else _("Off. The price drops at checkout only."))
    times = TIMES if e["from"] in TIMES else sorted(TIMES + [e["from"]])
    to_times = [t for t in TIMES + [CLOSE] + ([e["to"]] if e["to"] not in TIMES + [CLOSE] else []) if _mins(t) > _mins(e["from"])]
    today = timezone.localdate(now).isoformat()
    sched_text = (_("Runs %(when)s") % {"when": sched(e, low=True)}
                  + (", " + _("%(start)s to %(end)s") % {"start": _date(e["start"] or today), "end": _date(e["end"])} if e["end"]
                     else ", " + _("from %(start)s") % {"start": _date(e["start"])} if e["start"] and e["start"] > today else "")
                  + ". " + _("Uses the store’s time zone.")) if e["days"] else ""
    cats = []
    for station in Station.objects.filter(archived=False).order_by("position", "id"):
        dishes = list(Dish.objects.filter(archived=False, station=station).order_by("title"))
        if dishes:
            keys = [f"d{d.id}" for d in dishes]
            cats.append({"id": station.id, "name": station.name, "color": station.color, "all": set(keys) <= set(e["items"]), "n": len(keys),
                         "items": [(f"d{d.id}", d.title, _money(d.price).replace(".00", ""), f"d{d.id}" in e["items"]) for d in dishes]})
    combos = list(Combo.objects.filter(archived=False).order_by("title"))
    if combos:
        keys = [f"c{c.id}" for c in combos]
        cats.append({"id": "combos", "name": _("Combos"), "color": "#5B6A62", "all": set(keys) <= set(e["items"]), "n": len(keys),
                     "items": [(f"c{c.id}", c.title, "", f"c{c.id}" in e["items"]) for c in combos]})
    menu_rows = [(m.id, m.title, ngettext("%(n)d dish", "%(n)d dishes", m.n) % {"n": m.n}, m.id in e["menus"])
                 for m in Menu.objects.annotate(n=Count("dishes", distinct=True)).order_by("position", "id")]
    stats = {"given": Decimal(0), "sales": Decimal(0), "n": 0}
    if isinstance(e["id"], int):
        uses = OrderDiscount.objects.filter(discount_id=e["id"], order__timestamp__gte=now - datetime.timedelta(days=90))
        stats["given"] = uses.aggregate(s=Sum("amount"))["s"] or Decimal(0)
        stats["sales"] = uses.aggregate(s=Sum("order__total"))["s"] or Decimal(0)
        stats["n"] = uses.values("order").distinct().count()
    from events.models import StoreHours
    fee = StoreHours.load().delivery_fee
    days_on = set(e["days"])
    return {
        "e": e, "key": str(e["id"]), "err": err, "is_pct": is_pct, "now_text": now_text, "now_on": key == "now",
        "live_hint": (_("Off. Nothing is discounted.") if e["paused"] else _("Applies by itself, no code needed.") if e["auto"]
                      else _("Listed on the register for the cashier to pick.")),
        "badge_hint": badge_hint, "badge_lock": not e["auto"] or e["target"] == "delivery",
        "days": [(i + 1, str(d)[:2], DAYS_FULL[i], i + 1 in days_on, i + 1 == timezone.localtime(now).isoweekday()) for i, d in enumerate(DAYS)],
        "presets": [(k, label_) for k, label_, _d in PRESETS],
        "from_opts": [(t, clock(t), t == e["from"]) for t in times], "to_opts": [(t, clock(t), t == e["to"]) for t in to_times],
        "sched_text": sched_text,
        "targets": [(k, label_, desc, icon, color, e["target"] == k, k == "delivery" and not e["auto"]) for k, label_, desc, icon, color in TARGETS],
        "cats": cats, "menus": menu_rows, "free": is_pct and value >= 100,
        "del_text": (_("Free delivery. The whole delivery fee comes off on delivery orders that qualify.") if is_pct and value >= 100
                     else _("Takes %(v)s off the delivery fee on delivery orders.") % {"v": f"{_text(value)}%" if is_pct else _money(value)})
        + ("" if fee else " " + _("The store charges no delivery fee yet: set it on Store hours.")),
        "chans": [(c, label_, c in e["chans"]) for c, label_ in CHANNELS], "chan_lock": e["target"] == "delivery" or not e["auto"],
        "who": [(k, label_, desc, e["who"] == k) for k, label_, desc in WHO],
        "who_note": (_("Orders on the register aren’t signed in, so only online orders get it.") if e["who"] == "members" and "store" in e["chans"]
                     else ""),
        "stack_hint": _("A coupon can come off on top of this.") if e["stack"] else _("If a coupon is used, the customer gets whichever saves more."),
        "stats": [(_("Times applied"), str(stats["n"])), (_("Discount given"), _short(stats["given"])), (_("Orders total"), _short(stats["sales"])),
                  (_("Avg. discount"), _money(stats["given"] / stats["n"]) if stats["n"] else "—")],
        "limit_text": _("Last 90 days.") if stats["n"] else _("Not applied yet."),
        "del_hint": (_("Past orders keep their discount. It just stops applying.") if used_ever(e)
                     else _("It hasn’t been applied to any orders yet.")),
        "pv": preview(st, e, value, fee),
    }


def used_ever(e) -> bool:
    return isinstance(e["id"], int) and OrderDiscount.objects.filter(discount_id=e["id"]).exists()


def preview(st, e, value, fee) -> dict:
    """A sample order at checkout with this discount: a few dishes from different stations."""
    mode = st.get("pv", "pickup")
    picks = []
    for station in Station.objects.filter(archived=False).order_by("position", "id"):
        dish = Dish.objects.filter(archived=False, station=station).order_by("price").first()
        if dish:
            picks.append(dish)
        if len(picks) == 4:
            break
    qty = {1: 2}
    lines = [(d, qty.get(i, 1)) for i, d in enumerate(picks)]
    sub = sum((d.price * q for d, q in lines), Decimal(0))
    fee = fee if mode == "delivery" else Decimal(0)
    menu_dishes = set(Dish.objects.filter(menu__in=e["menus"]).values_list("id", flat=True)) if e["target"] == "menus" else set()

    def eligible(d):
        return e["target"] == "order" or (e["target"] == "items" and f"d{d.id}" in e["items"]) or (e["target"] == "menus" and d.id in menu_dishes)

    base = fee if e["target"] == "delivery" else sum((d.price * q for d, q in lines if eligible(d)), Decimal(0))
    minimum = _num(e["min"]) if e["min"] else None
    names = {"pickup": _("pickup"), "delivery": _("delivery"), "store": _("in-store")}
    why = ""
    if mode not in e["chans"]:
        why = _("Doesn’t apply: only for %(where)s orders.") % {"where": _(" and ").join(str(names[c]) for c in ALL_CHANS if c in e["chans"])}
    elif minimum and sub < minimum:
        why = _("Doesn’t apply: needs a %(amount)s subtotal.") % {"amount": _money(minimum)}
    elif not base:
        why = _("Doesn’t apply: no delivery fee on this order.") if e["target"] == "delivery" else _("Doesn’t apply: nothing in this cart qualifies.")
    off = Decimal(0)
    cap = _num(e["cap"]) if e["cap"] else None
    if not why:
        off = base * min(value, Decimal(100)) / 100 if e["kind"] == "pct" else min(value, base)
        if e["kind"] == "pct" and cap:
            off = min(off, cap)
        off = off.quantize(Decimal("0.01"))
    hit = not why and e["target"] in ("items", "menus")
    tax = ((sub - (off if e["target"] != "delivery" else 0)) * tax_rate()).quantize(Decimal("0.01"))
    sums = [(_("Subtotal"), _money(sub), "")]
    if fee:
        sums.append((_("Delivery fee"), _money(fee), ""))
    if off:
        sums.append((e["title"] or _("Discount"), "−" + _money(off), "green"))
    if tax:
        sums.append((_("Tax"), _money(tax), ""))
    sums.append((_("Total"), _money(sub + fee - off + tax), "big"))
    who = {"staff": " " + _("Applies when a staff card is on the order."), "members": " " + _("Customer must be signed in.")}.get(e["who"], "")
    when = (_("during %(when)s") % {"when": sched(e, low=True)}) if e["auto"] else _("when the cashier picks it")
    note = why or (_("Saves %(amount)s %(when)s.") % {"amount": _money(off), "when": when}
                   + (" " + _("Capped at %(cap)s.") % {"cap": _money(cap)} if e["kind"] == "pct" and cap and off >= cap else "") + who if off else "")
    return {"mode": mode, "lines": [(q, d.title, _money(d.price * q), hit and eligible(d)) for d, q in lines], "sums": sums,
            "note": note, "warn": bool(why)}
