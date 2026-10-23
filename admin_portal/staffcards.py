"""
Staff cards (design: AdminStaffCards): card types (gift_cards.StaffCardType) and the cards issued to staff. One form
with Save for the types: the list, and the picked type's name, colour, kind (balance or discount), whether cards work,
its balance and refills or its discount, rules and where it works. Issuing a card (a new printed one, or a
pre-printed blank scanned in) and revoking one happen straight away, since they're physical cards. How cards are
used is gift_cards/staff_cards.py.

    request.session["admin_staffcards"] = {"rows": [{"id": 3 or "n…", "name", "color", "kind", "live", "amount", "refill",
        "dow", "dom", "roll", "cap", "dkind", "dval", "dcap", "uses", "own", "no_alcohol", "stack", "chans"}], "sel": id,
        "gone": [ids], "issue_to": user id or None, "how": "print"|"scan", "blank": ""}
"""
import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext

from gift_cards import staff_cards
from gift_cards.models import GiftCard, StaffCard, StaffCardType

from .team import face, full_name, initials, when

KEY = "admin_staffcards"
COLORS = [("#12875A", _l("Green")), ("#4E79B5", _l("Blue")), ("#C2453B", _l("Red")), ("#C9821A", _l("Amber")), ("#7A5AC8", _l("Violet")),
          ("#2BA3A0", _l("Teal"))]
KIND_ICONS = {"balance": "M3 7h18v10H3z M3 11h18 M7 15h4", "discount": "M19 5 5 19 M7 7h.01 M17 17h.01"}
CHANNELS = [("store", _l("In store")), ("pickup", _l("Pickup")), ("delivery", _l("Delivery"))]
WEEKDAYS = [_l("Monday"), _l("Tuesday"), _l("Wednesday"), _l("Thursday"), _l("Friday"), _l("Saturday"), _l("Sunday")]


def _num(value):
    try:
        return Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value) -> str:
    """12.50 -> "12.5", 12.00 -> "12" (what the fields show)."""
    if value is None:
        return ""
    return format(Decimal(value).normalize(), "f")


def row_of(t: StaffCardType) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color, "kind": t.kind, "live": t.live, "amount": _text(t.amount), "refill": t.refill,
            "dow": t.refill_weekday, "dom": t.refill_day, "roll": t.leftover, "cap": _text(t.cap), "dkind": t.discount_kind,
            "dval": _text(t.discount_value), "dcap": _text(t.discount_cap), "uses": t.uses, "own": t.own_only,
            "no_alcohol": t.no_alcohol, "stack": t.stack, "chans": [c for c, _l in CHANNELS if c in (t.channels or [])]}


def saved() -> list:
    return [row_of(t) for t in StaffCardType.objects.filter(archived=False)]


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        rows = saved()
        st = {"rows": rows, "sel": rows[0]["id"] if rows else None, "gone": [], "issue_to": None, "how": "print", "blank": ""}
    return st


def selected(st):
    return next((r for r in st["rows"] if str(r["id"]) == str(st["sel"])), st["rows"][0] if st["rows"] else None)


def take_post(st, post):
    if "issue_to" in post:
        st["issue_to"] = int(post["issue_to"]) if post.get("issue_to", "").isdigit() else None
    st["blank"] = post.get("blank", st.get("blank", "")).strip()[:32]
    e = selected(st)
    if not e or post.get("editing") != str(e["id"]):
        return
    for f, size in (("name", 30), ("amount", 8), ("cap", 8), ("dval", 8), ("dcap", 8)):
        if f in post:
            e[f] = post.get(f, "").strip()[:size]
    for f, choices in (("refill", ("day", "week", "month", "never")), ("dom", ("1", "15", "last")), ("uses", ("any", "day", "week"))):
        if post.get(f) in choices:
            e[f] = post[f]
    if post.get("dow", "").isdigit() and int(post["dow"]) < 7:
        e["dow"] = int(post["dow"])


def apply_op(st, op) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    if name == "pick":
        found = next((r for r in st["rows"] if str(r["id"]) == arg), None)
        if found:
            st["sel"], st["issue_to"], st["blank"] = found["id"], None, ""
    elif name == "new":
        used = {r["color"] for r in st["rows"]}
        row = {"id": f"n{timezone.now().strftime('%H%M%S%f')}", "name": _("New card"), "color": next((c for c, _l in COLORS if c not in used), "#7A5AC8"),
               "kind": "balance", "live": False, "amount": "10", "refill": "day", "dow": 0, "dom": "1", "roll": "reset", "cap": "50",
               "dkind": "pct", "dval": "20", "dcap": "", "uses": "any", "own": True, "no_alcohol": True, "stack": False, "chans": ["store", "pickup"]}
        st["rows"].append(row)
        st["sel"], st["issue_to"] = row["id"], None
    elif not e:
        return ""
    elif name == "color" and arg in dict(COLORS):
        e["color"] = arg
    elif name == "kind" and arg in ("balance", "discount"):
        e["kind"] = arg
    elif name == "roll" and arg in ("reset", "carry"):
        e["roll"] = arg
    elif name == "dkind" and arg in ("pct", "amt"):
        e["dkind"] = arg
    elif name == "flip" and arg in ("live", "own", "no_alcohol", "stack"):
        e[arg] = not e[arg]
    elif name == "chan" and arg in dict(CHANNELS):
        chans = [c for c in e["chans"] if c != arg] if arg in e["chans"] else e["chans"] + [arg]
        if chans:
            e["chans"] = [c for c, _l in CHANNELS if c in chans]
    elif name == "how" and arg in ("print", "scan"):
        st["how"] = arg
    elif name == "delete":
        st["rows"] = [r for r in st["rows"] if r is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = st["rows"][0]["id"] if st["rows"] else None
        return _("%(name)s deleted. Save to keep it.") % {"name": e["name"] or _("Card")}
    return ""


def errors(r) -> dict:
    """{"name"|"balance"|"discount": message} for one card type."""
    out = {}
    if r["kind"] == "balance":
        amount, cap = _num(r["amount"]), _num(r["cap"])
        if not amount or amount <= 0:
            out["balance"] = _("Enter a balance.")
        elif r["roll"] == "carry" and r["refill"] != "never" and not (cap and cap >= amount):
            out["balance"] = _("The limit has to be at least $%(amount)s.") % {"amount": _text(amount)}
    else:
        value, cap = _num(r["dval"]), _num(r["dcap"]) if r["dcap"] else Decimal(0)
        if not value or value <= 0:
            out["discount"] = _("Enter how much it takes off.")
        elif r["dkind"] == "pct" and value > 100:
            out["discount"] = _("Percent off can’t be more than 100.")
        elif cap is None or cap < 0:
            out["discount"] = _("Enter the most it takes off, or leave it empty.")
    return out


def problems(st) -> dict:
    out = {}
    names = [r["name"].strip().lower() for r in st["rows"]]
    taken = set(n.lower() for n in StaffCardType.objects.filter(archived=True).values_list("name", flat=True))
    for r in st["rows"]:
        errs = errors(r)
        if not r["name"].strip():
            errs["name"] = _("Give the card a name.")
        elif names.count(r["name"].strip().lower()) > 1:
            errs["name"] = _("Another card type already has this name.")
        elif not isinstance(r["id"], int) and r["name"].strip().lower() in taken:
            errs["name"] = _("A deleted card type had this name. Pick another.")
        if errs:
            out[str(r["id"])] = errs
    return out


def is_dirty(st) -> bool:
    return bool(st["gone"]) or st["rows"] != saved()


@transaction.atomic
def save(st):
    now = timezone.now()
    for t in StaffCardType.objects.filter(pk__in=st["gone"]):
        t.archived = True
        t.name = f"{t.name} ({t.id})"[:30]  # frees the name
        t.save(update_fields=["archived", "name"])
        t.cards.filter(revoked_at__isnull=True).update(revoked_at=now)
    for position, r in enumerate(st["rows"]):
        t = StaffCardType.objects.get(pk=r["id"]) if isinstance(r["id"], int) else StaffCardType()
        t.name, t.color, t.kind, t.live, t.position = r["name"].strip(), r["color"], r["kind"], r["live"], position
        t.amount, t.refill, t.refill_weekday, t.refill_day = _num(r["amount"]) or t.amount, r["refill"], r["dow"], r["dom"]
        t.leftover, t.cap = r["roll"], _num(r["cap"]) or t.cap
        t.discount_kind, t.discount_value = r["dkind"], _num(r["dval"]) or t.discount_value
        t.discount_cap = _num(r["dcap"]) if r["dcap"] else None
        t.uses, t.own_only, t.no_alcohol, t.stack, t.channels = r["uses"], r["own"], r["no_alcohol"], r["stack"], list(r["chans"])
        t.save()


# ---------- issuing and revoking (straight away) ----------

def issue(st, me) -> tuple[str, str]:
    """(message, error) for issuing the picked type to st["issue_to"]."""
    e = selected(st)
    if not e or not isinstance(e["id"], int):
        return "", _("Save the card type before issuing cards.")
    t = StaffCardType.objects.filter(pk=e["id"], archived=False).first()
    person = User.objects.filter(pk=st.get("issue_to"), is_staff=True, is_active=True).first()
    if t is None or person is None:
        return "", _("Pick who gets the card.")
    if t.cards.filter(user=person, revoked_at__isnull=True).exists():
        return "", _("%(name)s already has a %(card)s card.") % {"name": person.first_name or person.username, "card": t.name}
    number = st.get("blank", "") if st.get("how") == "scan" else StaffCard.new_number()
    if st.get("how") == "scan":
        if not re.fullmatch(r"[A-Za-z0-9-]{6,32}", number):
            return "", _("Scan the blank card, or type its number (letters, digits and dashes).")
        if StaffCard.objects.filter(number=number).exists() or GiftCard.objects.filter(number=number).exists():
            return "", _("That card number is already in use.")
    StaffCard.objects.create(type=t, user=person, number=number, balance=t.amount if t.kind == t.BALANCE else 0, issued_by=me)
    st["issue_to"], st["blank"] = None, ""
    first = person.first_name or person.username
    if st.get("how") == "scan":
        return _("Card •• %(last4)s linked to %(name)s. It works right away.") % {"last4": number[-4:], "name": first}, ""
    return _("%(name)s’s card is ready. Print it from their row.") % {"name": first}, ""


def revoke(card_id) -> str:
    card = StaffCard.objects.filter(pk=card_id, revoked_at__isnull=True).select_related("user").first()
    if card is None:
        return ""
    card.revoked_at = timezone.now()
    card.save(update_fields=["revoked_at"])
    return _("%(name)s’s card •• %(last4)s revoked. It stops working right away.") % {"name": card.user.first_name or card.user.username,
                                                                                       "last4": card.number[-4:]}


# ---------- the page ----------

def _money(value) -> str:
    return f"${Decimal(value):,.2f}"


def _short(value) -> str:
    return _money(value).replace(".00", "")


def summary(r) -> str:
    if r["kind"] == "balance":
        amount = _short(_num(r["amount"]) or 0)
        if r["refill"] == "never":
            return _("%(amount)s one time") % {"amount": amount}
        return f"{amount} {_refill_text(r)}"
    value = _num(r["dval"]) or 0
    off = _("%(n)s%% off") % {"n": _text(value)} if r["dkind"] == "pct" else _("%(amount)s off") % {"amount": _short(value)}
    return off + {"any": "", "day": ", " + _("once a day"), "week": ", " + _("once a week")}[r["uses"]]


def _type_of(r) -> StaffCardType:
    return StaffCardType(kind=r["kind"], refill=r["refill"], refill_weekday=r["dow"], refill_day=r["dom"])


def _refill_text(r) -> str:
    return staff_cards.refill_text(_type_of(r))


def page(st, me) -> dict:
    errs = problems(st)
    dirty = is_dirty(st)
    if errs:
        bad = next(r for r in st["rows"] if str(r["id"]) in errs)
        status, kind = _("Fix %(name)s to save") % {"name": bad["name"].strip() or _("the new card")}, "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    live_cards = StaffCard.objects.filter(revoked_at__isnull=True, type__archived=False).select_related("user", "type")
    by_type = {}
    for card in live_cards:
        by_type.setdefault(card.type_id, []).append(card)
    e = selected(st)
    rows = []
    for r in st["rows"]:
        n = len(by_type.get(r["id"], []))
        big = _short(_num(r["amount"]) or 0) if r["kind"] == "balance" else (
            f"{_text(_num(r['dval']) or 0)}%" if r["dkind"] == "pct" else _short(_num(r["dval"]) or 0))
        rows.append({"r": r, "on": r is e, "name": r["name"] or _("Untitled"), "sub": summary(r), "big": big,
                     "holders": ngettext("%(n)d card issued", "%(n)d cards issued", n) % {"n": n}})
    month_used = sum((staff_cards.month_stats(t)["used"] for t in StaffCardType.objects.filter(archived=False)), Decimal(0))
    issued = len(live_cards)
    d = None
    if e:
        d = editor(st, e, errs.get(str(e["id"]), {}), by_type.get(e["id"], []))
    return {"st": st, "status": status, "status_kind": kind, "can_save": dirty and not errs, "rows": rows, "d": d,
            "foot_left": ngettext("%(n)d card issued", "%(n)d cards issued", issued) % {"n": issued},
            "foot_right": _("%(amount)s used this month") % {"amount": _short(month_used)}}


def editor(st, e, err, cards) -> dict:
    is_bal = e["kind"] == "balance"
    amount = _num(e["amount"]) or Decimal(0)
    now = timezone.now()
    t = StaffCardType.objects.filter(pk=e["id"]).first() if isinstance(e["id"], int) else None
    holders = []
    for card in cards:
        if is_bal and t:
            staff_cards.refill(card, now)
        u = card.user
        nxt = ""
        if is_bal:
            if e["refill"] == "never":
                nxt = _("Used up") if card.balance <= 0 else _("of %(amount)s") % {"amount": _money(amount)}
            else:
                day = staff_cards.next_refill(_type_of(e), now)
                nxt = _("Refills %(day)s") % {"day": date_format(day, "D, M j")} if day else ""
        holders.append({"card": card, "name": full_name(u.first_name, u.last_name, u.username), "ini": initials(u.first_name, u.last_name, u.username),
                        "face": face(u.id), "off": not u.is_active,
                        "sub": _("Card •• %(last4)s · last used %(when)s") % {"last4": card.number[-4:], "when": when(card.last_used).lower() if card.last_used else _("never")}
                        + ("" if u.is_active else " · " + _("can’t sign in")),
                        "bal": _money(card.balance) if is_bal else "—", "empty": is_bal and card.balance <= 0, "next": nxt})
    have = {c.user_id for c in cards}
    avail = [u for u in User.objects.filter(is_staff=True, is_active=True).order_by("first_name", "last_name", "id") if u.id not in have]
    stats = staff_cards.month_stats(t) if t else {"used": Decimal(0), "orders": 0}
    dval = _num(e["dval"]) or Decimal(0)
    return {
        "e": e, "key": str(e["id"]), "err": err, "saved": t is not None, "is_bal": is_bal, "kind_icons": KIND_ICONS,
        "colors": [(c, label, c == e["color"]) for c, label in COLORS],
        "kinds": [("balance", _("Balance card"), _("Holds money to spend, like a gift card. Can top back up on a schedule."), KIND_ICONS["balance"]),
                  ("discount", _("Discount card"), _("Takes a percent or amount off their order, like a coupon."), KIND_ICONS["discount"])],
        "live_hint": _("On. Scanning a card applies it.") if e["live"] else _("Off. Cards are declined at the register and online."),
        "refills": [("day", _("Every day")), ("week", _("Every week")), ("month", _("Every month")), ("never", _("Never, one-time balance"))],
        "weekdays": list(enumerate(WEEKDAYS)), "doms": [("1", _("1st")), ("15", _("15th")), ("last", _("Last day"))],
        "rolls": [("reset", _("Resets to %(amount)s") % {"amount": _short(amount)}, _("Unused money doesn’t carry over.")),
                  ("carry", _("Carries over"), _("Adds %(amount)s on top, up to a limit.") % {"amount": _short(amount)})],
        "bal_text": balance_text(e, amount),
        "uses": [("any", _("Every order")), ("day", _("Once a day")), ("week", _("Once a week"))],
        "rules": [("own", _("Only for their own order"), _("The order goes under their name.") if e["own"] else _("Can be used on any order they ring up.")),
                  ("no_alcohol", _("No alcohol"), _("Dishes made with alcohol aren’t covered.") if e["no_alcohol"] else _("Covers everything, alcohol included.")),
                  ("stack", _("Works with other discounts"), _("Adds to coupons and discounts.") if e["stack"] else _("If another discount applies, the better one wins."))],
        "chans": [(c, label, c in e["chans"]) for c, label in CHANNELS],
        "hold_head": ngettext("%(n)d card issued", "%(n)d cards issued", len(cards)) % {"n": len(cards)} if cards else _("Issued cards"),
        "hold_sub": _("Balances right now.") if is_bal else _("Each staff member gets their own card number."),
        "holders": holders, "avail": [(u.id, full_name(u.first_name, u.last_name, u.username)) for u in avail],
        "issue_off": not t or not st.get("issue_to") or st.get("issue_to") not in {u.id for u in avail},
        "stats": [(_("Used"), _short(stats["used"])), (_("Orders"), str(stats["orders"]))],
        "del_text": (ngettext("Delete, and revoke %(n)d card", "Delete, and revoke %(n)d cards", len(cards)) % {"n": len(cards)}) if cards else _("Delete card type"),
        "pv": preview(e, amount, dval, cards[0] if cards else None),
    }


def balance_text(e, amount) -> str:
    cap = _num(e["cap"]) or Decimal(0)
    if e["refill"] == "never":
        return _("Starts at %(amount)s and never refills. Once it’s spent, the card stops working.") % {"amount": _money(amount)}
    keep = (_("Leftover money stays, up to %(cap)s.") % {"cap": _money(cap)}) if e["roll"] == "carry" else _("Leftover money doesn’t carry over.")
    return _("Refills %(when)s at midnight. %(keep)s") % {"when": _refill_text(e), "keep": keep}


def preview(e, amount, dval, card) -> dict:
    """The card and what it does to a sample order at the register."""
    from pos_server.models import Dish
    is_bal = e["kind"] == "balance"
    dishes = list(Dish.objects.filter(archived=False).order_by("price")[:3])
    boozy = staff_cards.alcoholic_dish_ids([d.id for d in dishes]) if e["no_alcohol"] else set()
    lines = [{"name": d.title, "price": _money(d.price), "out": d.id in boozy} for d in dishes]
    sub = sum((d.price for d in dishes), Decimal(0))
    covered = sum((d.price for d in dishes if d.id not in boozy), Decimal(0))
    if is_bal:
        off = min(card.balance if card else amount, covered)
    else:
        off = covered * dval / 100 if e["dkind"] == "pct" else min(dval, covered)
        cap = _num(e["dcap"]) if e["dcap"] else None
        if e["dkind"] == "pct" and cap:
            off = min(off, cap)
    off = off.quantize(Decimal("0.01"))
    who = card.user.first_name if card else _("Staff")
    name = e["name"] or _("Staff card")
    if not e["live"]:
        note = _("Turned off, so the register would decline this card.")
    elif is_bal:
        note = (_("%(name)s has %(left)s left after this.") % {"name": who, "left": _money(max(Decimal(0), (card.balance if card else amount) - off))}
                + (" " + _("Alcohol is never covered.") if e["no_alcohol"] else ""))
    else:
        cap = _num(e["dcap"]) if e["dcap"] else None
        note = (_("Capped at %(cap)s per order.") % {"cap": _money(cap)} if e["dkind"] == "pct" and cap and off >= cap
                else _("Takes %(amount)s off this order.") % {"amount": _money(off)})
    return {"big": _short(amount) if is_bal else (f"{_text(dval)}%" if e["dkind"] == "pct" else _short(dval)),
            "name": name, "rule": (_("One-time balance") if e["refill"] == "never" else _("Refills %(when)s") % {"when": _refill_text(e)}) if is_bal
            else (_("Off their order") if e["dkind"] == "pct" else _("Off each order")),
            "scanned": _("Scanned %(card)s · %(name)s") % {"card": name, "name": who} + (f" •• {card.number[-4:]}" if card else ""),
            "lines": lines, "sub": _money(sub), "off": "−" + _money(off), "rest": _money(sub - off),
            "rest_label": _("Left to pay") if is_bal else _("Total before tax"), "note": note, "live": e["live"]}
