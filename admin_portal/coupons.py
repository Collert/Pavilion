"""
Coupons (design: AdminCoupons): discounts with a code (pos_server.Discount with `code`; the ones without are the
Discounts page's offers). One form with Save: the list (All, Active, Upcoming, Ended) and the picked coupon's code,
what customers see, whether it's accepted, the discount (percent with an optional cap, or an amount) and what it
comes off (the order, picked dishes and combos, menus, or the delivery fee), conditions (minimum subtotal, order
types, dates), limits (total uses, per customer, first order only), a checkout preview of a sample order and how
it's done. How a coupon prices is pos_server/pricing.py.

    request.session["admin_coupons"] = {"rows": [{"id": 3 or "n…", "code", "title", "kind", "value", "cap", "target",
        "items": ["d<id>"|"c<id>"], "menus": [ids], "min", "chans", "start", "end", "limit", "per", "first", "paused"}],
        "sel", "gone": [ids], "tab", "pv": "pickup"|"delivery"}
"""
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext

from pos_server.models import Combo, Discount, Dish, Menu, OrderDiscount, Station
from pos_server.pricing import tax_rate

KEY = "admin_coupons"
TARGETS = [("order", _l("Entire order"), _l("Everything in the cart"), "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6", "#1F4A3A"),
           ("items", _l("Specific items"), _l("Only the dishes you pick"), "M4 6h16 M4 12h16 M4 18h10", "#4E79B5"),
           ("menus", _l("Specific menus"), _l("Anything on those menus"), "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z M5 17a3 3 0 0 1 3-3h11", "#7A5AC8"),
           ("delivery", _l("Delivery fee"), _l("Off the delivery charge"),
            "M2.5 17.5a3 3 0 1 0 6 0 3 3 0 1 0-6 0z M15.5 17.5a3 3 0 1 0 6 0 3 3 0 1 0-6 0z M5.5 17.5 9 10h6l3.5 7.5 M9 10 7.5 7H5 M13 7h3l2 3", "#C9821A")]
CHANNELS = [("pickup", _l("Pickup")), ("delivery", _l("Delivery")), ("store", _l("In store"))]
PER = [("any", _l("Unlimited"), None), ("once", _l("Once"), 1), ("three", _l("Up to 3"), 3)]
TABS = [("all", _l("All")), ("active", _l("Active")), ("scheduled", _l("Upcoming")), ("ended", _l("Ended"))]
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _num(value):
    try:
        return Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value) -> str:
    return "" if value is None else format(Decimal(value).normalize(), "f")


def new_code() -> str:
    return get_random_string(8, ALPHABET)


def row_of(d: Discount) -> dict:
    per = next((k for k, _label, n in PER if n == d.per_customer), "any")
    return {"id": d.id, "code": d.code or "", "title": d.name, "kind": d.kind, "value": _text(d.value), "cap": _text(d.max_amount),
            "target": d.target, "items": sorted([f"d{x.id}" for x in d.dishes.all()] + [f"c{x.id}" for x in d.combos.all()]),
            "menus": sorted(m.id for m in d.menus.all()), "min": _text(d.min_subtotal),
            "chans": [c for c, _label in CHANNELS if c in (d.channels or [])],
            "start": d.starts_on.isoformat() if d.starts_on else "", "end": d.ends_on.isoformat() if d.ends_on else "",
            "limit": str(d.max_uses or ""), "per": per, "first": d.first_order, "paused": not d.active}


def coupons():
    return Discount.objects.filter(code__isnull=False).prefetch_related("dishes", "combos", "menus")


def saved() -> list:
    return [row_of(d) for d in coupons()]


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        rows = saved()
        st = {"rows": rows, "sel": rows[0]["id"] if rows else None, "gone": [], "tab": "all", "pv": "pickup"}
    return st


def selected(st):
    return next((r for r in st["rows"] if str(r["id"]) == str(st["sel"])), st["rows"][0] if st["rows"] else None)


def take_post(st, post):
    e = selected(st)
    if not e or post.get("editing") != str(e["id"]):
        return
    if "code" in post:
        e["code"] = re.sub(r"[^A-Z0-9]", "", post.get("code", "").upper())[:20]
    for f, size in (("title", 40), ("value", 8), ("cap", 8), ("min", 8), ("limit", 6)):
        if f in post:
            e[f] = post.get(f, "").strip()[:size]
    for f in ("start", "end"):
        if f in post and (post[f] == "" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", post[f])):
            e[f] = post[f] if (f == "end" or post[f]) else e[f]


def blank(code) -> dict:
    return {"id": f"n{timezone.now().strftime('%H%M%S%f')}", "code": code, "title": "", "kind": "pct", "value": "10", "cap": "",
            "target": "order", "items": [], "menus": [], "min": "", "chans": ["pickup", "delivery", "store"],
            "start": timezone.localdate().isoformat(), "end": "", "limit": "", "per": "any", "first": False, "paused": False}


def apply_op(st, op) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    if name == "pick":
        found = next((r for r in st["rows"] if str(r["id"]) == arg), None)
        if found:
            st["sel"] = found["id"]
    elif name == "tab" and arg in dict(TABS):
        st["tab"] = arg
    elif name == "pv" and arg in ("pickup", "delivery"):
        st["pv"] = arg
    elif name == "new":
        row = blank(new_code())
        st["rows"].insert(0, row)
        st.update(sel=row["id"], tab="all")
    elif not e:
        return ""
    elif name == "gen":
        e["code"] = new_code()
    elif name == "kind" and arg in ("pct", "amt") and arg != e["kind"]:
        e.update(kind=arg, value="10" if arg == "pct" else "5")
    elif name == "target" and arg in dict((t[0], 1) for t in TARGETS):
        if arg == "delivery":
            e["chans"] = ["delivery"]
        elif e["target"] == "delivery":
            e["chans"] = ["pickup", "delivery", "store"]
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
    elif name == "chan" and arg in dict(CHANNELS) and e["target"] != "delivery":
        chans = [c for c in e["chans"] if c != arg] if arg in e["chans"] else e["chans"] + [arg]
        if chans:
            e["chans"] = [c for c, _label in CHANNELS if c in chans]
    elif name == "per" and arg in dict((k, 1) for k, *_r in PER):
        e["per"] = arg
    elif name == "flip" and arg in ("paused", "first"):
        e[arg] = not e[arg]
    elif name == "end":
        e["end"] = "" if e["end"] else max(e["start"] or "", timezone.localdate().isoformat())
    elif name == "dup":
        copy = {**e, "id": f"n{timezone.now().strftime('%H%M%S%f')}", "code": (e["code"] + "2")[:20], "paused": True}
        st["rows"].insert(0, copy)
        st.update(sel=copy["id"], tab="all")
        return _("Copied as %(code)s, paused until you turn it on.") % {"code": copy["code"]}
    elif name == "delete":
        st["rows"] = [r for r in st["rows"] if r is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = st["rows"][0]["id"] if st["rows"] else None
        return _("%(code)s deleted. Save to keep it.") % {"code": e["code"] or _("Coupon")}
    return ""


def errors(r, rows) -> dict:
    out = {}
    taken = set(Discount.objects.filter(code__isnull=False).exclude(pk__in=[x["id"] for x in rows if isinstance(x["id"], int)])
                .values_list("code", flat=True))
    if not r["code"]:
        out["code"] = _("Give it a code.")
    elif len(r["code"]) < 3:
        out["code"] = _("Codes need at least 3 characters.")
    elif r["code"] in taken or any(x is not r and x["code"] == r["code"] for x in rows):
        out["code"] = _("Another coupon already uses %(code)s.") % {"code": r["code"]}
    value = _num(r["value"])
    if not value or value <= 0:
        out["value"] = _("Enter how much it takes off.")
    elif r["kind"] == "pct" and value > 100:
        out["value"] = _("Percent off can’t be more than 100.")
    elif r["target"] == "items" and not r["items"]:
        out["value"] = _("Pick at least one dish.")
    elif r["target"] == "menus" and not r["menus"]:
        out["value"] = _("Pick at least one menu.")
    if r["end"] and r["start"] and r["end"] < r["start"]:
        out["date"] = _("The end date is before the start date.")
    for f in ("cap", "min"):
        if r[f] and (_num(r[f]) is None or _num(r[f]) < 0):
            out["value" if f == "cap" else "date"] = _("Check the highlighted amounts.")
    if r["limit"] and not r["limit"].isdigit():
        out["date"] = _("Total uses is a whole number.")
    return out


def problems(st) -> dict:
    return {str(r["id"]): errs for r in st["rows"] if (errs := errors(r, st["rows"]))}


def is_dirty(st) -> bool:
    return bool(st["gone"]) or st["rows"] != saved()


def item_keys(cat) -> list:
    if cat == "combos":
        return [f"c{c.id}" for c in Combo.objects.filter(archived=False)]
    return [f"d{d.id}" for d in Dish.objects.filter(archived=False, station_id=cat if str(cat).isdigit() else None)]


@transaction.atomic
def save(st):
    Discount.objects.filter(pk__in=st["gone"], code__isnull=False).delete()
    for r in st["rows"]:
        d = Discount.objects.get(pk=r["id"]) if isinstance(r["id"], int) else Discount()
        d.code, d.name, d.kind, d.value = r["code"], r["title"] or r["code"], r["kind"], _num(r["value"])
        d.max_amount = _num(r["cap"]) if r["kind"] == "pct" and r["cap"] else None
        d.target = r["target"]
        d.scope = Discount.ORDER if r["target"] in ("menus", "delivery") else Discount.ANY
        d.min_subtotal = _num(r["min"]) if r["min"] else None
        d.channels = list(r["chans"])
        d.starts_on = r["start"] or None
        d.ends_on = r["end"] or None
        d.max_uses = int(r["limit"]) if r["limit"] else None
        d.per_customer = dict((k, n) for k, _label, n in PER)[r["per"]]
        d.first_order, d.active = r["first"], not r["paused"]
        d.save()
        d.dishes.set([int(k[1:]) for k in r["items"] if k.startswith("d")] if r["target"] == "items" else [])
        d.combos.set([int(k[1:]) for k in r["items"] if k.startswith("c")] if r["target"] == "items" else [])
        d.menus.set(r["menus"] if r["target"] == "menus" else [])


# ---------- the page ----------

def _money(value) -> str:
    return f"${Decimal(value or 0):,.2f}"


def _short(value) -> str:
    value = Decimal(value or 0)
    return f"${value / 1000:.1f}k" if value >= 1000 else _money(value)


def _date(iso) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return date_format(d, "M j" if d.year == timezone.localdate().year else "M j, Y")


def status(r, used) -> tuple[str, str]:
    today = timezone.localdate().isoformat()
    if r["end"] and r["end"] < today:
        return _("Ended"), "ended"
    if r["limit"].isdigit() and used >= int(r["limit"]):
        return _("Used up"), "ended"
    if r["paused"]:
        return _("Paused"), "paused"
    if r["start"] and r["start"] > today:
        return _("Scheduled"), "scheduled"
    return _("Active"), "active"


def group(key) -> str:
    return {"paused": "active", "active": "active", "scheduled": "scheduled"}.get(key, "ended")


def big(r) -> str:
    value = _num(r["value"]) or 0
    if r["target"] == "delivery" and r["kind"] == "pct" and value >= 100:
        return _("FREE")
    return f"{_text(value)}%" if r["kind"] == "pct" else f"${_text(value)}"


def summary(r, menus) -> str:
    value = _num(r["value"]) or 0
    v = f"{_text(value)}%" if r["kind"] == "pct" else _money(value).replace(".00", "")
    if r["target"] == "delivery":
        return _("Free delivery") if r["kind"] == "pct" and value >= 100 else _("%(v)s off delivery") % {"v": v}
    if r["target"] == "items":
        return ngettext("%(v)s off %(n)d item", "%(v)s off %(n)d items", len(r["items"])) % {"v": v, "n": len(r["items"])}
    if r["target"] == "menus":
        if len(r["menus"]) == 1:
            return _("%(v)s off %(menu)s") % {"v": v, "menu": menus.get(r["menus"][0], "?")}
        return ngettext("%(v)s off %(n)d menu", "%(v)s off %(n)d menus", len(r["menus"])) % {"v": v, "n": len(r["menus"])}
    return _("%(v)s off the order") % {"v": v}


def page(st) -> dict:
    errs = problems(st)
    dirty = is_dirty(st)
    if errs:
        bad = next(r for r in st["rows"] if str(r["id"]) in errs)
        status_text, kind = _("Fix %(code)s to save") % {"code": bad["code"] or _("the new coupon")}, "bad"
    else:
        status_text, kind = (_("Unsaved changes"), "dirty") if dirty else (_("All changes saved"), "")
    used = dict(Discount.objects.filter(code__isnull=False).values_list("id", "times_used"))
    menus = {m.id: m.title for m in Menu.objects.all()}
    e = selected(st)
    rows, counts = [], {"all": len(st["rows"]), "active": 0, "scheduled": 0, "ended": 0}
    for r in st["rows"]:
        n = used.get(r["id"], 0) if isinstance(r["id"], int) else 0
        label, key = status(r, n)
        counts[group(key)] += 1
        if st["tab"] != "all" and group(key) != st["tab"]:
            continue
        rows.append({"r": r, "on": r is e, "st": label, "st_key": key, "big": big(r), "desc": r["title"] or summary(r, menus),
                     "used": f"{n}/{r['limit']}" if r["limit"] else str(n), "color": dict((t[0], t[4]) for t in TARGETS)[r["target"]],
                     "dim": group(key) == "ended"})
    return {"st": st, "status": status_text, "status_kind": kind, "can_save": dirty and not errs, "rows": rows,
            "tabs": [(k, label, counts[k], st["tab"] == k) for k, label in TABS],
            "d": editor(st, e, errs.get(str(e["id"]), {}), used, menus) if e else None}


def editor(st, e, err, used, menus) -> dict:
    n = used.get(e["id"], 0) if isinstance(e["id"], int) else 0
    label, key = status(e, n)
    value = _num(e["value"]) or Decimal(0)
    is_pct = e["kind"] == "pct"
    live_hint = (_("Paused. Customers get “This code isn’t active right now.”") if e["paused"]
                 else _("On. Starts working %(date)s.") % {"date": _date(e["start"])} if key == "scheduled"
                 else _("On, but it ended %(date)s. Change the end date to bring it back.") % {"date": _date(e["end"])} if label == _("Ended")
                 else _("On, but it hit its use limit.") if key == "ended"
                 else _("On. Customers can use it at checkout now."))
    cats = []
    for station in Station.objects.filter(archived=False).order_by("position", "id"):
        dishes = list(Dish.objects.filter(archived=False, station=station).order_by("title"))
        if dishes:
            keys = [f"d{d.id}" for d in dishes]
            cats.append({"id": station.id, "name": station.name, "color": station.color,
                         "all": set(keys) <= set(e["items"]), "n": len(keys),
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
        uses = OrderDiscount.objects.filter(discount_id=e["id"])
        stats["given"] = uses.aggregate(s=Sum("amount"))["s"] or Decimal(0)
        stats["sales"] = uses.aggregate(s=Sum("order__total"))["s"] or Decimal(0)
        stats["n"] = uses.values("order").distinct().count()
    limit = int(e["limit"]) if e["limit"].isdigit() else None
    from events.models import StoreHours
    fee = StoreHours.load().delivery_fee
    return {
        "e": e, "key": str(e["id"]), "err": err, "is_pct": is_pct, "live_hint": live_hint,
        "targets": [(k, label_, desc, icon, color, e["target"] == k) for k, label_, desc, icon, color in TARGETS],
        "cats": cats, "menus": menu_rows,
        "free": is_pct and value >= 100,
        "del_text": (_("Free delivery. The whole delivery fee comes off, and the coupon only works on delivery orders.") if is_pct and value >= 100
                     else _("Takes %(v)s off the delivery fee. Only works on delivery orders.") % {"v": f"{_text(value)}%" if is_pct else _money(value)})
        + ("" if fee else " " + _("The store charges no delivery fee yet: set it on Store hours.")),
        "chans": [(c, label_, c in e["chans"]) for c, label_ in CHANNELS], "chan_lock": e["target"] == "delivery",
        "per": [(k, label_, e["per"] == k) for k, label_, _n in PER],
        "acct": e["per"] != "any" or e["first"],
        "stats": [(_("Times used"), str(n)), (_("Discount given"), _short(stats["given"])), (_("Orders total"), _short(stats["sales"])),
                  (_("Avg. discount"), _money(stats["given"] / stats["n"]) if stats["n"] else "—")],
        "limit_pct": min(100, round(n * 100 / limit)) if limit else None, "limit_full": bool(limit and n >= limit),
        "limit_text": (_("%(n)d of %(limit)d uses, used up") % {"n": n, "limit": limit} if limit and n >= limit
                       else _("%(n)d of %(limit)d uses, %(left)d left") % {"n": n, "limit": limit, "left": limit - n} if limit
                       else _("No use limit.") if n else _("Not used yet.")),
        "del_hint": _("Past orders keep their discount. The code just stops working.") if n else _("Nobody has used it yet."),
        "pv": preview(st, e, value, fee),
    }


def preview(st, e, value, fee) -> dict:
    """A sample order at checkout with this coupon: a few dishes from different stations."""
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
    where = {"pickup": "pickup", "delivery": "delivery"}[mode]
    minimum = _num(e["min"]) if e["min"] else None
    why = ""
    if where not in e["chans"]:
        why = _("Doesn’t apply: this code is for %(where)s orders.") % {
            "where": _(" and ").join(str(label).lower() for c, label in CHANNELS if c in e["chans"])}
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
        sums.append(((e["code"] or "CODE") + (" · " + _("free delivery") if e["target"] == "delivery" and e["kind"] == "pct" and value >= 100 else ""),
                     "−" + _money(off), "green"))
    if tax:
        sums.append((_("Tax"), _money(tax), ""))
    sums.append((_("Total"), _money(sub + fee - off + tax), "big"))
    note = why or (_("Saves %(amount)s on this sample order.") % {"amount": _money(off)}
                   + (" " + _("Capped at %(cap)s.") % {"cap": _money(cap)} if e["kind"] == "pct" and cap and off >= cap else "") if off else "")
    return {"mode": mode, "lines": [(q, d.title, _money(d.price * q), hit and eligible(d)) for d, q in lines], "sums": sums,
            "note": note, "warn": bool(why)}
