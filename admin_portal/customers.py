"""
Customers (design: AdminCustomers): people with store accounts (not staff). One form with Save: the list (search,
sort, filters) and the picked customer's profile: name, email, phone, staff notes, delivery addresses, whether they
can sign in and get marketing emails. Their orders and stats come from their orders; their cards from Square
(refreshed and removed straight away). A changed email isn't switched on Save: the new address gets a link to
confirm it and the old one a heads-up (users/accounts.py). Password reset and verification emails go straight away.

    request.session["admin_customers"] = {"q", "sort", "filter", "sel": user id, "rows": {"<id>": row}, "confirm": card id,
        "cards_at": {"<id>": iso}}
    row = {"first", "last", "email", "phone", "notes", "active", "marketing", "addrs": [{"id", "label", "line", "note", "def"}]}
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext

from pos_server.models import Order
from users.models import Customer, SavedAddress, SavedCard

from .orders import status_of
from .team import face, full_name, initials, when

KEY = "admin_customers"
FILTERS = [("all", _l("All")), ("month", _l("Ordered this month")), ("never", _l("No orders")), ("marketing", _l("Gets emails")),
           ("off", _l("Signed out"))]
SORTS = [("recent", _l("Last order")), ("name", _l("Name")), ("orders", _l("Most orders")), ("spend", _l("Top spend")), ("new", _l("Newest"))]
CHANNELS = {"store": (_l("In store"), "#12875A"), "web": (_l("Pickup"), "#C9821A"), "delivery": (_l("Delivery"), "#4E79B5")}
BRANDS = {"visa": ("VISA", "#1A1F71"), "mastercard": ("MC", "#EB001B"), "american_express": ("AMEX", "#2E77BC"), "amex": ("AMEX", "#2E77BC"),
          "interac": ("INTERAC", "#FFB500"), "discover": ("DISC", "#E55C20")}


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def customers():
    """Store accounts (staff are on the Staff page), with their order count, last order and spend."""
    return (User.objects.filter(is_staff=False).select_related("customer_profile")
            .annotate(n_orders=Count("orders", distinct=True), last_order=Max("orders__timestamp"), spend=Sum("orders__total")))


def phone_text(digits) -> str:
    d = str(digits or "")
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else d


def saved_row(user) -> dict:
    p = Customer.objects.get_or_create(user=user)[0]
    return {"first": user.first_name, "last": user.last_name, "email": user.email, "phone": phone_text(p.phone), "notes": p.staff_notes,
            "active": user.is_active, "marketing": p.newsletter,
            "addrs": [{"id": a.id, "label": a.label, "line": ", ".join(filter(None, [a.line, a.unit, a.city])), "note": a.note, "def": a.is_default}
                      for a in user.saved_addresses.all()]}


def state(session) -> dict:
    st = session.get(KEY) or {}
    return {"q": "", "sort": "recent", "filter": "all", "sel": None, "rows": {}, "confirm": None, "cards_at": {}, **st}


def store(session, st):
    session[KEY] = st


def row_of(st, user) -> dict:
    return st["rows"].get(str(user.id)) or saved_row(user)


def take_post(st, post):
    st["q"] = post.get("q", st["q"]).strip()[:60]
    if post.get("sort") in dict(SORTS):
        st["sort"] = post["sort"]
    user = User.objects.filter(pk=_int(post.get("sel")), is_staff=False).first()
    if user is not None:
        st["sel"] = user.id
    if user is None or post.get("profile") != "1":
        return
    row = dict(row_of(st, user))
    for f, size in (("first", 150), ("last", 150), ("email", 120), ("phone", 20), ("notes", 200)):
        if f in post:
            row[f] = post.get(f, "").strip()[:size]
    if "addr_line" in post:
        labels, lines, notes, ids = post.getlist("addr_label"), post.getlist("addr_line"), post.getlist("addr_note"), post.getlist("addr_id")
        old = {str(a["id"]): a for a in row["addrs"]}
        row["addrs"] = [{"id": _int(i) or i, "label": labels[k].strip()[:16] if k < len(labels) else "", "line": lines[k].strip()[:90],
                         "note": notes[k].strip()[:90] if k < len(notes) else "", "def": old.get(str(i), {}).get("def", False)}
                        for k, i in enumerate(ids) if k < len(lines)]
    _keep(st, user, row)


def _keep(st, user, row):
    if row == saved_row(user):
        st["rows"].pop(str(user.id), None)
    else:
        st["rows"][str(user.id)] = row


def apply_op(st, op) -> str:
    name, _sep, arg = op.partition(":")
    user = User.objects.filter(pk=st.get("sel"), is_staff=False).first()
    if name == "pick" and User.objects.filter(pk=_int(arg), is_staff=False).exists():
        st["sel"], st["confirm"] = _int(arg), None
    elif name == "filter" and arg in dict(FILTERS):
        st["filter"] = "all" if st["filter"] == arg else arg
    elif user is None:
        return ""
    elif name == "flip" and arg in ("active", "marketing"):
        row = dict(row_of(st, user))
        row[arg] = not row[arg]
        _keep(st, user, row)
    elif name in ("addr_default", "addr_rm", "addr_add"):
        row = dict(row_of(st, user))
        addrs = [dict(a) for a in row["addrs"]]
        i = _int(arg, -1)
        if name == "addr_add":
            addrs.append({"id": f"n{timezone.now().strftime('%H%M%S%f')}", "label": "", "line": "", "note": "", "def": not addrs})
        elif 0 <= i < len(addrs) and name == "addr_default":
            for k, a in enumerate(addrs):
                a["def"] = k == i
        elif 0 <= i < len(addrs):
            gone = addrs.pop(i)
            if gone["def"] and addrs:
                addrs[0]["def"] = True
        row["addrs"] = addrs
        _keep(st, user, row)
    return ""


def problems(st) -> dict:
    """{user id: message} for the edited customers."""
    out = {}
    drafts = {int(k): r for k, r in st["rows"].items()}
    for uid, r in drafts.items():
        email = r["email"].strip()
        try:
            validate_email(email)
        except ValidationError:
            out[uid] = _("Enter a valid email. It’s what they sign in with.")
            continue
        other = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exclude(pk=uid).first()
        twin = next((k for k, x in drafts.items() if k != uid and x["email"].strip().lower() == email.lower()), None)
        if other or twin:
            who = other or User.objects.get(pk=twin)
            out[uid] = _("%(name)s already uses this email.") % {"name": full_name(who.first_name, who.last_name, who.username)}
        elif any(a["line"].strip() == "" for a in r["addrs"]):
            out[uid] = _("Give every address a street, or remove it.")
    return out


@transaction.atomic
def save(st, request) -> list[str]:
    """Write the edited customers. Returns notes for the toast (an email waiting to be confirmed)."""
    from users import accounts
    notes = []
    for key, r in st["rows"].items():
        user = User.objects.filter(pk=int(key), is_staff=False).first()
        if user is None:
            continue
        user.first_name, user.last_name, user.is_active = r["first"], r["last"], r["active"]
        user.save(update_fields=["first_name", "last_name", "is_active"])
        p = accounts.profile_of(user)
        digits = "".join(c for c in r["phone"] if c.isdigit())
        p.phone, p.staff_notes, p.newsletter = (int(digits) if digits else None), r["notes"], r["marketing"]
        p.save(update_fields=["phone", "staff_notes", "newsletter"])
        keep = []
        for a in r["addrs"]:
            addr = SavedAddress.objects.filter(pk=a["id"], user=user).first() if isinstance(a["id"], int) else None
            addr = addr or SavedAddress(user=user)
            if addr.pk is None or a["line"] != ", ".join(filter(None, [addr.line, addr.unit, addr.city])):
                addr.line, addr.unit, addr.city = a["line"], "", ""
            addr.label, addr.note, addr.is_default = a["label"] or _("Address"), a["note"], a["def"]
            addr.save()
            keep.append(addr.pk)
        user.saved_addresses.exclude(pk__in=keep).delete()
        if r["email"].strip().lower() != (user.email or "").lower():
            accounts.change_email(request, user, r["email"].strip())
            notes.append(_("%(name)s confirms %(email)s from their inbox before it takes over.") % {"name": user.first_name or user.username, "email": r["email"].strip()})
    st["rows"] = {}
    return notes


# ---------- straight away ----------

def refresh_cards(st, user) -> str:
    """Bring their Square cards in: new ones added, gone ones removed, details updated."""
    from payments import square
    p = Customer.objects.get_or_create(user=user)[0]
    if not p.square_customer_id:
        st["cards_at"][str(user.id)] = timezone.now().isoformat()
        return _("No cards in Square yet. They add one at checkout.")
    try:
        cards = square.list_cards(p.square_customer_id)
    except Exception:
        return _("Square couldn’t be reached. Try again in a minute.")
    seen = []
    for c in cards:
        card, _created = SavedCard.objects.update_or_create(user=user, square_card_id=c["id"], defaults={
            "square_customer_id": p.square_customer_id, "brand": (c["brand"] or "").lower(), "last4": c["last4"],
            "exp_month": c["exp_month"], "exp_year": c["exp_year"]})
        seen.append(card.pk)
    user.saved_cards.exclude(pk__in=seen).exclude(square_card_id__startswith="demo:").delete()
    if user.saved_cards.exists() and not user.saved_cards.filter(is_default=True).exists():
        first = user.saved_cards.order_by("created_at").first()
        first.is_default = True
        first.save(update_fields=["is_default"])
    st["cards_at"][str(user.id)] = timezone.now().isoformat()
    return ""


def remove_card(st, user, card_id) -> str:
    """Two presses: the first asks, the second disables it in Square and forgets it."""
    from payments import square
    card = user.saved_cards.filter(pk=card_id).first()
    if card is None:
        return ""
    if st.get("confirm") != card.id:
        st["confirm"] = card.id
        return ""
    st["confirm"] = None
    if not card.square_card_id.startswith("demo:"):
        try:
            square.disable_card(card.square_card_id)
        except Exception:
            return _("Square couldn’t remove that card. Try again in a minute.")
    was_default = card.is_default
    label = card.label
    card.delete()
    if was_default:
        nxt = user.saved_cards.first()
        if nxt:
            nxt.is_default = True
            nxt.save(update_fields=["is_default"])
    return _("%(card)s removed. It can’t be charged any more.") % {"card": label}


# ---------- the page ----------

def _rel(dt) -> str:
    if not dt:
        return ""
    days = (timezone.localdate() - timezone.localtime(dt).date()).days
    if days <= 0:
        return _("Today")
    if days == 1:
        return _("Yesterday")
    if days < 7:
        return ngettext("%(n)d day ago", "%(n)d days ago", days) % {"n": days}
    return date_format(timezone.localtime(dt), "M j" if timezone.localtime(dt).year == timezone.localdate().year else "M j, Y")


def _money(value) -> str:
    return f"${Decimal(value or 0):,.2f}"


def page(st, request) -> dict:
    everyone = list(customers())
    errs = problems(st)
    dirty = bool(st["rows"])
    if errs:
        bad = User.objects.get(pk=next(iter(errs)))
        r = row_of(st, bad)
        status, kind = _("Fix %(name)s’s profile to save") % {"name": full_name(r["first"], r["last"], bad.username)}, "bad"
    else:
        status, kind = (_("Unsaved changes"), "dirty") if dirty else (_("All changes saved"), "")
    month_ago = timezone.now() - datetime.timedelta(days=30)

    def keep(u, key):
        r = row_of(st, u) if str(u.id) in st["rows"] else None
        active = r["active"] if r else u.is_active
        marketing = r["marketing"] if r else u.customer_profile.newsletter
        return {"all": True, "month": bool(u.last_order and u.last_order >= month_ago), "never": not u.n_orders,
                "marketing": marketing, "off": not active}[key]

    filters = [(k, label, sum(keep(u, k) for u in everyone), st["filter"] == k) for k, label in FILTERS]
    q = st["q"].lower()
    digits = "".join(c for c in st["q"] if c.isdigit())
    shown = [u for u in everyone if keep(u, st["filter"]) and (
        not q or q in f"{u.first_name} {u.last_name} {u.email}".lower()
        or (len(digits) >= 3 and digits in str(u.customer_profile.phone or "")))]
    far = timezone.make_aware(datetime.datetime(1970, 1, 1))
    key = {"recent": lambda u: -(u.last_order or far).timestamp(), "name": lambda u: full_name(u.first_name, u.last_name, u.username).lower(),
           "orders": lambda u: -u.n_orders, "spend": lambda u: -(u.spend or 0), "new": lambda u: -u.date_joined.timestamp()}[st["sort"]]
    shown.sort(key=key)
    if st["sel"] not in {u.id for u in everyone}:
        st["sel"] = shown[0].id if shown else None
    rows = []
    for u in shown[:300]:
        r = row_of(st, u)
        rows.append({"u": u, "on": u.id == st["sel"], "name": full_name(r["first"], r["last"], u.username), "email": r["email"],
                     "ini": initials(r["first"], r["last"], u.username), "face": face(u.id), "off": not r["active"],
                     "orders": ngettext("%(n)d order", "%(n)d orders", u.n_orders) % {"n": u.n_orders} if u.n_orders else _("No orders"),
                     "last": _rel(u.last_order) if u.n_orders else _("Joined %(date)s") % {"date": date_format(timezone.localtime(u.date_joined), "M j")}})
    joined = sum(u.date_joined >= month_ago for u in everyone)
    p = profile(st, next((u for u in everyone if u.id == st["sel"]), None), errs, request)
    return {"st": st, "status": status, "status_kind": kind, "can_save": dirty and not errs, "rows": rows, "p": p, "filters": filters,
            "sorts": [(k, label, k == st["sort"]) for k, label in SORTS], "none": not everyone,
            "foot_left": ngettext("%(n)d customer", "%(n)d customers", len(everyone)) % {"n": len(everyone)},
            "foot_right": ngettext("%(n)d joined in the last 30 days", "%(n)d joined in the last 30 days", joined) % {"n": joined}}


def profile(st, u, errs, request):
    if u is None:
        return None
    from users.access import can_page
    r = row_of(st, u)
    cp = u.customer_profile
    orders = list(Order.objects.filter(customer=u).annotate(refunded=Sum("refunds__amount")).prefetch_related("items").order_by("-timestamp"))
    n = len(orders)
    spend = sum((o.total - (o.refunded or 0) for o in orders if not o.voided_at), Decimal(0))
    kept = [o for o in orders if not o.voided_at and status_of(o) != "refunded"]
    channels = {}
    tally = {}
    for o in orders:
        channels[o.channel] = channels.get(o.channel, 0) + 1
        for i in o.items.all():
            if i.parent_id is None:
                tally[i.title] = tally.get(i.title, 0) + i.quantity
    top = max(channels, key=channels.get) if channels else None
    fav = sorted(tally, key=tally.get, reverse=True)[:3]
    recent = []
    for o in orders[:5]:
        st_key = status_of(o)
        state_label = (_("Refunded") if st_key == "refunded" else _("Part refunded") if st_key == "partial" else _("Voided") if st_key == "voided"
                       else _("Completed") if o.picked_up else _("Preparing"))
        items = {}
        for i in o.items.all():
            if i.parent_id is None:
                items[i.title] = items.get(i.title, 0) + i.quantity
        recent.append({"o": o, "date": _rel(o.timestamp), "ch": CHANNELS.get(o.channel, (o.channel, "#5B6A62")),
                       "items": ", ".join((f"{q}× " if q > 1 else "") + t for t, q in items.items()), "total": _money(o.total),
                       "state": state_label, "tone": "bad" if st_key in ("refunded", "voided") else "warn" if state_label == _("Preparing") else ""})
    today = timezone.localdate()
    cards = []
    for c in u.saved_cards.all():
        short, color = BRANDS.get((c.brand or "").lower(), ((c.brand or "CARD")[:4].upper(), "#5B6A62"))
        expired = bool(c.exp_year and (c.exp_year, c.exp_month or 12) < (today.year, today.month))
        soon = bool(c.exp_year and not expired and (c.exp_year * 12 + (c.exp_month or 12)) - (today.year * 12 + today.month) <= 3)
        exp = f"{c.exp_month:02d}/{str(c.exp_year)[-2:]}" if c.exp_year and c.exp_month else ""
        cards.append({"c": c, "short": short, "color": color, "default": c.is_default, "confirm": st.get("confirm") == c.id,
                      "exp": (_("Expired %(d)s") if expired else _("Expires soon, %(d)s") if soon else _("Expires %(d)s")) % {"d": exp} if exp else "",
                      "tone": "bad" if expired else "warn" if soon else ""})
    at = st["cards_at"].get(str(u.id))
    changed_email = r["email"].strip().lower() != (u.email or "").lower() and u.id not in errs
    return {
        "u": u, "r": r, "ini": initials(r["first"], r["last"], u.username), "face": face(u.id),
        "since": _("Since %(date)s") % {"date": date_format(timezone.localtime(u.date_joined), "M Y")}, "err": errs.get(u.id, ""),
        "email_note": _("Saving sends a confirmation to the new address, and a heads-up to the old one. They sign in with the new email after confirming.") if changed_email
        else (_("Waiting for %(email)s to confirm it.") % {"email": cp.pending_email} if cp.pending_email else ""),
        "stats": [(_("Orders"), str(n), _("Mostly %(how)s") % {"how": str(CHANNELS[top][0]).lower()} if top in CHANNELS else _("None yet")),
                  (_("Lifetime spend"), _money(spend) if n else "$0", _("After refunds") if n else "—"),
                  (_("Average order"), _money(spend / len(kept)) if kept else "—", ngettext("Across %(n)d order", "Across %(n)d orders", n) % {"n": n} if n else "—"),
                  (_("Last order"), _rel(orders[0].timestamp) if n else "—", f"#{orders[0].id}" if n else _("Never ordered"))],
        "fav": _("Usually gets %(items)s") % {"items": ", ".join(fav)} if fav else _("Nothing ordered yet"),
        "all_orders": (_("All %(n)d orders") % {"n": n}) if n > 5 else _("Open in Orders"), "orders_q": u.email,
        "recent": recent, "cards": cards,
        "cards_sub": _("From Square · updated %(when)s") % {"when": when(datetime.datetime.fromisoformat(at)).lower()} if at else _("Cards they saved at checkout, kept in Square"),
        "switches": [("active", _("Can sign in"), r["active"], _("Turn off to lock the account. Orders stay.") if r["active"] else _("Locked. They can still order as a guest.")),
                     ("marketing", _("Marketing emails"), r["marketing"], _("They opted in to news and offers.") if r["marketing"] else _("Off. Only order receipts are sent."))],
        "facts": [(_("Joined"), date_format(timezone.localtime(u.date_joined), "M j, Y"), ""),
                  (_("Last signed in"), when(u.last_login) if u.last_login else "—", ""),
                  (_("Email"), _("Verified") if cp.email_verified else _("Not verified"), "" if cp.email_verified else "warn")],
        "unverified": not cp.email_verified, "can_staff": can_page(request.user, "staff", "edit"),
    }
