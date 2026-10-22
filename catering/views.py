"""
The online store's catering pages, drawn by the store's theme (online_store.views.theme_page):

- /catering/ (quote_page; design: StoreCatering): build a quote, then send it with a card on file
  (payments/save-card). catering/store.py keeps the quote in the session and checks it.
- /catering/<token> (order_page; design: StoreCateringOrder): the request after it's sent, linked from every
  catering email. Its versions (CateringVersion) side by side, what changed, accept the store's changes, suggest
  your own (a draft like the admin's, admin_portal/catering.py), cancel, and messages. The token is the key, like a
  gift card's number: no account needed.
"""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from admin_portal import catering as drafts
from admin_portal.models import StoreSettings
from pos_server import dish_info
from online_store import storefront
from online_store.views import theme_page

from . import flow, payments, pricing, store
from .models import CateringLine, CateringMessage, CateringOrder, CateringSettings, CateringVersion

FOOD_TONES = 4


# ---------- the quote ----------

def quote_page(request):
    cfg = CateringSettings.load()
    q = store.load(request)
    if request.GET.get("add"):
        q = store.apply_op(q, f"sadd:{request.GET['add']}", {})
        store.save(request, q)
        return redirect("store_catering")
    if request.method == "POST":
        op = request.POST.get("op", "")
        q = store.apply_op(q, op, request.POST)
        store.save(request, q)
        if op == "send":
            result = _send(request, q)
            if result:
                return redirect("catering_order", token=result.token)
        elif op == "drop_card":
            request.session.pop("saved_card", None)
        elif op.startswith("use_card:") and request.user.is_authenticated:
            saved = request.user.saved_cards.filter(pk=op[9:]).first()
            if saved:
                request.session["saved_card"] = saved.as_card()
    st = store.state(q)
    foods = []
    stations = {}
    for dish in store.dishes():
        heading = dish.station.menu_heading or dish.station.name
        stations.setdefault(heading, []).append(dish)
    cats = [("all", _("Everything"))] + [(h, h) for h in stations]
    for heading, items in stations.items():
        if q["cat"] not in ("all", heading):
            continue
        for dish in items:
            qty = q["dishes"].get(str(dish.id), 0)
            tiers = list(dish.catering_prices.all())
            best = tiers[-1] if len(tiers) > 1 else None
            foods.append({"dish": dish, "qty": qty, "tone": len(foods) % FOOD_TONES, "drawing": storefront.drawing(dish.title),
                          "diets": [d["short"] for d in dish_info.dish_diets(dish) if d["short"] in ("V", "VG", "GF")],
                          "price": pricing.money(dish.catering_price(dish.catering_min)) + " " + _("each")
                                   + (f" · {pricing.money(best.price)} " + _("for %(n)d+") % {"n": best.quantity} if best else "")
                                   + " · " + _("min %(n)d") % {"n": dish.catering_min},
                          "total": pricing.money(dish.catering_price(qty) * qty) if qty else ""})
    groups = []
    for key, label in store.Service.CATEGORIES:
        items = []
        for s in store.services(q):
            if s.category != key:
                continue
            x = q["services"].get(str(s.id))
            locked = st["delivery_fee"] and s.id == st["delivery_fee"].id
            line = next((l for l in st["lines"] if l["kind"] == CateringLine.SERVICE and l["ref"] == s.id), None)
            f = pricing.figure(line, st["catalog"]) if line else None
            items.append({"s": s, "x": x, "locked": locked, "timed": s.pricing in (s.HOUR, s.DAY),
                          "unit": _("staff") if s.counts_units and s.category == s.STAFF else _("people") if s.pricing == s.PERSON else "",
                          "deposit": pricing.money(s.deposit_amount) if s.deposit and s.deposit_amount else "",
                          "price": storefront.service_price(s), "total": pricing.money(f["total"]) if f else "",
                          "amount_text": f"{x['h']} h" if x and s.pricing == s.HOUR else f"{x['h']} d" if x and s.pricing == s.DAY else ""})
        if items:
            groups.append({"title": label, "items": items})
    quote_groups = []
    for kind, title in ((CateringLine.DISH, _("Food")), (CateringLine.SERVICE, _("Services"))):
        rows = [{"name": pricing.figure(l, st["catalog"])["name"], "qty": pricing.figure(l, st["catalog"])["qty_text"],
                 "total": pricing.money(pricing.figure(l, st["catalog"])["total"])} for l in st["lines"] if l["kind"] == kind]
        if rows:
            quote_groups.append({"title": title, "rows": rows})
    fuls = [(way, label, desc) for way, label, desc in (
        (CateringOrder.PICKUP, _("Pickup"), _("Collect it from the store, packed and labelled.")),
        (CateringOrder.DELIVERY, _("Delivery"), _("We drop it off.") + (" " + _("Adds %(fee)s.") % {"fee": pricing.money(st["delivery_fee"].price)} if st["delivery_fee"] else "")),
        (CateringOrder.FULL, _("Full service"), _("We deliver, set up and serve. Add staff below.")))
        if getattr(cfg, store.FULS[way])]
    rules = [
        ("M12 7v5l3 2 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", _("%(n)d days’ notice") % {"n": round(cfg.lead_days)} if cfg.lead_unit == cfg.DAYS else _("%(n)d hours’ notice") % {"n": cfg.lead}, _("Very large orders need a day or two more.")),
        ("M4 7h16v12H4z M4 11h16 M8 15h3", _("%(min)s minimum") % {"min": pricing.money(cfg.min_subtotal).replace(".00", "")}, _("Before tax. Each dish has its own minimum too.")),
        ("M5 4h14v16H5z M5 9h14 M9 2v4 M15 2v4", _("Up to %(n)d days out") % {"n": cfg.max_ahead}, _("Events from %(window)s.") % {"window": st["window"]}),
        ("M3 7h11v9H3z M14 10h4l3 3v3h-7 M7 19a2 2 0 1 0 0-.01 M17 19a2 2 0 1 0 0-.01", _("Your way"), ", ".join(str(f[1]) for f in fuls) + "."),
        ("M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z", _("Nothing charged yet"), _("We keep your card on file and charge it once you approve the final quote.")),
    ]
    return theme_page(request, "catering.html", {
        "q": q, "st": st, "cfg": cfg, "foods": foods, "cats": cats, "svc_groups": groups, "quote_groups": quote_groups,
        "fuls": fuls, "rules": rules, "card": request.session.get("saved_card"), "sending": request.POST.get("op", "").startswith(("review", "agree", "drop_card", "use_card", "send")),
        "saved_cards": list(request.user.saved_cards.all()) if request.user.is_authenticated else [],
        "off": not cfg.on,
    }, active="catering")


def _send(request, q):
    st = store.state(q)
    card = request.session.get("saved_card")
    if st["blocker"]:
        messages.error(request, st["blocker"])
        return None
    if not CateringSettings.load().on:
        messages.error(request, _("We’re not taking catering orders right now."))
        return None
    if not card:
        messages.error(request, _("Add a card first. Nothing is charged until you approve the final quote."))
        return None
    if not q["agree"]:
        messages.error(request, _("Agree to the cancellation policy to send it."))
        return None
    order = store.create(q, card, request.user if request.user.is_authenticated else None)
    flow.place(order, request=request)
    store.clear(request)
    request.session.pop("saved_card", None)
    messages.success(request, _("Request sent. We’ll reply by email, usually within a business day."))
    return order


# ---------- a request ----------

def order_page(request, token):
    order = get_object_or_404(CateringOrder, token=token)
    editing = order.status in (CateringOrder.NEW, CateringOrder.WAITING) and (request.GET.get("edit") == "1" or request.POST.get("editing") == "1")
    draft = None
    if request.method == "POST":
        op = request.POST.get("op", "")
        try:
            if op == "reply":
                flow.write(order, CateringMessage.CUSTOMER, request.POST.get("text", ""), request=request)
                messages.success(request, _("Sent. We’ll get back to you soon."))
            elif op == "agree":
                flow.agree(order, request=request)
                messages.success(request, _("You’re all set. Your card was charged and the order is confirmed."))
            elif op == "cancel":
                flow.customer_cancel(order, request=request)
                messages.success(request, _("Request cancelled. Nothing was charged."))
            elif op == "send":
                flow.counter(order, drafts.draft_from_post(request.POST)["lines"], request.POST.get("msg", ""), request=request)
                messages.success(request, _("Your changes are on their way. Nothing is charged until you both agree."))
            elif editing:
                draft = drafts.apply_op(drafts.draft_from_post(request.POST), op, order)
                draft["add_kind"] = CateringLine.DISH
        except (flow.CateringError, payments.PaymentFailed) as exc:
            messages.error(request, str(exc))
            if op == "send":
                draft = drafts.draft_from_post(request.POST)
        if draft is None:
            return redirect("catering_order", token=order.token)
    if editing and draft is None:
        draft = drafts.draft_from_order(order)
    return theme_page(request, "catering_order.html", view(request, order, draft), active="catering")


def _version_rows(lines, prev, catalog, editing):
    return drafts._rows(lines, prev, catalog, editing)


def view(request, order, draft=None) -> dict:
    store_name = StoreSettings.load().name
    versions = list(order.versions.all())
    current = pricing.lines_of(order)
    if not versions:
        versions = [CateringVersion(order=order, number=1, by=CateringVersion.CUSTOMER, lines=current, at=order.placed_at)]
    last = versions[-1]
    editing = draft is not None
    chosen = next((v for v in versions if str(v.number) == request.GET.get("v", "")), last)
    diff_on = editing or request.GET.get("diff", "1") != "0"
    if editing:
        shown, prev_lines = draft["lines"], current
    else:
        shown = current if chosen is last else chosen.lines
        before = next((v for v in versions if v.number == chosen.number - 1), None)
        prev_lines = before.lines if before and diff_on else None
    catalog = pricing.Catalog(shown, prev_lines, *[v.lines for v in versions])
    t = pricing.totals(shown, catalog)
    tp = pricing.totals(prev_lines, catalog) if prev_lines is not None else None
    chips = []
    if prev_lines is not None:
        chips = [{"t": c[0], "kind": c[1]} for c in pricing.changes(prev_lines, shown, catalog)]
        if chips:
            chips.append({"t": _("Total %(a)s → %(b)s") % {"a": pricing.money(tp["total"]), "b": pricing.money(t["total"])}, "kind": "tot"})
    status = order.status
    first_total = versions[0].total or pricing.totals(versions[0].lines, catalog)["total"]
    when = f"{order.event_date:%a, %b} {order.event_date.day} · {storefront.clock(order.event_time)}"
    days = (order.event_date - timezone.localdate()).days
    ful = order.get_fulfilment_display()
    facts = [(_("When"), when, _("In %(n)d days") % {"n": days} if days > 1 else _("Tomorrow") if days == 1 else _("Today") if days == 0 else _("Past")),
             (_("Pickup") if order.fulfilment == CateringOrder.PICKUP else _("%(way)s to") % {"way": ful}, order.address or _("At the store"), order.organization),
             (_("Contact"), order.name, order.phone or order.email),
             (_("Request"), order.number, _("Sent %(day)s") % {"day": f"{timezone.localtime(order.placed_at):%b} {timezone.localtime(order.placed_at).day}"})]
    proposal = status == CateringOrder.WAITING and not editing
    with_them = status == CateringOrder.NEW and not editing
    if editing:
        kind, head = "editing", _("Suggest your own changes")
        text = _("Change quantities, remove anything you don’t want or add a dish. %(store)s reviews your version, and nothing is charged until you both agree.") % {"store": store_name}
    elif status == CateringOrder.WAITING:
        kind, head = "proposal", _("%(store)s suggested a few changes") % {"store": store_name}
        text = _("The total went from %(a)s to %(b)s. Accept it, suggest your own changes, or cancel.") % {"a": pricing.money(pricing.totals(versions[-2].lines, catalog)["total"]) if len(versions) > 1 else pricing.money(first_total), "b": pricing.money(pricing.totals(current, catalog)["total"])}
    elif status == CateringOrder.NEW:
        kind, head = "waiting", _("Sent. Waiting on %(store)s.") % {"store": store_name}
        text = _("You sent version %(n)d with a total of %(t)s. We usually reply within a business day.") % {"n": last.number, "t": pricing.money(pricing.totals(current, catalog)["total"])}
    elif status in (CateringOrder.CONFIRMED, CateringOrder.DONE):
        kind, head = "confirmed", (_("You’re booked. See you %(day)s.") % {"day": f"{order.event_date:%B} {order.event_date.day}"}) if status == CateringOrder.CONFIRMED else _("Thanks for having us!")
        text = _("We charged %(t)s to %(card)s.") % {"t": pricing.money(order.charged), "card": order.card}
    else:
        kind, head = "cancelled", _("Request cancelled") if status == CateringOrder.CANCELLED else _("We couldn’t take this one")
        text = (_("%(t)s is on its way back to your card.") % {"t": pricing.money(order.refunded)}) if order.refunded else _("Nothing was charged.")
    tabs = [] if editing else [{"n": v.number, "on": v.number == chosen.number,
                                "label": _("Version %(n)d") % {"n": v.number},
                                "sub": ((_("Your request") if v.number == 1 else _("Your changes")) if v.by == CateringVersion.CUSTOMER else _("Proposed by %(store)s") % {"store": store_name}) + f" · {timezone.localtime(v.at):%b} {timezone.localtime(v.at).day}",
                                "tag": (_("Accepted") if status in (CateringOrder.CONFIRMED, CateringOrder.DONE) else "" if status in (CateringOrder.CANCELLED, CateringOrder.REJECTED) else _("Current")) if v is last else _("Original") if v.number == 1 else ""} for v in reversed(versions)]
    history = []
    for v in versions:
        who = (_("You sent your request (version 1)") if v.number == 1 else _("You suggested version %(n)d") % {"n": v.number}) if v.by == CateringVersion.CUSTOMER else _("%(store)s proposed version %(n)d") % {"store": store_name, "n": v.number}
        history.append({"what": who, "when": f"{timezone.localtime(v.at):%b} {timezone.localtime(v.at).day}", "state": "done"})
    if status in (CateringOrder.CONFIRMED, CateringOrder.DONE):
        history.append({"what": _("You’re confirmed and were charged %(t)s") % {"t": pricing.money(order.charged)}, "when": f"{timezone.localtime(order.accepted_at or order.placed_at):%b} {timezone.localtime(order.accepted_at or order.placed_at).day}", "state": "done"})
    elif status in (CateringOrder.CANCELLED, CateringOrder.REJECTED):
        history.append({"what": order.get_status_display(), "when": f"{timezone.localtime(order.closed_at or order.placed_at):%b} {timezone.localtime(order.closed_at or order.placed_at).day}", "state": "done"})
    elif status == CateringOrder.WAITING:
        history.append({"what": _("Waiting for you to accept, suggest changes or cancel"), "when": _("Now"), "state": "now"})
    else:
        history.append({"what": _("Waiting for %(store)s to accept or reply") % {"store": store_name}, "when": _("Now"), "state": "now"})
    if status not in (CateringOrder.CANCELLED, CateringOrder.REJECTED):
        history.append({"what": _("Event day"), "when": when, "state": "done" if status == CateringOrder.DONE else "next"})
    events = {CateringMessage.PLACED: _("Request sent"), CateringMessage.CHANGES: _("%(store)s proposed new changes") % {"store": store_name},
              CateringMessage.COUNTER: _("You suggested changes"), CateringMessage.ACCEPTED: _("%(store)s confirmed the order") % {"store": store_name},
              CateringMessage.AGREED: _("You accepted the changes"), CateringMessage.REJECTED: _("%(store)s couldn’t take the order") % {"store": store_name},
              CateringMessage.CANCELLED: _("The request was cancelled"), CateringMessage.DECLINED: _("You declined the changes")}
    thread = []
    for m in order.messages.all():
        at = timezone.localtime(m.at)
        stamp = f"{at:%b} {at.day}, {storefront.clock(at.time())}"
        if m.who == CateringMessage.SYSTEM:
            if m.text.startswith(_("Auto-reply sent")):
                continue
            thread.append({"event": True, "text": m.text, "at": f"{at:%b} {at.day}"})
            continue
        if m.kind in events:
            thread.append({"event": True, "text": events[m.kind], "at": f"{at:%b} {at.day}"})
        body = order.note if m.kind == CateringMessage.PLACED else m.text
        if body:
            thread.append({"event": False, "mine": m.who == CateringMessage.CUSTOMER, "text": body, "at": stamp,
                           "who": _("You") if m.who == CateringMessage.CUSTOMER else ((m.user.first_name if m.user else "") + f", {store_name}").strip(", ")})
    pay_head = (_("Charged %(t)s") % {"t": pricing.money(order.charged)}) if order.charged else _("Card on file")
    pay_sub = f"{order.card} · " + (_("refunded") if order.refunded else _("charged") if order.charged else _("not charged"))
    pay_note = (_("The equipment deposit is held the day before the event and released once everything is back.") if t["deposit"] and status == CateringOrder.CONFIRMED
                else _("We charge the final total once you both agree.") if status in (CateringOrder.NEW, CateringOrder.WAITING) else "")
    add_options = []
    if editing:
        used = {l["ref"] for l in draft["lines"] if l["kind"] == CateringLine.DISH and not l.get("removed")}
        add_options = [{"id": d.id, "label": f"{d.title} · {pricing.money(d.catering_price(d.catering_min))} " + _("each · min %(n)d") % {"n": d.catering_min}}
                       for d in store.dishes() if d.id not in used]
    return {"o": order, "facts": facts, "kind": kind, "head": head, "text": text, "proposal": proposal, "with_them": with_them,
            "editing": editing, "draft": draft, "tabs": tabs, "chips": chips, "diff_on": diff_on, "can_diff": not editing and chosen.number > 1,
            "chosen": chosen.number, "groups": _version_rows(shown, prev_lines, catalog, editing), "tot": t, "tp": tp,
            "history": history, "thread": thread, "pay_head": pay_head, "pay_sub": pay_sub, "pay_note": pay_note,
            "add_options": add_options, "store_name": store_name, "new_total": pricing.money(pricing.totals(current, catalog)["total"]),
            "open": status in CateringOrder.OPEN, "policy": CateringSettings.load().policy}
