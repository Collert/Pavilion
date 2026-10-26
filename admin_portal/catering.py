"""
The admin's Catering page (design: AdminCatering): an inbox of catering orders by status, one order's details,
editing it into proposed changes (a draft like the other editors: op buttons post the form and the page comes back
with the draft), the accept and reject sheets, and the Settings tab (one form with Save). What the actions do is in
catering/flow.py; money in catering/pricing.py.
"""
import datetime
from decimal import Decimal

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from catering import flow, pricing
from catering.models import CateringLine, CateringMessage, CateringOrder, CateringSettings
from pos_server.models import Condiment, Dish, Disposable
from services.models import Service

from .models import StoreSettings

CENT = Decimal("0.01")
money = pricing.money
TABS = [("new", _("New"), [CateringOrder.NEW]), ("waiting", _("Waiting on customer"), [CateringOrder.WAITING]),
        ("upcoming", _("Upcoming"), [CateringOrder.CONFIRMED]),
        ("past", _("Past"), [CateringOrder.DONE, CateringOrder.REJECTED, CateringOrder.CANCELLED])]
EMPTY = {"new": _("No new catering orders. Nice."), "waiting": _("Nobody’s deciding on changes right now."),
         "upcoming": _("No confirmed events coming up."), "past": _("Nothing here yet.")}
AVATARS = ["#1F4A3A", "#C9821A", "#4E79B5", "#7A5AC8", "#2BA3A0", "#C2453B", "#6B7A2E", "#E87BA4"]
KINDS = [(CateringLine.DISH, _("Dish"), "M3 11h18 M5 11a7 7 0 0 1 14 0 M4 15h16l-2 4H6z"),
         (CateringLine.SERVICE, _("Service"), "M12 3 4 7v6c0 4 3.4 7.4 8 8 4.6-.6 8-4 8-8V7z M9 12l2 2 4-4"),
         (CateringLine.CONDIMENT, _("Condiment"), "M9 3h6v3l2 3v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V9l2-3z M7 13h10"),
         (CateringLine.DISPOSABLE, _("Disposable"), "M5 8h14l-1.5 12h-11z M4 8h16 M9 4h6l1 4H8z")]
GROUPS = [(CateringLine.DISH, _("Food")), (CateringLine.SERVICE, _("Services")), (CateringLine.CONDIMENT, _("Condiments")),
          (CateringLine.DISPOSABLE, _("Disposables"))]


# ---------- small formatting ----------

def clock(t) -> str:
    """12 PM, 6:30 PM"""
    hour = (t.hour + 11) % 12 + 1
    return (f"{hour}:{t.minute:02d}" if t.minute else str(hour)) + (" AM" if t.hour < 12 else " PM")


def day_text(d, weekday=False) -> str:
    return (f"{d:%a} " if weekday else "") + f"{d:%b} {d.day}"


def stamp(at) -> str:
    """Today, 3:42 PM · Yesterday, 9 AM · Oct 15, 11:20 AM"""
    at = timezone.localtime(at)
    days = (timezone.localdate() - at.date()).days
    when = _("Today") if days == 0 else _("Yesterday") if days == 1 else day_text(at.date())
    return f"{when}, {clock(at.time())}"


def ago(at) -> str:
    days = (timezone.localdate() - timezone.localtime(at).date()).days
    if days <= 0:
        return _("today")
    if days == 1:
        return _("yesterday")
    return ngettext("%(n)d day ago", "%(n)d days ago", days) % {"n": days}


def plural_days(n) -> str:
    n = float(n)
    if n == int(n):
        return ngettext("%(n)d day", "%(n)d days", int(n)) % {"n": int(n)}
    return _("%(n)s days") % {"n": f"{n:g}"}


def initials(name) -> str:
    return "".join(word[0] for word in name.split()[:2]).upper()


def avatar(name) -> str:
    return AVATARS[sum(ord(c) for c in name) % len(AVATARS)]


def tab_of(status) -> str:
    return next(key for key, _label, statuses in TABS if status in statuses)


# ---------- inbox ----------

def inbox(tab, selected=None) -> dict:
    """The tabs, the rows in `tab`, and which order is open (`selected`, else the tab's first)."""
    cfg = CateringSettings.load()
    counts = dict(CateringOrder.objects.values_list("status").annotate(n=Count("id")))
    tabs = [{"key": key, "label": label, "count": sum(counts.get(s, 0) for s in statuses), "on": key == tab,
             "url": reverse("admin_catering") + f"?tab={key}"} for key, label, statuses in TABS]
    statuses = next(statuses for key, _l, statuses in TABS if key == tab)
    orders = list(CateringOrder.objects.filter(status__in=statuses).prefetch_related("lines"))
    orders.sort(key=lambda o: (o.event_date, o.event_time), reverse=tab == "past")
    today = timezone.localdate()
    rows = []
    for o in orders:
        lines = pricing.lines_of(o)
        t = pricing.totals(lines, pricing.Catalog(lines))
        ahead, soon = o.days_ahead, (o.event_date - today).days
        short = o.status == CateringOrder.NEW and ahead < cfg.notice_days(t["minutes"])
        flag, flag_kind = "", ""
        if short:
            flag, flag_kind = ngettext("Only %(n)d day’s notice", "Only %(n)d days’ notice", ahead) % {"n": ahead}, "bad"
        elif o.status == CateringOrder.WAITING and o.proposed_at:
            flag, flag_kind = _("Sent %(when)s") % {"when": ago(o.proposed_at)}, "soft"
        elif o.status == CateringOrder.CONFIRMED and 0 <= soon <= 7:
            flag, flag_kind = (_("Today") if soon == 0 else ngettext("In %(n)d day", "In %(n)d days", soon) % {"n": soon}), "warn"
        elif o.status == CateringOrder.NEW:
            flag, flag_kind = _("Came in %(when)s") % {"when": ago(o.placed_at)}, "soft"
        rows.append({"o": o, "on": selected is not None and o.id == selected.id, "name": o.organization or o.name,
                     "mon": f"{o.event_date:%b}".upper(), "day": o.event_date.day,
                     "meta": f"{o.event_date:%a} {clock(o.event_time)} · {o.get_fulfilment_display()}",
                     "total": t["total"], "flag": flag, "flag_kind": flag_kind,
                     "url": reverse("admin_catering_order", args=[o.id]) + f"?tab={tab}"})
    if selected is None and rows:
        selected = rows[0]["o"]
        rows[0]["on"] = True
    return {"tabs": tabs, "tab": tab, "rows": rows, "empty": EMPTY[tab], "selected": selected, "off": not cfg.on,
            "new_count": counts.get(CateringOrder.NEW, 0)}


# ---------- the edit draft ----------

def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dec(value):
    try:
        return Decimal(str(value).strip() or "0").quantize(CENT)
    except ArithmeticError:
        return Decimal("0")


def draft_from_order(order) -> dict:
    return {"lines": pricing.lines_of(order), "add_kind": CateringLine.DISH, "add_ref": "", "add_qty": "10",
            "add_price": "", "add_last": "", "msg": ""}


def draft_from_post(post) -> dict:
    lines = []
    for i in range(min(_int(post.get("line-count")), 200)):
        kind = post.get(f"line-{i}-kind", "")
        if kind not in CateringLine.FIELDS or not _int(post.get(f"line-{i}-ref")):
            continue
        lines.append({"id": post.get(f"line-{i}-id", ""), "kind": kind, "ref": _int(post.get(f"line-{i}-ref")),
                      "quantity": max(_int(post.get(f"line-{i}-quantity"), 1), 1), "amount": max(_int(post.get(f"line-{i}-amount")), 0),
                      "price": str(max(_dec(post.get(f"line-{i}-price")), Decimal("0"))), "removed": post.get(f"line-{i}-removed") == "1"})
    kind = post.get("add_kind", CateringLine.DISH)
    return {"lines": lines, "add_kind": kind if kind in CateringLine.FIELDS else CateringLine.DISH,
            "add_ref": post.get("add_ref", ""), "add_qty": post.get("add_qty", "10"), "add_price": post.get("add_price", ""),
            "add_last": post.get("add_last", ""), "msg": post.get("msg", "")[:600]}


def _step(quantity, up):
    """Steppers move by 1 up to 20, then by 5 (like the design)."""
    if up:
        return quantity + (5 if quantity >= 20 else 1)
    return max(1, quantity - (5 if quantity > 20 else 1))


def apply_op(draft, op, order) -> dict:
    """Change the draft for an editor button: less/more:i, hless/hmore:i, remove/undo:i, add_kind:k, add."""
    saved = {line["id"]: line for line in pricing.lines_of(order)}
    name, _sep, arg = op.partition(":")
    lines = draft["lines"]
    i = _int(arg, -1)
    line = lines[i] if 0 <= i < len(lines) else None
    if name in ("less", "more") and line:
        line["quantity"] = _step(line["quantity"], name == "more")
    elif name in ("hless", "hmore") and line:
        line["amount"] = max(1, line["amount"] + (1 if name == "hmore" else -1))
    elif name == "remove" and line:
        if line["id"] in saved:
            line["removed"] = True
        else:
            lines.pop(i)
    elif name == "undo" and line and line["id"] in saved:
        lines[i] = dict(saved[line["id"]])
    elif name == "add_kind" and arg in CateringLine.FIELDS:
        draft.update(add_kind=arg, add_ref="", add_price="", add_last="", add_qty="1" if arg == CateringLine.SERVICE else "10")
    elif name == "add":
        item = pricing.Catalog([{"kind": draft["add_kind"], "ref": _int(draft["add_ref"])}]).get(draft["add_kind"], _int(draft["add_ref"]))
        quantity = _int(draft["add_qty"])
        if item is not None and quantity > 0:
            kind = draft["add_kind"]
            amount = (3 if item.pricing == Service.HOUR else 1) if pricing.hourly(kind, item) else 0
            price = _dec(draft["add_price"]) if kind in pricing.CUSTOM else pricing.catalog_price(kind, item, quantity)
            lines.append({"id": "", "kind": kind, "ref": item.pk, "quantity": quantity, "amount": amount, "price": str(price), "removed": False})
            draft.update(add_ref="", add_price="", add_last="")
    # Dishes follow their catering price as the quantity changes (unchanged saved lines keep theirs)
    catalog = pricing.Catalog(lines)
    for line in lines:
        if line["kind"] == CateringLine.DISH:
            was = saved.get(line["id"])
            if not was or was["quantity"] != line["quantity"]:
                dish = catalog.get(line["kind"], line["ref"])
                if dish:
                    line["price"] = str(dish.catering_price(line["quantity"]))
    # A newly picked condiment or disposable starts at its suggested price
    if draft["add_kind"] in pricing.CUSTOM and draft["add_ref"] and draft["add_ref"] != draft["add_last"]:
        item = pricing.Catalog([{"kind": draft["add_kind"], "ref": _int(draft["add_ref"])}]).get(draft["add_kind"], _int(draft["add_ref"]))
        if item is not None:
            draft["add_price"] = str(pricing.suggested_price(draft["add_kind"], item))
    draft["add_last"] = draft["add_ref"]
    return draft


def _add_options(draft, used):
    kind = draft["add_kind"]
    taken = {line["ref"] for line in used if line["kind"] == kind}
    if kind == CateringLine.DISH:
        items = Dish.objects.filter(catering=True, archived=False).prefetch_related("catering_prices").order_by("title")
        label = lambda x: f"{x.title} · {money(x.catering_price(x.catering_min))}"  # noqa: E731
    elif kind == CateringLine.SERVICE:
        items = Service.objects.filter(catering=True, active=True, archived=False).order_by("category", "title")
        per = {Service.HOUR: "/h", Service.DAY: "/day"}
        label = lambda x: f"{x.title} · {money(x.price)}{per.get(x.pricing, '')}"  # noqa: E731
    elif kind == CateringLine.CONDIMENT:
        items = Condiment.objects.select_related("component").filter(component__archived=False).order_by("component__title")
        label = lambda x: f"{x.component.title} · {money(x.price)}"  # noqa: E731
    else:
        items = Disposable.objects.select_related("ingredient").filter(ingredient__archived=False).order_by("ingredient__title")
        label = lambda x: f"{x.title} · " + _("costs %(c)s") % {"c": money(x.ingredient.cost)}  # noqa: E731
    return [{"id": x.pk, "label": label(x), "sel": str(x.pk) == str(draft["add_ref"])} for x in items if x.pk not in taken]


def _add_panel(draft, used, first):
    kind, ref = draft["add_kind"], _int(draft["add_ref"])
    item = pricing.Catalog([{"kind": kind, "ref": ref}]).get(kind, ref) if ref else None
    what = {CateringLine.DISH: _("Dish"), CateringLine.SERVICE: _("Service"), CateringLine.CONDIMENT: _("Condiment"),
            CateringLine.DISPOSABLE: _("Disposable item")}[kind]
    custom = kind in pricing.CUSTOM
    if kind == CateringLine.DISH:
        hint = _("Dishes use their catering price.")
        fixed = money(item.catering_price(_int(draft["add_qty"], 1))) + " " + _("ea") if item else _("Catering price")
    elif kind == CateringLine.SERVICE:
        hint = _("Services use their regular price. Deposits are added automatically.")
        fixed = f"{money(item.price)}{ {Service.HOUR: '/h', Service.DAY: '/day'}.get(item.pricing, '')}" if item else _("Regular price")
    elif kind == CateringLine.CONDIMENT:
        hint = (_("Regular price is %(p)s. Charge what you like, or $0 to include it.") % {"p": money(item.price)}) if item else _("You set the price for condiments.")
        fixed = ""
    else:
        hint = (_("Costs you %(c)s each. Charge what you like, or $0 to include it.") % {"c": money(item.ingredient.cost)}) if item else _("You set the price for disposables.")
        fixed = ""
    qty_label = _("People") if item is not None and pricing.hourly(kind, item) and item.counts_units else _("Quantity")
    ok = item is not None and _int(draft["add_qty"]) > 0 and (not custom or draft["add_price"] != "")
    return {"kinds": [{"key": k, "label": label, "path": path, "on": k == kind} for k, label, path in KINDS],
            "what": what, "options": _add_options(draft, used), "custom": custom, "fixed": fixed, "hint": hint,
            "qty_label": qty_label, "ok": ok, "placeholder": _("Choose a %(what)s…") % {"what": what.lower()}}


# ---------- one order ----------

def _staff_name(user, me):
    if user is None:
        return _("Staff")
    name = user.first_name or user.username
    return _("%(name)s (you)") % {"name": name} if me and user.pk == me.pk else name


def _amount_text(service, line):
    n = max(int(line.get("amount") or 1), 1)
    if service.pricing == Service.HOUR:
        return _("%(n)d h") % {"n": n}
    return ngettext("%(n)d day", "%(n)d days", n) % {"n": n}


def _rows(cur, prev, catalog, editing):
    """The order's lines by group, tagged against `prev` (the lines before the changes) when there is one."""
    old = {line["id"]: line for line in prev or [] if line.get("id")}
    groups = []
    for kind, title in GROUPS:
        rows = []
        for n, line in enumerate(cur):
            if line["kind"] != kind:
                continue
            f = pricing.figure(line, catalog)
            was = old.get(line.get("id")) if prev is not None else None
            removed = bool(line.get("removed"))
            new = prev is not None and not was
            tag, tag_kind = "", ""
            if removed:
                tag, tag_kind = _("Removed"), "rm"
            elif new:
                tag, tag_kind = _("New"), "add"
            elif was:
                if int(was["quantity"]) != int(line["quantity"]) or int(was.get("amount") or 0) != int(line.get("amount") or 0):
                    tag, tag_kind = _("Was %(q)s") % {"q": pricing.figure(was, catalog)["qty_text"].replace("× ", "")}, "ch"
                elif Decimal(str(was["price"])) != Decimal(str(line["price"])) and kind in pricing.CUSTOM:
                    tag, tag_kind = _("Was %(p)s ea") % {"p": money(was["price"])}, "ch"
            detail = f["unit"] + (" · " + _("%(d)s deposit") % {"d": money(f["deposit"])} if f["deposit"] else "")
            rows.append({"n": n, "l": line, "name": f["name"], "detail": detail, "qty_text": f["qty_text"], "total": f["total"],
                         "tag": tag, "tag_kind": tag_kind, "removed": removed, "new": new, "changed": tag_kind == "ch",
                         "hourly": f["hourly"], "counts": f["counts"], "custom": kind in pricing.CUSTOM,
                         "amount_text": _amount_text(f["item"], line) if f["hourly"] else "",
                         "can_undo": editing and removed and line.get("id") in old})
        if prev is not None and not editing:
            ids = {line.get("id") for line in cur}
            for line in prev:
                if line["kind"] == kind and line.get("id") not in ids:
                    f = pricing.figure(line, catalog)
                    rows.append({"name": f["name"], "detail": _("Removed in your changes"), "qty_text": f["qty_text"], "total": f["total"],
                                 "tag": _("Removed"), "tag_kind": "rm", "removed": True, "ghost": True})
        if rows:
            groups.append({"title": title, "rows": rows})
    return groups


def _totals(order, t, tp, editing):
    rate = pricing.tax_rate() * 100
    def row(label, value, was=None, big=False):
        return {"label": label, "value": value, "was": was if was is not None and abs(was - value) >= CENT else None, "big": big}
    out = [row(_("Food"), t["food"], tp and tp["food"]), row(_("Services"), t["services"], tp and tp["services"])]
    if t["extras"] or (tp and tp["extras"]):
        out.append(row(_("Condiments and disposables"), t["extras"], tp and tp["extras"]))
    out.append(row(_("Tax %(rate)s%%") % {"rate": f"{rate.normalize():f}"}, t["tax"], tp and tp["tax"]))
    if editing or order.status in (CateringOrder.NEW, CateringOrder.WAITING):
        label = _("Total to charge")
    elif order.status == CateringOrder.REJECTED or (order.status == CateringOrder.CANCELLED and not order.charged):
        label = _("Total, not charged")
    else:
        label = _("Charged")
    out.append(row(label, t["total"], tp and tp["total"], big=True))
    return out


def _timeline(order, me):
    store = StoreSettings.load().name
    out = []
    for m in order.messages.select_related("user"):
        staff = _staff_name(m.user, me)
        head = {
            CateringMessage.PLACED: _("%(name)s placed the order") % {"name": order.first_name},
            CateringMessage.CHANGES: _("%(name)s proposed changes") % {"name": staff},
            CateringMessage.ACCEPTED: _("%(name)s accepted the order") % {"name": staff},
            CateringMessage.REJECTED: _("%(name)s rejected the order · %(reason)s") % {"name": staff, "reason": m.reason},
            CateringMessage.CANCELLED: _("%(name)s cancelled the order") % {"name": order.first_name} if m.who == CateringMessage.CUSTOMER else _("%(name)s cancelled the order · %(reason)s") % {"name": staff, "reason": m.reason},
            CateringMessage.AGREED: _("%(name)s accepted the changes") % {"name": order.first_name},
            CateringMessage.DECLINED: _("%(name)s declined the changes") % {"name": order.first_name},
            CateringMessage.COUNTER: _("%(name)s suggested changes") % {"name": order.first_name},
        }.get(m.kind)
        if head is None:
            head = order.name if m.who == CateringMessage.CUSTOMER else staff if m.who == CateringMessage.STAFF else store
        text = order.note if m.kind == CateringMessage.PLACED else m.text
        changes = [{"t": c[0], "kind": c[1]} for c in m.changes or []]
        total = ""
        if m.was_total is not None and m.now_total is not None:
            total = _("Total %(was)s → %(now)s") % {"was": money(m.was_total), "now": money(m.now_total)}
        who_name = order.organization or order.name
        out.append({"head": head, "at": stamp(m.at), "text": text, "who": m.who, "changes": changes, "total": total,
                    "ini": initials(who_name) if m.who == CateringMessage.CUSTOMER else initials(staff) if m.who == CateringMessage.STAFF else store[:1].upper(),
                    "color": avatar(who_name) if m.who == CateringMessage.CUSTOMER else ""})
    return out


def detail(order, me, draft=None, sheet="") -> dict:
    """Everything the right side shows for `order`; `draft` = editing it into proposed changes."""
    cfg = CateringSettings.load()
    today = timezone.localdate()
    editing = draft is not None
    saved = pricing.lines_of(order)
    if editing:
        cur, prev = draft["lines"], saved
    else:
        cur = saved
        prev = order.previous if order.status in (CateringOrder.NEW, CateringOrder.WAITING) and order.previous else None
    catalog = pricing.Catalog(cur, prev, saved)
    t = pricing.totals(cur, catalog)
    tp = pricing.totals(prev, catalog) if prev is not None else None
    first = order.first_name
    until = (order.event_date - today).days
    pickup = order.fulfilment == CateringOrder.PICKUP
    street, _sep, rest = order.address.partition(",")
    card_sub = {CateringOrder.NEW: _("not charged yet"), CateringOrder.WAITING: _("not charged yet"),
                CateringOrder.REJECTED: _("never charged")}.get(order.status)
    if card_sub is None:
        card_sub = _("refunded") if order.refunded else _("charged") if order.charged else _("never charged")
    facts = [
        {"label": _("When"), "value": f"{day_text(order.event_date, True)}, {clock(order.event_time)}",
         "sub": (_("in %(d)s") % {"d": plural_days(until)}) if until > 0 else _("today") if until == 0 else _("%(d)s ago") % {"d": plural_days(-until)}},
        {"label": order.get_fulfilment_display(), "value": _("At the store") if pickup else street,
         "sub": _("ready 15 min before") if pickup else rest.strip()},
        {"label": _("Contact"), "value": order.phone or "—", "sub": order.email},
        {"label": _("Card on file"), "value": order.card, "sub": card_sub},
    ]
    ahead = order.days_ahead
    extra = cfg.extra_days(t["minutes"])
    need = cfg.notice_days(t["minutes"])
    size = " " + _("for an order this size") if extra else ""
    if ahead < need:
        lead = _("Ordered %(a)s ahead. You ask for %(n)s%(size)s.") % {"a": plural_days(ahead), "n": plural_days(need), "size": size}
    else:
        lead = _("Ordered %(a)s ahead. Meets your %(n)s-day minimum%(size)s.") % {"a": plural_days(ahead), "n": f"{need:g}", "size": size}
    placed = stamp(order.placed_at)
    if placed.startswith((_("Today"), _("Yesterday"))):
        placed = placed[:1].lower() + placed[1:]
    d = {"o": order, "first": first, "editing": editing, "facts": facts, "lead": lead, "lead_bad": ahead < need,
         "kitchen": _("About %(t)s of kitchen time, from each dish’s prep time.") % {"t": pricing.kitchen_text(t["minutes"])},
         "who": " · ".join(filter(None, [order.name, order.organization, _("ordered %(when)s") % {"when": placed}])),
         "ini": initials(order.organization or order.name), "color": avatar(order.organization or order.name),
         "groups": _rows(cur, prev, catalog, editing), "totals": _totals(order, t, tp, editing), "t": t,
         "head": _("Editing the order") if editing else (_("Their suggested changes") if order.status == CateringOrder.NEW else _("Your proposed changes")) if prev is not None else _("Order"),
         "portions": ngettext("%(n)d portion", "%(n)d portions", t["portions"]) % {"n": t["portions"]},
         "timeline": _timeline(order, me), "closed": order.status not in CateringOrder.OPEN}
    # Deposit
    hold_day = order.hold_on(cfg.hold_days)
    if t["deposit"]:
        if order.status == CateringOrder.DONE or order.hold_state in (CateringOrder.RELEASED, CateringOrder.KEPT):
            dep_sub = _("Kept, something didn’t come back.") if order.hold_state == CateringOrder.KEPT else _("Released after everything came back.") if order.hold_state == CateringOrder.RELEASED else _("Never held.")
        elif order.status in (CateringOrder.REJECTED, CateringOrder.CANCELLED):
            dep_sub = _("Never held.")
        else:
            when = _("Held on their card now") if order.hold_state == CateringOrder.HELD or hold_day <= today else _("Held on %(day)s") % {"day": day_text(hold_day, True)}
            dep_sub = when + ", " + _("not charged. Released when the equipment comes back, or kept if something’s missing or broken.")
        d["deposit"] = {"head": _("%(d)s equipment deposit, held separately") % {"d": money(t["deposit"])}, "sub": dep_sub}
    d["reply_ph"] = _("Write to %(name)s…") % {"name": first}
    d["thread_sub"] = _("This order is closed.") if d["closed"] else _("Messages go to %(email)s and show on their order page.") % {"email": order.email}
    d["link"] = reverse("catering_order", args=[order.token])
    # The action bar
    total_text = money(t["total"]) + (" + " + _("%(d)s deposit hold") % {"d": money(t["deposit"])} if t["deposit"] else "")
    bar = {"kind": "edit" if editing else "new" if order.status == CateringOrder.NEW else "other", "text": total_text, "buttons": []}
    if order.status == CateringOrder.WAITING:
        bar.update(icon="wait", tone="wait", head=_("Waiting on %(name)s since %(day)s") % {"name": first, "day": day_text(timezone.localtime(order.proposed_at or order.placed_at).date(), True)},
                   sub=_("Charged %(t)s when they accept. Cancelled if they decline.") % {"t": money(t["total"])},
                   buttons=[{"label": _("Cancel order"), "href": "?sheet=reject", "kind": "bad"},
                            {"label": _("Edit changes"), "href": "?edit=1", "kind": ""},
                            {"label": _("Send reminder"), "op": "remind", "kind": "go"}])
    elif order.status == CateringOrder.CONFIRMED:
        accepted = timezone.localtime(order.accepted_at).date() if order.accepted_at else today
        if order.hold_state == CateringOrder.HELD:
            sub = _("%(d)s deposit is held on their card.") % {"d": money(order.hold_amount)}
        elif t["deposit"]:
            sub = (_("%(d)s deposit will be held on %(day)s.") % {"d": money(t["deposit"]), "day": day_text(hold_day, True)}) if hold_day > today else _("%(d)s deposit hold is being placed.") % {"d": money(t["deposit"])}
        else:
            sub = _("No deposit on this order.")
        buttons = [{"label": _("Cancel and refund"), "href": "?sheet=reject", "kind": "bad"}]
        if order.event_date <= today:
            if order.hold_state == CateringOrder.HELD:
                buttons = [{"label": _("Keep deposit"), "op": "keep", "kind": "bad"}, {"label": _("Equipment back"), "op": "finish", "kind": "go"}]
            else:
                buttons.append({"label": _("Mark completed"), "op": "finish", "kind": "go"})
        bar.update(icon="ok", tone="ok", head=_("Confirmed · %(t)s charged %(day)s") % {"t": money(order.charged or t["total"]), "day": day_text(accepted)},
                   sub=sub, buttons=buttons)
    elif order.status == CateringOrder.DONE:
        dep = {CateringOrder.RELEASED: _("Deposit released."), CateringOrder.KEPT: _("Deposit kept.")}.get(order.hold_state, "")
        bar.update(icon="ok", tone="done", head=_("Completed %(day)s") % {"day": day_text(order.event_date, True)},
                   sub=(_("%(t)s charged.") % {"t": money(order.charged)} + " " + dep).strip())
    elif order.status in (CateringOrder.REJECTED, CateringOrder.CANCELLED):
        closed = timezone.localtime(order.closed_at).date() if order.closed_at else today
        word = _("Cancelled %(day)s") if order.status == CateringOrder.CANCELLED else _("Rejected %(day)s")
        bar.update(icon="no", tone="bad", head=(word % {"day": day_text(closed, True)}) + (" · " + order.reason if order.reason else ""),
                   sub=_("%(t)s refunded to %(card)s.") % {"t": money(order.refunded), "card": order.card} if order.refunded else _("Nothing was charged."))
    d["bar"] = bar
    # Editing
    if editing:
        used = [line for line in cur if not line.get("removed")]
        d["draft"] = draft
        d["add"] = _add_panel(draft, used, first)
        diff = pricing.changes(saved, cur, catalog)
        saved_total = pricing.totals(saved, catalog)["total"]
        d["edit"] = {"count": len(diff), "head": ngettext("%(n)d change", "%(n)d changes", len(diff)) % {"n": len(diff)} if diff else _("No changes yet"),
                     "total": f"{money(saved_total)} → {money(t['total'])}" if diff else "",
                     "hint": _("Change quantities and prices, remove lines, or add something above.") if not diff else _("Add a reason above to send.") if not draft["msg"].strip() else _("Nothing is charged until %(name)s accepts.") % {"name": first},
                     "can_send": bool(diff) and bool(draft["msg"].strip()),
                     "placeholder": _("Tell %(name)s what changed and why. They see this with the new total.") % {"name": first}}
    # Sheets
    if sheet == "accept" and order.status == CateringOrder.NEW:
        d["sheet"] = {"kind": "accept", "sub": f"{order.title} · {day_text(order.event_date, True)}, {clock(order.event_time)} · {order.get_fulfilment_display()}",
                      "dep_when": _("Held now, released when the equipment comes back.") if hold_day <= today else _("Held on %(day)s, %(when)s. Card holds only last about 7 days.") % {"day": day_text(hold_day, True), "when": dict(CateringSettings.HOLDS)[cfg.hold_days]}}
    elif sheet == "reject" and order.status in CateringOrder.OPEN:
        confirmed, cancelling = order.status == CateringOrder.CONFIRMED, order.status != CateringOrder.NEW
        d["sheet"] = {"kind": "reject",
                      "title": _("Cancel and refund this order?") if confirmed else _("Cancel this order?") if cancelling else _("Reject %(name)s’s order?") % {"name": first},
                      "sub": _("%(t)s goes back to %(card)s and any deposit hold is dropped.") % {"t": money(order.charged or t["total"]), "card": order.card} if confirmed else _("Nothing has been charged. %(name)s gets an email with your message.") % {"name": first},
                      "reasons": flow.REASONS, "default": _("Not enough notice") if order.status == CateringOrder.NEW and ahead < need else "",
                      "button": _("Cancel and refund") if confirmed else _("Cancel order") if cancelling else _("Reject order")}
    return d


# ---------- settings ----------

SWITCHES = ("on", "big_on", "rush", "pickup", "delivery")


def settings_draft(cfg) -> dict:
    return {"on": cfg.on, "lead": str(cfg.lead), "lead_unit": cfg.lead_unit, "max_ahead": str(cfg.max_ahead),
            "big_on": cfg.big_on, "big_hours": str(cfg.big_hours), "rush": cfg.rush, "min_subtotal": f"{cfg.min_subtotal.normalize():f}",
            "days": [bool(x) for x in (cfg.days or [True] * 7)][:7], "earliest": cfg.earliest.strftime("%H:%M"),
            "latest": cfg.latest.strftime("%H:%M"), "pickup": cfg.pickup, "delivery": cfg.delivery,
            "hold_days": str(cfg.hold_days), "policy": cfg.policy, "auto_reply": cfg.auto_reply,
            "services": sorted(Service.objects.filter(catering=True, archived=False).values_list("id", flat=True))}


def settings_from_post(post) -> dict:
    return {**{k: post.get(k) == "1" for k in SWITCHES},
            "lead": post.get("lead", ""), "lead_unit": CateringSettings.HOURS if post.get("lead_unit") == CateringSettings.HOURS else CateringSettings.DAYS,
            "max_ahead": post.get("max_ahead", ""), "big_hours": post.get("big_hours", ""), "min_subtotal": post.get("min_subtotal", ""),
            "days": [post.get(f"day-{i}") == "1" for i in range(7)], "earliest": post.get("earliest", ""), "latest": post.get("latest", ""),
            "hold_days": post.get("hold_days", "1"), "policy": post.get("policy", "")[:500], "auto_reply": post.get("auto_reply", "")[:400],
            "services": sorted(_int(x) for x in post.getlist("service") if _int(x))}


def settings_op(draft, op) -> dict:
    name, _sep, arg = op.partition(":")
    if name == "toggle" and arg in SWITCHES:
        draft[arg] = not draft[arg]
    elif name == "day" and 0 <= _int(arg, -1) < 7:
        draft["days"][_int(arg)] = not draft["days"][_int(arg)]
    elif name == "unit" and arg in (CateringSettings.HOURS, CateringSettings.DAYS):
        draft["lead_unit"] = arg
    elif name == "service" and _int(arg):
        ids = set(draft["services"])
        ids ^= {_int(arg)}
        draft["services"] = sorted(ids)
    return draft


def _canonical(draft):
    out = dict(draft)
    for key in ("lead", "max_ahead", "big_hours", "hold_days"):
        out[key] = _int(out[key], -1)
    out["min_subtotal"] = _dec(out["min_subtotal"])
    return out


def settings_dirty(draft) -> bool:
    return _canonical(draft) != _canonical(settings_draft(CateringSettings.load()))


def _time(value):
    try:
        return datetime.time.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def settings_problems(draft):
    if _int(draft["lead"]) < 1:
        return _("Minimum notice has to be at least 1.")
    if _int(draft["max_ahead"]) < 1:
        return _("Customers have to be able to book at least a day ahead.")
    if draft["big_on"] and _int(draft["big_hours"]) < 1:
        return _("Add at least 1 hour of kitchen time per extra day.")
    if not any(draft["days"]):
        return _("Pick at least one day for events.")
    start, end = _time(draft["earliest"]), _time(draft["latest"])
    if not start or not end or start >= end:
        return _("The latest time has to be after the earliest.")
    if not (draft["pickup"] or draft["delivery"]):
        return _("Turn on at least one way for customers to get their order.")
    if _dec(draft["min_subtotal"]) < 0:
        return _("The minimum order can’t be negative.")
    return ""


def save_settings(draft):
    cfg = CateringSettings.load()
    for key in SWITCHES:
        setattr(cfg, key, draft[key])
    cfg.lead, cfg.lead_unit, cfg.max_ahead = min(_int(draft["lead"]), 999), draft["lead_unit"], min(_int(draft["max_ahead"]), 730)
    cfg.big_hours = min(max(_int(draft["big_hours"], 8), 1), 999)
    cfg.min_subtotal = min(_dec(draft["min_subtotal"]), Decimal("999999"))
    cfg.days, cfg.earliest, cfg.latest = draft["days"], _time(draft["earliest"]), _time(draft["latest"])
    cfg.hold_days = min(max(_int(draft["hold_days"], 1), 0), 2)
    cfg.policy, cfg.auto_reply = draft["policy"].strip(), draft["auto_reply"].strip()
    cfg.save()
    Service.objects.filter(archived=False).filter(~Q(id__in=draft["services"])).update(catering=False)
    Service.objects.filter(archived=False, id__in=draft["services"]).update(catering=True)


def settings_page(draft) -> dict:
    today = timezone.localdate()
    lead = _int(draft["lead"], 1) or 1
    lead_days = lead if draft["lead_unit"] == CateringSettings.DAYS else lead / 24
    earliest = today + datetime.timedelta(days=int(-(-lead_days // 1)))
    latest = today + datetime.timedelta(days=max(_int(draft["max_ahead"], 1), 1))
    preview = _("Ordering today, the earliest event a customer can pick is %(first)s, and the latest is %(last)s.") % {
        "first": day_text(earliest, True), "last": day_text(latest, True) + (f", {latest.year}" if latest.year != today.year else "")}
    example = ""
    big = max(CateringOrder.objects.filter(status__in=CateringOrder.OPEN).prefetch_related("lines"),
              key=lambda o: pricing.order_totals(o)["minutes"], default=None)
    if big is not None and draft["big_on"] and _int(draft["big_hours"]) > 0:
        minutes = pricing.order_totals(big)["minutes"]
        base = int(-(-lead_days // 1))
        example = _("Example: %(title)s is about %(t)s of kitchen time, so it needs %(need)s of notice instead of %(base)s.") % {
            "title": big.title, "t": pricing.kitchen_text(minutes), "need": plural_days(base + int(minutes // 60 // _int(draft["big_hours"]))),
            "base": plural_days(base)}
    dishes = Dish.objects.filter(catering=True, archived=False).prefetch_related("catering_prices").order_by("title")
    groups = []
    services = list(Service.objects.filter(archived=False).order_by("title"))
    for key, label in Service.CATEGORIES:
        items = [{"s": s, "on": s.id in draft["services"], "price": _service_price(s)} for s in services if s.category == key]
        if items:
            groups.append({"title": label, "items": items})
    on_count = sum(1 for s in services if s.id in draft["services"])
    return {"d": draft, "dirty": settings_dirty(draft), "problem": settings_problems(draft), "preview": preview, "example": example,
            "units": [(CateringSettings.HOURS, _("Hours")), (CateringSettings.DAYS, _("Days"))],
            "days": [{"i": i, "label": label, "on": draft["days"][i]} for i, label in enumerate([_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")])],
            "switches": [(k, draft[k]) for k in SWITCHES],
            "fulfilment": [("pickup", _("Pickup"), _("They collect it from the store."), draft["pickup"]),
                           ("delivery", _("Delivery"), _("You bring it to them. Your delivery service is added to the quote; staff and setup are booked as services."), draft["delivery"])],
            "dishes": [{"d": d, "price": d.catering_price(d.catering_min)} for d in dishes],
            "dish_sub": ngettext("%(n)d dish is on for catering. Turn dishes on or off, and set quantity prices, under Catering pricing on each dish.",
                                 "%(n)d dishes are on for catering. Turn dishes on or off, and set quantity prices, under Catering pricing on each dish.", len(dishes)) % {"n": len(dishes)},
            "service_groups": groups,
            "service_sub": _("%(n)d of %(all)d services can be added to catering orders.") % {"n": on_count, "all": len(services)},
            "holds": CateringSettings.HOLDS}


def _service_price(s):
    per = {Service.HOUR: _(" an hour"), Service.DAY: _(" a day"), Service.FLAT: _(" flat"), Service.PERSON: _(" a guest"), Service.UNIT: _(" each")}
    text = money(s.price) + per.get(s.pricing, "")
    if s.category == Service.EQUIPMENT and s.deposit and s.deposit_amount:
        text += " · " + (_("%(d)s deposit per booking") if s.deposit_per == Service.PER_BOOKING else _("%(d)s deposit each")) % {"d": money(s.deposit_amount)}
    return text
