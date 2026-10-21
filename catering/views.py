"""
The customer's catering order page (/catering/<token>): the link in every catering email. It shows the order and
the conversation, and lets them reply, and accept or decline changes the store proposed (accepting charges the
card on file). The token is the key, like a gift card's number: no account is needed.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from admin_portal.models import StoreSettings

from . import flow, payments, pricing
from .models import CateringMessage, CateringOrder, CateringSettings


def order_page(request, token):
    order = get_object_or_404(CateringOrder, token=token)
    if request.method == "POST":
        op = request.POST.get("op", "")
        try:
            if op == "reply":
                flow.write(order, CateringMessage.CUSTOMER, request.POST.get("text", ""), request=request)
                messages.success(request, _("Sent. We’ll get back to you soon."))
            elif op == "agree":
                flow.agree(order, request=request)
                messages.success(request, _("You’re all set. Your card was charged and the order is confirmed."))
            elif op == "decline":
                flow.decline(order, request.POST.get("text", ""), request=request)
                messages.success(request, _("Order cancelled. Nothing was charged."))
        except (flow.CateringError, payments.PaymentFailed) as exc:
            messages.error(request, str(exc))
        return redirect("catering_order", token=order.token)
    return render(request, "catering/order.html", {"o": order, "v": view(order), "store": StoreSettings.load()})


def view(order) -> dict:
    current = pricing.lines_of(order)
    before = order.previous if order.status == CateringOrder.WAITING and order.previous else None
    catalog = pricing.Catalog(current, before)
    old = {line["id"]: line for line in before or []}
    rows = []
    for line in current:
        f = pricing.figure(line, catalog)
        was = old.get(line["id"]) if before is not None else None
        tag, kind = "", ""
        if before is not None:
            if not was:
                tag, kind = _("New"), "add"
            elif pricing.figure(was, catalog)["total"] != f["total"]:
                tag, kind = _("Changed"), "ch"
        rows.append({"name": f["name"], "qty": f["qty_text"], "unit": f["unit"].split(" · ")[0], "total": f["total"], "tag": tag, "kind": kind})
    for line in before or []:
        if line["id"] not in {l["id"] for l in current}:
            f = pricing.figure(line, catalog)
            rows.append({"name": f["name"], "qty": f["qty_text"], "unit": "", "total": f["total"], "tag": _("Removed"), "kind": "rm", "removed": True})
    totals = pricing.totals(current, catalog)
    was_total = pricing.totals(before, catalog)["total"] if before is not None else None
    labels = {CateringMessage.PLACED: _("You placed the order"), CateringMessage.CHANGES: _("We suggested changes"),
              CateringMessage.ACCEPTED: _("We confirmed your order"), CateringMessage.REJECTED: _("We couldn’t take this order"),
              CateringMessage.CANCELLED: _("The order was cancelled"), CateringMessage.AGREED: _("You accepted the changes"),
              CateringMessage.DECLINED: _("You declined the changes")}
    thread = []
    for m in order.messages.all():
        if m.who == CateringMessage.SYSTEM and m.text.startswith(_("Auto-reply sent")):
            continue
        head = labels.get(m.kind) or (_("You") if m.who == CateringMessage.CUSTOMER else StoreSettings.load().name if m.who == CateringMessage.STAFF else "")
        text = order.note if m.kind == CateringMessage.PLACED else m.text
        thread.append({"head": head, "at": m.at, "text": text, "who": m.who, "changes": m.changes})
    return {"rows": rows, "totals": totals, "was_total": was_total, "thread": thread,
            "waiting": order.status == CateringOrder.WAITING, "open": order.status in CateringOrder.OPEN,
            "policy": CateringSettings.load().policy, "rate": pricing.tax_rate() * 100}
