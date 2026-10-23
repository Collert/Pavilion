"""
Gift cards (design: AdminGiftCards): every gift card, what's owed on them, and what staff do to one (gift_cards/
giftcards.py). Not a Save form: each action happens straight away. The stats, the list (With balance, Empty, All;
searched by last 4, name or email), the picked card (its look, facts, message, resend, freeze, adjust, history) and
the Issue dialog, whose last step shows the new card's number once: it's rendered into that response only and
never stored.

    request.session["admin_giftcards"] = {"tab": "active"|"empty"|"all", "q", "sel": card id,
        "adj": {"dir": "add"|"remove", "amt", "why"}, "issue": None or {"design", "amt", "other", "name", "email",
        "msg", "why", "send": "email"|"show"}}
"""
import datetime
import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext

from gift_cards import giftcards as gc
from gift_cards.models import GiftCard, GiftCardEvent

KEY = "admin_giftcards"
TABS = [("active", _l("With balance")), ("empty", _l("Empty")), ("all", _l("All"))]
WHY = [_l("Make up for a problem"), _l("Birthday treat"), _l("Giveaway or contest"), _l("Staff thank-you"), _l("Donation or charity"), _l("Other")]
SOURCE = {GiftCard.ONLINE: (_l("Online"), "online"), GiftCard.REGISTER: (_l("Register"), "pos"), GiftCard.STAFF: (_l("Issued"), "staff")}
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _money(value) -> str:
    return f"${Decimal(value or 0):,.2f}"


def _num(value):
    try:
        return Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _date(d, year=False) -> str:
    if isinstance(d, datetime.datetime):
        d = timezone.localtime(d).date()
    return date_format(d, "M j, Y" if year or d.year != timezone.localdate().year else "M j")


def _rel(d) -> str:
    n = (timezone.localdate() - timezone.localtime(d).date()).days
    return _("Today") if n == 0 else _("Yesterday") if n == 1 else (ngettext("%(n)d day ago", "%(n)d days ago", n) % {"n": n}) if n < 7 else _date(d)


def state(session) -> dict:
    return session.get(KEY) or {"tab": "active", "q": "", "sel": None, "adj": {"dir": "add", "amt": "", "why": ""}, "issue": None}


def blank_issue() -> dict:
    design = gc.designs_on_sale().first()
    return {"design": design.id if design else None, "amt": (design.amounts[:1] or [25])[0] if design and design.amounts else 25, "other": "",
            "name": "", "email": "", "msg": "", "why": str(WHY[0]), "send": "email"}


def take_post(st, post):
    if "q" in post:
        st["q"] = post.get("q", "").strip()[:60]
    if "adj_amt" in post:
        st["adj"].update(amt=post.get("adj_amt", "").strip()[:9], why=post.get("adj_why", "").strip()[:60])
    m = st.get("issue")
    if m is not None and "iss_name" in post:
        other = post.get("iss_other", "").strip()[:5]
        if other and other != m["other"]:  # typing another amount picks it
            m["amt"] = "other"
        m.update(name=post.get("iss_name", "").strip()[:60], email=post.get("iss_email", "").strip()[:80],
                 msg=post.get("iss_msg", "").strip()[:120], other=other)
        if post.get("iss_why") in [str(w) for w in WHY]:
            m["why"] = post["iss_why"]


def issue_amount(m):
    if m["amt"] == "other":
        value = _num(m["other"])
        return value if value is not None and value == value.to_integral_value() else None
    return Decimal(m["amt"])


def issue_errors(m) -> dict:
    out = {}
    amount = issue_amount(m)
    if amount is None or amount <= 0 or amount > 1000:
        out["amt"] = _("Pick an amount (whole dollars, up to $1,000)")
    if not m["name"]:
        out["name"] = _("Who is it for?")
    if m["email"] and not EMAIL.match(m["email"]):
        out["email"] = _("That doesn’t look like an email.")
    elif m["send"] == "email" and not m["email"]:
        out["email"] = _("Add an email to send it, or choose to show the code.")
    return out


def apply_op(st, op, request) -> tuple[str, dict | None]:
    """Run a button. (toast, reveal): `reveal` is the new card to show once, only after issuing."""
    name, _sep, arg = op.partition(":")
    user = request.user
    card = GiftCard.objects.filter(pk=st["sel"]).first() if st.get("sel") else None
    if name == "pick" and arg.isdigit():
        st.update(sel=int(arg), adj={"dir": "add", "amt": "", "why": ""})
    elif name == "tab" and arg in dict(TABS):
        st["tab"] = arg
    elif name == "dir" and arg in ("add", "remove"):
        st["adj"]["dir"] = arg
    elif name == "adjust" and card:
        amount = _num(st["adj"]["amt"])
        delta = None if not amount or amount <= 0 else (amount if st["adj"]["dir"] == "add" else -amount)
        if delta is None or not st["adj"]["why"]:
            return _("Enter an amount and a reason."), None
        problem = gc.adjust_problem(card, delta)
        if problem:
            return problem, None
        card = gc.adjust(card, delta.quantize(Decimal("0.01")), st["adj"]["why"], user)
        st["adj"] = {"dir": "add", "amt": "", "why": ""}
        return (_("Balance %(how)s to %(amount)s.") % {"how": _("increased") if delta > 0 else _("reduced"), "amount": _money(card.available_balance)}
                + (" " + _("It’s deleted in 30 days.") if card.available_balance <= 0 else "")), None
    elif name == "freeze" and card and card.available_balance > 0:
        gc.set_frozen(card, not card.frozen, user)
        return (_("•• %(last4)s frozen. It won’t work at checkout.") if card.frozen else _("•• %(last4)s can be used again.")) % {"last4": card.last4}, None
    elif name == "resend" and card and card.email and card.available_balance > 0 and not card.frozen:
        if gc.resend(card, request):
            return _("Gift card email resent to %(email)s. It shows the last 4 digits and a link to the balance.") % {"email": card.email}, None
        return _("The email couldn’t be sent. Try again in a minute."), None
    elif name == "issue":
        st["issue"] = blank_issue()
    elif name == "close":
        st["issue"] = None
    elif st.get("issue") is not None:
        m = st["issue"]
        if name == "design" and arg.isdigit():
            design = gc.designs_on_sale().filter(pk=arg).first()
            if design:
                m["design"] = design.id
                if m["amt"] != "other" and design.amounts:
                    m["amt"] = design.amounts[min(1, len(design.amounts) - 1)]
        elif name == "amt" and (arg.isdigit() or arg == "other"):
            m["amt"] = int(arg) if arg.isdigit() else "other"
        elif name == "send" and arg in ("email", "show"):
            m["send"] = arg
        elif name == "go":
            if issue_errors(m):
                return "", None
            design = gc.designs_on_sale().filter(pk=m["design"]).first()
            amount = issue_amount(m)
            new, number = gc.issue(design=design, amount=amount, recipient=m["name"], email=m["email"], message=m["msg"], note=m["why"],
                                   by=user, request=request, send=m["send"] == "email")
            st.update(issue=None, sel=new.id, tab="active", q="")
            return "", {"card": new, "number": gc.pretty(number), "send": m["send"], "email": m["email"], "name": m["name"],
                        "amount": f"${amount:,.0f}", "design": design}
    return "", None


# ---------- the page ----------

def cards():
    return GiftCard.objects.select_related("design").annotate(
        last_use=Max("events__at", filter=Q(events__kind=GiftCardEvent.USE)),
        uses=Count("events", filter=Q(events__kind=GiftCardEvent.USE)))


def page(st, reveal=None) -> dict:
    from admin_portal.models import StoreSettings
    today = timezone.localdate()
    everything = list(cards())
    live = [c for c in everything if c.available_balance > 0]
    empty = [c for c in everything if c.available_balance <= 0]
    since = timezone.now() - datetime.timedelta(days=30)
    recent = [c for c in everything if c.issued_at >= since]
    nxt = min((c.deletes_on for c in empty if c.deletes_on), default=None)
    stats = [
        (_("Outstanding balance"), _money(sum((c.available_balance for c in live), Decimal(0))),
         ngettext("Owed to customers on %(n)d card", "Owed to customers on %(n)d cards", len(live)) % {"n": len(live)}),
        (_("Active cards"), str(len(live)), ngettext("%(n)d frozen", "%(n)d frozen", sum(c.frozen for c in live)) % {"n": sum(c.frozen for c in live)}),
        (_("Sold or issued, 30 days"), _money(sum((c.original for c in recent), Decimal(0))),
         ngettext("%(n)d card", "%(n)d cards", len(recent)) % {"n": len(recent)}),
        (_("Empty, being deleted"), str(len(empty)), _("Next on %(date)s") % {"date": _date(nxt)} if nxt else _("Nothing pending")),
    ]
    q = st["q"].lower()
    shown = [c for c in everything if (st["tab"] == "all" or (st["tab"] == "active") == (c.available_balance > 0))
             and (not q or q in c.last4 or q in f"{c.recipient} {c.email} {c.buyer}".lower())]
    shown.sort(key=lambda c: (c.available_balance <= 0, -(c.last_use or c.issued_at).timestamp()))
    sel = next((c for c in everything if c.id == st.get("sel")), None) or (shown[0] if shown else None)
    if sel:
        st["sel"] = sel.id
    rows = []
    for c in shown:
        is_empty = c.available_balance <= 0
        label, key = SOURCE[c.source]
        if is_empty:
            label, key = ngettext("Deletes in %(n)dd", "Deletes in %(n)dd", max(0, (c.deletes_on - today).days)) % {"n": max(0, (c.deletes_on - today).days)}, "gone"
        elif c.frozen:
            label, key = _("Frozen"), "frozen"
        pct = int(c.available_balance * 100 / c.original) if c.original else 0
        rows.append({"c": c, "on": sel is not None and c.id == sel.id, "empty": is_empty, "src": label, "src_key": key,
                     "bal": _money(c.available_balance), "of": _("of %(v)s") % {"v": _money(c.original).replace(".00", "")},
                     "pct": max(0, min(100, pct)), "low": pct < 20,
                     "last": _rel(c.last_use) if c.last_use else _("Unused")})
    no_rows = (_("No card matches “%(q)s”.") % {"q": st["q"]} if q else _("No empty cards waiting to be deleted.") if st["tab"] == "empty"
               else _("No gift cards yet."))
    return {"st": st, "stats": stats, "tabs": [(k, label, {"active": len(live), "empty": len(empty), "all": len(everything)}[k], st["tab"] == k) for k, label in TABS],
            "rows": rows, "no_rows": no_rows, "store": StoreSettings.load().name,
            "d": detail(st, sel) if sel else None, "m": issue_dialog(st["issue"]) if st.get("issue") is not None else None, "reveal": reveal}


def detail(st, c) -> dict:
    is_empty = c.available_balance <= 0
    banner = ""
    if is_empty and c.empty_since:
        days = max(0, (c.deletes_on - timezone.localdate()).days)
        banner = _("Balance ran out %(since)s. This card is deleted on %(on)s (%(n)s days).") % {
            "since": _date(c.empty_since), "on": _date(c.deletes_on), "n": days}
    elif c.frozen:
        banner = _("Frozen. It can’t be used at checkout until you unfreeze it.") + (" " + _("Note: %(note)s") % {"note": c.note} if c.note else "")
    source = {GiftCard.ONLINE: _("Online store"), GiftCard.REGISTER: _("Register"), GiftCard.STAFF: _("Issued by staff")}[c.source]
    if c.buyer and c.source == GiftCard.ONLINE:
        source += f" · {c.buyer}"
    elif c.buyer and c.source == GiftCard.STAFF:
        source += f" · {c.buyer.split(' ')[0]}"
    events = list(c.events.select_related("order").order_by("-at", "-id"))
    hist = []
    for h in events:
        if h.kind == GiftCardEvent.NOTE:
            what, when = h.note, _date(h.at, True)
        elif h.kind == GiftCardEvent.USE:
            what, when = (_("Used on order #%(n)s") % {"n": h.order_id} if h.order_id else _("Used")), _date(h.at, True)
        elif h.kind == GiftCardEvent.REFUND:
            what, when = (_("Money back from order #%(n)s") % {"n": h.order_id} if h.order_id else _("Money back")), _date(h.at, True)
        else:
            what = _("Activated") if h.kind == GiftCardEvent.ISSUE else _("Adjusted")
            when = _date(h.at, True) + (f" · {h.note}" if h.note else "")
        hist.append({"what": what, "when": when, "kind": h.kind, "plus": h.amount > 0,
                     "amt": "" if not h.amount else ("+" if h.amount > 0 else "−") + _money(abs(h.amount)),
                     "after": "" if h.kind == GiftCardEvent.NOTE else _("Left %(v)s") % {"v": _money(h.balance)}})
    adj = st["adj"]
    amount = _num(adj["amt"])
    delta = (amount if adj["dir"] == "add" else -amount) if amount and amount > 0 else None
    adj_err = gc.adjust_problem(c, delta) if delta else ""
    return {
        "c": c, "empty": is_empty, "banner": banner, "picture": c.picture_url(), "design": c.design.name if c.design else _("Gift card"),
        "ink": c.design.ink if c.design else "light", "color": c.design.color if c.design else "#1F4A3A",
        "facts": [(_("Recipient"), c.recipient or _("Not given")), (_("Email"), c.email or "—"), (_("From"), source),
                  (_("Issued"), _date(c.issued_at, True)), (_("Starting value"), _money(c.original)),
                  (_("Used"), f"{_money(c.original - c.available_balance if c.original > c.available_balance else 0)} · "
                   + ngettext("%(n)d order", "%(n)d orders", c.uses) % {"n": c.uses})],
        "note": _("Internal note: %(note)s") % {"note": c.note} if c.note and c.source == GiftCard.STAFF and not c.frozen else "",
        "resend_off": not c.email or is_empty or c.frozen, "freeze_off": is_empty,
        "adj": adj, "adj_err": adj_err, "adj_off": not delta or not adj["why"] or bool(adj_err),
        "adj_text": ((_("Add %(v)s · new balance %(b)s") if delta > 0 else _("Remove %(v)s · new balance %(b)s"))
                     % {"v": _money(abs(delta)), "b": _money(max(Decimal(0), c.available_balance + delta))}) if delta else _("Enter an amount and a reason"),
        "hist": hist,
    }


def issue_dialog(m) -> dict:
    designs = list(gc.designs_on_sale())
    design = next((d for d in designs if d.id == m["design"]), designs[0] if designs else None)
    errs = issue_errors(m)
    amount = issue_amount(m)
    match = None
    if m["email"] and EMAIL.match(m["email"]):
        match = get_user_model().objects.filter(email__iexact=m["email"]).first()
    first = m["name"].split(" ")[0] if m["name"] else ""
    ok = "amt" not in errs
    return {
        "m": m, "errs": errs, "off": bool(errs), "design": design,
        "designs": [(d, design is not None and d.id == design.id) for d in designs],
        "amounts": [(a, m["amt"] == a) for a in (design.amounts if design else [25, 50, 100])],
        "other_on": m["amt"] == "other",
        "match": (_("Matches customer %(name)s. It shows on their profile and in their account.") % {"name": match.get_full_name() or match.username})
        if match else "",
        "whys": [(str(w), str(w) == m["why"]) for w in WHY],
        "summary": ((f"{_money(amount).replace('.00', '')} " + (_("%(design)s card") % {"design": design.name} if design else _("gift card"))
                     + (" " + _("for %(name)s") % {"name": first} if first else "")) if ok else errs["amt"]),
        "go_text": _("Issue and send") if m["send"] == "email" else _("Issue and show code"),
    }
