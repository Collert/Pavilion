"""
The register's order history (design: History): past orders by day and status with search, one order's receipt,
and from there print, email or refund it. The same data and refund rules as the admin's Orders page
(admin_portal/orders.py, pos_server/refunds.py), on the register's own screen. Refunding needs `act.refund`.

GET ?date=today|yesterday|week&status=all|paid|refunds|voided&q=&page=&sel=<order id>
    &sheet=send|refund (+ the refund sheet's mode, q_<item id>, reason, to)
POST op=refund|send on the same address; print posts to register_history_print (a new tab with the receipt).
"""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy

from admin_portal import orders as orders_data
from admin_portal.models import StoreSettings
from users.access import can

from .devices import device_required
from .models import Order, ReceiptLog, Refund
from .refunds import RefundError, refund_order
from .register import register_staff_required

PER_PAGE = 8
DATES = [("today", gettext_lazy("Today")), ("yesterday", gettext_lazy("Yesterday")), ("week", gettext_lazy("This week"))]
KEEP = ("date", "status", "q", "page", "sel")  # what the address keeps between actions


def _state(params) -> dict:
    return {k: params.get(k, "") for k in KEEP if params.get(k, "")}


def _url(state, **more) -> str:
    query = urlencode({**state, **{k: v for k, v in more.items() if v not in (None, "")}})
    return reverse("register_history") + (f"?{query}" if query else "")


@register_staff_required
@device_required
def history(request):
    if request.method == "POST":
        return _post(request)
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    get = request.GET
    try:
        page = int(get.get("page", 1))
    except ValueError:
        page = 1
    date = get.get("date") if get.get("date") in dict(DATES) else "today"
    data = orders_data.order_list(date, get.get("status", "all"), get.get("q", ""), page, per_page=PER_PAGE)
    state = _state(get)
    rows = data["rows"]
    sel = None
    if get.get("sel", "").isdigit():
        sel = Order.objects.filter(pk=int(get["sel"])).first()
    elif rows:
        sel = rows[0]["order"]
    d = orders_data.order_page(sel) if sel else None
    if d:
        at = timezone.localtime(sel.timestamp)
        d["over"] = f"{orders_data.day_label(at)} · {date_format(at, 'g:i A')}"
        d["svc"] = _("To go") if sel.to_go_order else _("For here")
    can_refund = can(request.user, "act.refund")
    sheet, refund, send = get.get("sheet", ""), None, None
    if d and sheet == "refund" and d["can_refund"] and can_refund:
        picks = {int(k[2:]): int(v) for k, v in get.items() if k.startswith("q_") and k[2:].isdigit() and v.lstrip("-").isdigit()}
        refund = orders_data.refund_sheet(sel, get.get("mode", "whole"), picks, get.get("reason", ""), get.get("to", ""))
    elif d and sheet == "send":
        send = {"to": get.get("to", d["email"])}
    else:
        sheet = ""
    for r in rows:
        r["on"] = sel is not None and r["order"].pk == sel.pk
        r["href"] = _url({**state, "sel": r["order"].pk})
    when = {"today": _("today"), "yesterday": _("yesterday"), "week": _("this week")}[date]
    return render(request, "pos_server/history.html", {
        "nav": "history", "o": data, "dates": DATES, "date": date, "state": state, "d": d, "sel": sel,
        "head_sub": _("%(n)s · %(net)s net %(when)s") % {
            "n": translation.ngettext("%(n)d order", "%(n)d orders", data["total"]) % {"n": data["total"]},
            "net": f"${data['net']:,.2f}", "when": when},
        "sheet": sheet, "refund": refund, "send": send, "can_refund": can_refund,
        "links": {
            "dates": [(k, label, _url({**{k2: v for k2, v in state.items() if k2 not in ("page", "sel", "date")}}, date=k)) for k, label in DATES],
            "statuses": [(k, label, n, _url({**{k2: v for k2, v in state.items() if k2 not in ("page", "sel", "status")}}, status=k))
                         for k, label, n in data["statuses"]],
            "pages": [(n, _url({**state, "page": n}) if n != "…" else "") for n in data["nums"]],
            "prev": _url({**state, "page": data["page"] - 1}) if data["page"] > 1 else "",
            "next": _url({**state, "page": data["page"] + 1}) if data["page"] < data["pages"] else "",
            "close": _url(state),
            "send": _url({**state, "sheet": "send"}),
            "refund": _url({**state, "sheet": "refund"}),
        },
    })


def _post(request):
    post = request.POST
    state = _state(post)
    order = get_object_or_404(Order, pk=post.get("sel") or 0)
    op = post.get("op")
    if op == "refund":
        if not can(request.user, "act.refund"):
            messages.error(request, _("Refunds need someone whose group allows them."))
            return redirect(_url(state))
        picks = {int(k[2:]): v for k, v in post.items() if k.startswith("q_") and k[2:].isdigit()}
        try:
            refund = refund_order(order, picks=picks, whole=post.get("mode", "whole") != "items", reason=post.get("reason", ""),
                                  to=post.get("to", Refund.ORIGINAL), user=request.user)
        except RefundError as e:
            messages.error(request, str(e))
            keep = {k: v for k, v in post.items() if k.startswith("q_") or k in ("mode", "reason", "to")}
            return redirect(_url(state, sheet="refund", **keep))
        where = _("in cash") if refund.to == Refund.CASH else _("to the card")
        messages.success(request, _("%(amount)s refunded %(where)s.") % {"amount": f"${refund.amount:.2f}", "where": where})
        return redirect(_url(state))
    if op == "send":
        to = post.get("to", "").strip()
        try:
            validate_email(to)
        except ValidationError:
            messages.error(request, _("Enter an email address."))
            return redirect(_url(state, sheet="send", to=to))
        from misc_tools.email import EmailError, language_for, send as send_email
        try:
            lang = language_for(to, user=order.customer, fallback=settings.LANGUAGE_CODE)
            with translation.override(lang):
                data = orders_data.receipt_data(order, StoreSettings.load().name)
            sent = send_email("receipt", to, data, language=lang)
        except EmailError as e:
            messages.error(request, _("The receipt wasn't sent: %(error)s") % {"error": e})
            return redirect(_url(state, sheet="send", to=to))
        if not sent:
            messages.error(request, _("Email isn’t set up yet, so nothing was sent."))
            return redirect(_url(state, sheet="send", to=to))
        ReceiptLog.objects.create(order=order, channel=ReceiptLog.EMAIL, to=to, by=request.user)
        if not order.email:
            order.email = to
            order.save(update_fields=["email"])
        messages.success(request, _("Receipt sent to %(to)s.") % {"to": to})
        return redirect(_url(state))
    return redirect(_url(state))


@register_staff_required
@device_required
def history_print(request, order_id):
    """Log the print and show the printable receipt (it opens the print dialog)."""
    order = get_object_or_404(Order, pk=order_id)
    if request.method == "POST":
        ReceiptLog.objects.create(order=order, channel=ReceiptLog.PRINT, by=request.user)
    return render(request, "admin_portal/receipt.html", {"d": orders_data.order_page(order), "store": StoreSettings.load()})
