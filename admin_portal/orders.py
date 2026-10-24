"""
Retail orders (design: AdminOrders): the list with date and status filters, and one order's page.
Retail means every Order: register, online pickup and delivery. Refund rules live in pos_server.refunds.
"""
import datetime
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy, ngettext

from pos_server import refunds
from pos_server.models import Order, OrderPayment, ReceiptLog, Refund

PER_PAGE = 10
DATES = [("today", gettext_lazy("Today")), ("yesterday", gettext_lazy("Yesterday")), ("week", gettext_lazy("Last 7 days")),
         ("all", gettext_lazy("All"))]
STATUSES = [("all", gettext_lazy("All")), ("paid", gettext_lazy("Paid")), ("refunds", gettext_lazy("Refunds")),
            ("voided", gettext_lazy("Voided"))]
STATUS_LABELS = {"paid": gettext_lazy("Paid"), "partial": gettext_lazy("Part refunded"),
                 "refunded": gettext_lazy("Refunded"), "voided": gettext_lazy("Voided")}
CHANNELS = {"store": gettext_lazy("In person"), "web": gettext_lazy("Online pickup"), "delivery": gettext_lazy("Delivery")}
ICONS = {
    "card": "M3 6h18v12H3z M3 10h18 M7 15h4",
    "tap": "M8.5 8.5a5 5 0 0 1 0 7 M11.5 6a8.5 8.5 0 0 1 0 12 M5.5 11a1.5 1.5 0 0 1 0 2",
    "cash": "M3 7h18v10H3z M12 9.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z M6 10v4 M18 10v4",
    "gift": "M4 11h16v9H4z M4 7h16v4H4z M12 7v13 M12 7c-2-3-6-3-6 0 M12 7c2-3 6-3 6 0",
    "refund": "M9 14 4 9l5-5 M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
    "print": "M7 9V4h10v5 M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2 M7 14h10v6H7z",
    "send": "M4 12 20 4l-6 16-3-7z M11 13l9-9",
    "cart": "M4 5h2l2 10h10l2-7H7 M10 19h.01 M17 19h.01",
    "fire": "M12 3c1 3 4 4.5 4 8.5a4 4 0 0 1-8 0c0-2 1-3 1.5-4 .5 1.5 1.5 2 2 2C11.5 7 11 5 12 3z",
    "check": "M5 12.5 9.5 17 19 7.5",
    "void": "M6 6l12 12 M18 6 6 18",
}


def day_start(day):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def day_label(when):
    today = timezone.localdate()
    if when.date() == today:
        return _("Today")
    if when.date() == today - datetime.timedelta(days=1):
        return _("Yesterday")
    return date_format(when, "D, M j")


def pay_label(payments):
    """('cash'|'card'|'tap'|'gift', 'Tap · Visa 4242') for the list: the biggest payment, '+ N' when split."""
    if not payments:
        return "card", _("Unpaid")
    main = max(payments, key=lambda p: p.amount)
    if main.method == OrderPayment.CASH:
        kind, text = "cash", _("Cash")
    elif main.method == OrderPayment.GIFT:
        kind, text = "gift", _("Gift card ··%(last4)s") % {"last4": main.gift_card.number[-4:]} if main.gift_card else _("Gift card")
    elif main.method == OrderPayment.STAFF:
        kind, text = "gift", (_("%(card)s ··%(last4)s") % {"card": main.staff_card.type.name, "last4": main.staff_card.number[-4:]}
                              if main.staff_card else _("Staff card"))
    else:
        kind = "tap" if main.entry_method == "tap" else "card"
        text = main.card_label.replace(" •••• ", " ") if main.card_last4 else (_("Online card") if main.entry_method == "online" else _("Card"))
        if main.entry_method == "tap":
            text = _("Tap") + " · " + text
    if len(payments) > 1:
        text += f" + {len(payments) - 1}"
    return kind, text


def base_queryset():
    return (Order.objects.annotate(refunded=Sum("refunds__amount"))
            .select_related("device", "cashier", "customer")
            .prefetch_related("items", "payments__gift_card", "payments__staff_card__type"))


def status_of(order) -> str:
    if order.voided_at:
        return "voided"
    refunded = order.refunded or Decimal("0")
    return "refunded" if refunded and refunded >= order.total else "partial" if refunded else "paid"


def in_status(order, key):
    st = status_of(order)
    return key == "all" or (key == "refunds" and st in ("refunded", "partial")) or (key == "voided" and st == "voided") or (key == "paid" and st == "paid")


def order_list(date="today", status="all", query="", page=1):
    date = date if date in dict(DATES) else "today"
    status = status if status in dict(STATUSES) else "all"
    today = timezone.localdate()
    orders = base_queryset().order_by("-timestamp", "-id")
    if date == "today":
        orders = orders.filter(timestamp__gte=day_start(today))
    elif date == "yesterday":
        orders = orders.filter(timestamp__gte=day_start(today - datetime.timedelta(days=1)), timestamp__lt=day_start(today))
    elif date == "week":
        orders = orders.filter(timestamp__gte=day_start(today - datetime.timedelta(days=6)))
    q = (query or "").strip().lstrip("#")
    if q:
        match = (Q(name__icontains=q) | Q(cashier__first_name__icontains=q) | Q(cashier__username__icontains=q) | Q(payments__card_last4=q)
                 | Q(email__iexact=q) | Q(customer__email__iexact=q))  # the Customers page links here by email
        if q.isdigit():
            match |= Q(id=int(q))
        orders = orders.filter(match).distinct()
    found = list(orders)
    counts = {key: sum(1 for o in found if in_status(o, key)) for key, _l in STATUSES}
    shown = [o for o in found if in_status(o, status)]
    pages = max(1, -(-len(shown) // PER_PAGE))
    page = min(max(1, page), pages)
    rows = []
    for order in shown[(page - 1) * PER_PAGE: page * PER_PAGE]:
        when = timezone.localtime(order.timestamp)
        items = [i for i in order.items.all() if not i.parent_id]
        count = sum(i.quantity for i in items)
        kind, text = pay_label(list(order.payments.all()))
        st = status_of(order)
        rows.append({
            "order": order, "day": day_label(when), "time": date_format(when, "g:i A"),
            "name": order.name or (_("Walk-in") if order.channel == "store" else _("Guest")),
            "summary": ngettext("%(n)d item", "%(n)d items", count) % {"n": count} + " · " + ", ".join(i.title for i in items),
            "register": order.device.name if order.device else CHANNELS.get(order.channel, order.channel),
            "cashier": (order.cashier.first_name or order.cashier.username) if order.cashier else "—",
            "pay_icon": ICONS[kind], "pay": text, "cash": kind == "cash",
            "status": st, "status_label": STATUS_LABELS[st],
        })
    net = sum((o.total - (o.refunded or 0) for o in found if not o.voided_at), Decimal("0"))
    nums = []
    for k in range(1, pages + 1):
        if k in (1, pages) or abs(k - page) <= 1:
            nums.append(k)
        elif nums[-1] != "…":
            nums.append("…")
    return {"date": date, "status": status, "query": query or "", "rows": rows, "page": page, "pages": pages, "nums": nums,
            "counts": counts, "total": len(shown), "first": (page - 1) * PER_PAGE + 1 if shown else 0,
            "last": min(len(shown), page * PER_PAGE), "net": net,
            "dates": DATES, "statuses": [(key, label, counts[key]) for key, label in STATUSES]}


def neighbours(order):
    newer = Order.objects.filter(Q(timestamp__gt=order.timestamp) | Q(timestamp=order.timestamp, id__gt=order.id)).order_by("timestamp", "id").first()
    older = Order.objects.filter(Q(timestamp__lt=order.timestamp) | Q(timestamp=order.timestamp, id__lt=order.id)).order_by("-timestamp", "-id").first()
    position = Order.objects.filter(Q(timestamp__gt=order.timestamp) | Q(timestamp=order.timestamp, id__gt=order.id)).count() + 1
    return newer, older, position, Order.objects.count()


def order_page(order):
    order = (Order.objects.select_related("device", "cashier", "customer", "authorization", "voided_by")
             .prefetch_related("items__station", "items__children", "payments__gift_card", "payments__staff_card__type", "discounts__approved_by", "refunds__items",
                               "refunds__by", "receipts__by", "station_statuses__station").get(pk=order.pk))
    st = refunds.status(order)
    refunded = refunds.refunded_total(order)
    done = refunds.refunded_counts(order)
    items = []
    for item in order.items.all():
        if item.parent_id:
            continue
        children = [c for c in item.children.all()]
        mods = " · ".join(([f"{c.slot}: {c.title}" if c.slot else c.title for c in children]) + list(item.mods or []))
        n = done.get(item.id, 0)
        station = item.station or (children[0].station if children else None)
        items.append({"item": item, "mods": mods, "station": station, "combo": not item.dish_id and bool(children),
                      "refunded": n, "refund_text": _("Refunded") if n >= item.quantity else _("%(n)d of %(q)d refunded") % {"n": n, "q": item.quantity} if n else ""})
    count = sum(i["item"].quantity for i in items)

    totals = [{"label": _("Subtotal"), "value": order.subtotal}]
    for d in order.discounts.all():
        by = d.approved_by.first_name or d.approved_by.username if d.approved_by else ""
        totals.append({"label": _("%(label)s · approved by %(name)s") % {"label": d.label, "name": by} if by else d.label, "value": -d.amount})
    if order.tax:
        totals.append({"label": _("Tax"), "value": order.tax})
    for fee in order.fee_lines.all():
        totals.append({"label": f"{fee.label} × {fee.quantity}" if fee.quantity > 1 else fee.label, "value": fee.amount})
    if order.tip:
        totals.append({"label": _("Tip"), "value": order.tip})
    for card in order.gift_cards_sold.all():
        totals.append({"label": _("Gift card •• %(last4)s") % {"last4": card.last4}, "value": card.original})
    totals.append({"label": _("Total"), "value": order.total, "big": True})
    if refunded:
        totals.append({"label": _("Refunded"), "value": -refunded, "red": True})
        if refunded < order.total:
            totals.append({"label": _("Net"), "value": order.total - refunded, "big": True})

    when = timezone.localtime(order.timestamp)
    stations = sorted({row.station for row in order.station_statuses.all()}, key=lambda s: (s.position, s.id))
    station_rows = list(order.station_statuses.all())
    payments = list(order.payments.all())
    timeline = [{"at": order.timestamp, "title": _("Order rung up") if order.channel == "store" else _("Order placed online"),
                 "sub": (_("By %(who)s on %(where)s") % {"who": order.cashier.first_name or order.cashier.username,
                                                          "where": order.device.name if order.device else _("the register")}
                         if order.cashier else str(CHANNELS.get(order.channel))) + " · " + ngettext("%(n)d item", "%(n)d items", count) % {"n": count},
                 "icon": ICONS["cart"], "tone": "n"}]
    confirmed = [r.confirmed_at for r in station_rows if r.confirmed_at]
    if confirmed and not order.voided_at:
        timeline.append({"at": max(confirmed), "title": _("Sent to the kitchen"), "icon": ICONS["fire"], "tone": "n",
                         "sub": _("%(stations)s confirmed") % {"stations": ", ".join(s.name for s in stations)}})
    ready = [r.ready_at for r in station_rows if r.ready_at]
    if ready and len(ready) == len(station_rows):
        timeline.append({"at": max(ready), "title": _("Ready"), "icon": ICONS["check"], "tone": "g",
                         "sub": _("Prep time %(t)s") % {"t": mmss(order.prep_time)} if order.prep_time else ""})
    for p in payments:
        kind, text = pay_label([p])
        sub = _("Tendered %(t)s · change %(c)s") % {"t": f"${p.tendered:.2f}", "c": f"${p.change:.2f}"} if p.method == OrderPayment.CASH and p.tendered else text
        timeline.append({"at": p.timestamp, "title": _("Paid %(amount)s") % {"amount": f"${p.amount:.2f}"}, "sub": sub, "icon": ICONS[kind], "tone": "g"})
    for r in order.receipts.all():
        timeline.append({"at": r.at, "title": _("Receipt printed") if r.channel == ReceiptLog.PRINT else _("Receipt sent"),
                         "sub": r.to or (r.by.first_name if r.by else ""), "icon": ICONS["print" if r.channel == ReceiptLog.PRINT else "send"], "tone": "n"})
    for r in order.refunds.all():
        timeline.append({"at": r.created_at, "title": _("Refunded %(amount)s") % {"amount": f"${r.amount:.2f}"}, "icon": ICONS["refund"], "tone": "x",
                         "sub": _("%(reason)s · by %(who)s · to %(where)s") % {"reason": r.reason, "who": (r.by.first_name or r.by.username) if r.by else "—",
                                                                             "where": _("cash") if r.to == Refund.CASH else _("the original payment")}})
    if order.voided_at:
        timeline.append({"at": order.voided_at, "title": _("Voided"), "icon": ICONS["void"], "tone": "x",
                         "sub": _("By %(who)s · nothing was charged") % {"who": order.voided_by.first_name if order.voided_by else "—"}})
    timeline.sort(key=lambda e: e["at"])

    pay_rows = []
    for p in payments:
        kind, text = pay_label([p])
        if p.method == OrderPayment.CASH:
            sub = _("Tendered %(t)s · change %(c)s") % {"t": f"${(p.tendered or p.amount):.2f}", "c": f"${p.change:.2f}"}
        elif p.method in (OrderPayment.GIFT, OrderPayment.STAFF):
            sub = date_format(timezone.localtime(p.timestamp), "g:i A")
        else:
            how = {"tap": _("Tapped"), "chip": _("Inserted"), "swipe": _("Swiped"), "keyed": _("Keyed in"), "online": _("Paid online")}.get(p.entry_method, _("Card"))
            sub = how + " " + date_format(timezone.localtime(p.timestamp), "g:i A")
        pay_rows.append({"label": p.card_label if p.method == OrderPayment.CARD else text, "sub": sub, "amount": p.amount, "icon": ICONS[kind], "kind": "pay"})
    for r in order.refunds.all():
        where = ", ".join({"cash": _("cash"), "gift": _("gift card"), "card": _("the card"), "staff": _("the staff card")}.get(part["method"], part["method"]) for part in r.parts) or "—"
        pay_rows.append({"label": _("Refund · %(reason)s") % {"reason": r.reason},
                         "sub": date_format(timezone.localtime(r.created_at), "g:i A") + " · " + _("to %(where)s") % {"where": where} + " · " + _("by %(who)s") % {"who": (r.by.first_name or r.by.username) if r.by else "—"},
                         "amount": -r.amount, "icon": ICONS["refund"], "kind": "refund"})

    customer = order.customer
    contact_email = order.email or (customer.email if customer else "")
    phone = f"{order.phone}" if order.phone else ""
    if len(phone) == 10:
        phone = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
    visits = customer.orders.count() if customer else 0
    newer, older, position, total = neighbours(order)
    can_refund = st not in ("refunded", "voided")
    return {
        "order": order, "status": st, "status_label": STATUS_LABELS[st],
        "meta": date_format(when, "l, F j") + " " + _("at") + " " + date_format(when, "g:i A")
                + (" · " + order.device.name if order.device else "") + (" · " + (order.cashier.first_name or order.cashier.username) if order.cashier else ""),
        "newer": newer, "older": older, "position": position, "count": total,
        "items": items, "item_count": count, "totals": totals, "timeline": timeline, "payments": pay_rows,
        "customer": customer, "name": order.name or (customer.get_full_name() if customer else "") or _("Walk-in"),
        "linked": bool(customer), "email": contact_email, "phone": phone,
        "customer_sub": (ngettext("%(n)d order", "%(n)d orders", visits) % {"n": visits} + " · " + _("customer since %(when)s") % {"when": date_format(customer.date_joined, "M Y")})
                        if customer else _("Not linked to a customer"),
        "details": [(_("Channel"), CHANNELS.get(order.channel, order.channel)), (_("Service"), _("To go") if order.to_go_order else _("For here")),
                    (_("Register"), order.device.name if order.device else "—"),
                    (_("Cashier"), (order.cashier.get_full_name() or order.cashier.username) if order.cashier else "—"),
                    (_("Stations"), ", ".join(s.name for s in stations) or "—"), (_("Prep time"), mmss(order.prep_time) if order.prep_time else "—")],
        "receipts": list(order.receipts.all()), "can_refund": can_refund,
        "refund_label": _("Voided") if st == "voided" else _("Refunded") if st == "refunded" else _("Refund"),
        "has_cash": any(p.method == OrderPayment.CASH for p in payments),
        "refunded": refunded, "left": order.total - refunded,
    }


def mmss(duration):
    if not duration:
        return "—"
    seconds = int(duration.total_seconds())
    return f"{seconds // 60}:{seconds % 60:02d}"


def refund_sheet(order, mode, picks, reason, to):
    """The refund sheet: items with what's left to refund, the reason and target chips, and the total so far."""
    done = refunds.refunded_counts(order)
    whole = mode != "items"
    rows = []
    for item in order.items.all():
        if item.parent_id:
            continue
        left = item.quantity - done.get(item.id, 0)
        pick = left if whole else max(0, min(left, picks.get(item.id, 0)))
        rows.append({"item": item, "left": left, "pick": pick, "done": item.quantity - left, "each": item.line_total / item.quantity if item.quantity else 0,
                     "amount": refunds.item_amount(order, item, pick) if pick else None})
    try:
        amount, _lines = refunds.quote(order, {r["item"].id: r["pick"] for r in rows}, whole)
        problem = ""
    except refunds.RefundError as e:
        amount, problem = Decimal("0"), str(e)
    only_cash = all(p.method == OrderPayment.CASH for p in order.payments.all())
    to = Refund.CASH if only_cash else (to if to in (Refund.ORIGINAL, Refund.CASH) else Refund.ORIGINAL)
    card = next((p for p in order.payments.all() if p.method == OrderPayment.CARD), None)
    return {
        "mode": "whole" if whole else "items", "rows": rows, "amount": amount, "problem": problem,
        "reasons": Refund.REASONS, "reason": reason if reason in Refund.REASONS else "",
        "to": to, "only_cash": only_cash,
        "original_label": card.card_label if card else (_("Gift card") if not only_cash else _("Original payment")),
        "breakdown": _("Everything left on this order") if whole else _("Items, with their share of discounts and tax"),
        "ready": amount > 0 and reason in Refund.REASONS,
    }


def receipt_data(order, store_name):
    """What the receipt email shows (misc_tools/emails.py: receipt), in the language that's on."""
    when = timezone.localtime(order.timestamp)
    items = [{"qty": i.quantity, "name": i.title, "total": f"${i.line_total:.2f}"} for i in order.items.all() if not i.parent_id]
    totals = [{"label": str(_("Subtotal")), "value": f"${order.subtotal:.2f}"}]
    if order.discount:
        totals.append({"label": str(_("Discount")), "value": f"−${order.discount:.2f}"})
    if order.tax:
        totals.append({"label": str(_("Tax")), "value": f"${order.tax:.2f}"})
    for fee in order.fee_lines.all():
        totals.append({"label": f"{fee.label} × {fee.quantity}" if fee.quantity > 1 else fee.label, "value": f"${fee.amount:.2f}"})
    if order.tip:
        totals.append({"label": str(_("Tip")), "value": f"${order.tip:.2f}"})
    for card in order.gift_cards_sold.all():
        totals.append({"label": _("Gift card •• %(last4)s") % {"last4": card.last4}, "value": f"${card.original:.2f}"})
    totals.append({"label": str(_("Total")), "value": f"${order.total:.2f}"})
    refunded = refunds.refunded_total(order)
    if refunded:
        totals.append({"label": str(_("Refunded")), "value": f"−${refunded:.2f}"})
    return {"store": store_name, "number": order.id, "when": date_format(when, "M j, Y g:i A"), "items": items, "totals": totals}
