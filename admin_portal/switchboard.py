"""
Switchboard (Settings; no mockup, laid out like the Payments page): the Switchboards connected to this Pavilion
(switchboard.SwitchboardConnection), each with a Disconnect button, and the recent event deliveries
(switchboard.Delivery). Connecting starts in Switchboard: an admin types this Pavilion's address there and approves
on the consent page (switchboard/views.py: authorize), so this page has no Connect button, only the address to type.
"""
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext as _, pgettext

from .devices import _when

ASK_KEY = "admin.switchboard.disconnect"  # session: the connection whose "Disconnect?" question is open
ICON = "M5 4h5v5H5z M14 15h5v5h-5z M7.5 9v4a4 4 0 0 0 4 4H14"


def allows() -> list:
    """What a connection lets Switchboard do, for this page and the consent page."""
    return [_("Start workflows from what happens here: orders, deliveries, catering, gift cards, stock, the cash drawer, staff and the schedule"),
            _("Look up staff, customers, orders, dishes, stock, gift cards, coupons and a day’s sales"),
            _("Add staff accounts and make their sign-in links, issue staff cards, add and cancel shifts, and ask for time off"),
            _("Issue gift cards by email, add money to them or freeze them, and create coupon codes"),
            _("Turn dishes and stations off or on, change stock counts, pause online orders and add closures"),
            _("Announce on the screens, and email receipts, invitations and catering messages")]


def _status(d) -> tuple[str, str]:
    """(what the log says about a delivery, its tone: ok, warn or bad)."""
    from switchboard.events import ATTEMPTS
    from switchboard.models import Delivery
    if d.status == Delivery.DELIVERED:
        return pgettext("an event", "Delivered"), "ok"
    if d.status == Delivery.PENDING:
        if d.attempts == 0:
            return _("Sending"), "warn"
        return _("Trying again (%(n)s of %(of)s)") % {"n": d.attempts, "of": ATTEMPTS}, "warn"
    if d.status == Delivery.GONE:
        return _("Not sent: connection gone"), "bad"
    return pgettext("an event", "Failed"), "bad"


def _why(d) -> str:
    if d.response_status and not 200 <= d.response_status < 300:
        return _("Switchboard answered %(status)s") % {"status": d.response_status}
    if d.error:
        return _("Couldn’t reach Switchboard")
    return ""


def page(request) -> dict:
    from switchboard.models import Delivery, SwitchboardConnection
    now = timezone.now()
    connections = list(SwitchboardConnection.objects.select_related("connected_by"))
    week = Delivery.objects.filter(created_at__gte=now - timedelta(days=7))
    failed = {}
    for connection_id in week.filter(status=Delivery.FAILED).values_list("connection_id", flat=True):
        failed[connection_id] = failed.get(connection_id, 0) + 1
    rows = []
    for c in connections:
        connected = _when(c.connected_at)
        if c.connected_by:
            connected = _("%(when)s by %(who)s") % {"when": connected, "who": c.connected_by.get_full_name() or c.connected_by.username}
        rows.append({"c": c, "connected": connected, "last": _when(c.last_delivery_at), "used": _when(c.last_used_at),
                     "broken_since": _when(c.broken_at), "failed": failed.get(c.pk, 0)})
    log = []
    for d in Delivery.objects.select_related("connection")[:40]:
        text, tone = _status(d)
        log.append({"d": d, "when": _when(d.created_at), "status": text, "tone": tone, "why": _why(d)})
    ask = request.session.get(ASK_KEY)
    return {"address": request.build_absolute_uri("/").rstrip("/"), "rows": rows, "log": log, "allows": allows(), "icon": ICON,
            "live": sum(1 for c in connections if not c.broken), "broken": sum(1 for c in connections if c.broken),
            "ask": next((c for c in connections if c.pk == ask), None)}
