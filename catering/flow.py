"""
Everything that happens to a catering order: it comes in (place), staff accept it (charge the card), reject or
cancel it (refund), propose changes the customer then agrees to or declines, write to each other, and the
deposit hold is placed, let go or kept. Each step adds a CateringMessage, emails the customer (misc_tools.email;
logged when listmonk isn't set up) and, for services, keeps services.Booking rows in step. Raise CateringError for
anything staff need to see; payment problems come up as payments.PaymentFailed and change nothing. `quiet` skips
the email (seed_demo).
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from admin_portal.models import StoreSettings
from misc_tools.email import EmailError, send as send_email
from services.models import Booking

from . import payments, pricing
from .models import CateringLine, CateringMessage, CateringOrder, CateringSettings, CateringVersion

logger = logging.getLogger(__name__)
REASONS = [_("Fully booked that day"), _("Not enough notice"), _("Too large for us"), _("Outside our delivery area"),
           _("Can’t make some items"), _("Other")]


class CateringError(Exception):
    pass


def order_link(order, request=None) -> str:
    path = reverse("catering_order", args=[order.token])
    return request.build_absolute_uri(path) if request else path


def _email(kind, order, request=None, **data):
    """Email the customer; a failure is logged, never stops the step."""
    total = pricing.order_totals(order)["total"]
    payload = {"name": order.first_name, "number": order.number, "title": order.title, "store": StoreSettings.load().name,
               "when": f"{order.event_date:%a %b} {order.event_date.day}, {order.event_time:%I:%M %p}".replace(" 0", " "),
               "total": pricing.money(total), "order_url": order_link(order, request), **data}
    try:
        send_email(kind, order.email, payload)
    except EmailError:
        logger.exception("Catering email %s for %s wasn't sent", kind, order.number)


def snapshot(order, by):
    """Keep the order's lines as its next version."""
    lines = pricing.lines_of(order)
    number = (order.versions.order_by("-number").values_list("number", flat=True).first() or 0) + 1
    total = pricing.totals(lines, pricing.Catalog(lines))["total"]
    return CateringVersion.objects.create(order=order, number=number, by=by, lines=lines, total=total)


def _say(order, who, kind=CateringMessage.MESSAGE, user=None, **fields):
    return CateringMessage.objects.create(order=order, who=who, kind=kind, user=user, **fields)


def place(order, request=None, quiet=False):
    """A new order came in (its lines are saved): note it, and send the auto-reply."""
    cfg = CateringSettings.load()
    totals = pricing.order_totals(order)
    if not order.versions.exists():
        snapshot(order, CateringVersion.CUSTOMER)
        CateringVersion.objects.filter(order=order).update(at=order.placed_at)
    if order.days_ahead < cfg.notice_days(totals["minutes"]):
        order.rush = True
        order.save(update_fields=["rush"])
    _say(order, CateringMessage.CUSTOMER, CateringMessage.PLACED, at=order.placed_at)
    if order.rush:
        _say(order, CateringMessage.SYSTEM, at=order.placed_at,
             text=_("Sent as a request: %(n)s days’ notice is under your %(m)s-day minimum.") % {
                 "n": order.days_ahead, "m": pricing_days(cfg.notice_days(totals["minutes"]))})
    if cfg.auto_reply:
        _say(order, CateringMessage.SYSTEM, at=order.placed_at, text=_("Auto-reply sent: “%(text)s”") % {"text": cfg.auto_reply})
    if not quiet:
        _email("catering_received", order, request, message=cfg.auto_reply)


def write_lines(order, draft_lines):
    """Make the order's lines match `draft_lines` (keeping the rows that stay, so ids carry over)."""
    keep = []
    for position, line in enumerate(l for l in draft_lines if not l.get("removed")):
        row = order.lines.filter(pk=line["id"]).first() if line.get("id") else None
        row = row or CateringLine(order=order, kind=line["kind"])
        setattr(row, CateringLine.FIELDS[line["kind"]] + "_id", int(line["ref"]))
        row.quantity, row.amount = max(int(line["quantity"]), 1), int(line.get("amount") or 0)
        row.price, row.position = Decimal(str(line["price"])), position
        row.save()
        keep.append(row.id)
    order.lines.exclude(pk__in=keep).delete()


def pricing_days(days: float) -> str:
    return f"{days:g}"


def _sync_bookings(order, status=None):
    """Keep one confirmed services.Booking per service line while the order is on; cancel them when it's off."""
    for line in order.lines.filter(kind=CateringLine.SERVICE).select_related("service", "booking"):
        if status:
            if line.booking_id:
                line.booking.status = status
                line.booking.save(update_fields=["status"])
            continue
        f = pricing.figure({"kind": line.kind, "ref": line.ref, "quantity": line.quantity, "amount": line.amount,
                            "price": line.price}, pricing.Catalog([{"kind": line.kind, "ref": line.ref}]))
        timed = line.service.pricing in (line.service.HOUR, line.service.DAY)
        fields = {"service": line.service, "name": order.organization or order.name, "customer": order.customer,
                  "date": order.event_date, "amount": max(line.amount, 1) if timed else line.quantity,
                  "count": line.quantity, "status": Booking.CONFIRMED, "channel": Booking.CATERING,
                  "total": f["total"] + f["tax"], "deposit": f["deposit"], "notes": order.number}
        if line.booking_id:
            Booking.objects.filter(pk=line.booking_id).update(**fields)
        else:
            line.booking = Booking.objects.create(**fields)
            line.save(update_fields=["booking"])


def hold_due(order, cfg=None) -> bool:
    cfg = cfg or CateringSettings.load()
    return order.hold_on(cfg.hold_days) <= timezone.localdate()


def _confirm(order, request=None):
    """Charge the card and confirm: shared by staff accepting and the customer agreeing to changes."""
    cfg = CateringSettings.load()
    totals = pricing.order_totals(order)
    payments.charge(order, totals["total"])
    order.status, order.accepted_at, order.previous = CateringOrder.CONFIRMED, timezone.now(), None
    note = _("Charged %(total)s to %(card)s.") % {"total": pricing.money(totals["total"]), "card": order.card}
    if totals["deposit"]:
        if hold_due(order, cfg):
            try:
                payments.place_hold(order, totals["deposit"])
                note += " " + _("%(dep)s deposit held on the card.") % {"dep": pricing.money(totals["deposit"])}
            except payments.PaymentFailed:
                note += " " + _("The %(dep)s deposit hold didn’t go through; it’s tried again daily.") % {"dep": pricing.money(totals["deposit"])}
        else:
            day = order.hold_on(cfg.hold_days)
            note += " " + _("%(dep)s deposit hold scheduled for %(day)s.") % {"dep": pricing.money(totals["deposit"]), "day": f"{day:%b} {day.day}"}
    order.save()
    _sync_bookings(order)
    _say(order, CateringMessage.SYSTEM, text=note)


@transaction.atomic
def accept(order, user, message="", request=None, quiet=False):
    if order.status != CateringOrder.NEW:
        raise CateringError(_("Only new orders can be accepted."))
    _say(order, CateringMessage.STAFF, CateringMessage.ACCEPTED, user=user, text=message.strip())
    _confirm(order, request)
    if not quiet:
        _email("catering_accepted", order, request, message=message.strip())


@transaction.atomic
def reject(order, user, reason, message, request=None, quiet=False):
    """Reject a new order, or cancel one waiting on the customer or confirmed (refunding what was charged)."""
    if order.status not in CateringOrder.OPEN:
        raise CateringError(_("This order is already closed."))
    if not reason or not message.strip():
        raise CateringError(_("Pick a reason and write to %(name)s.") % {"name": order.first_name})
    refunded = payments.refund(order, reason)
    payments.release_hold(order)
    kind = CateringMessage.REJECTED if order.status == CateringOrder.NEW else CateringMessage.CANCELLED
    order.status = CateringOrder.REJECTED if kind == CateringMessage.REJECTED else CateringOrder.CANCELLED
    order.reason, order.closed_at, order.previous = reason[:60], timezone.now(), None
    order.save()
    _sync_bookings(order, Booking.DECLINED if kind == CateringMessage.REJECTED else Booking.CANCELLED)
    _say(order, CateringMessage.STAFF, kind, user=user, reason=reason[:60], text=message.strip())
    if refunded:
        _say(order, CateringMessage.SYSTEM, text=_("Refunded %(amount)s to %(card)s.") % {"amount": pricing.money(refunded), "card": order.card})
    if quiet:
        return
    _email("catering_closed", order, request, message=message.strip(), reason=reason,
           refunded=pricing.money(refunded) if refunded else "")


@transaction.atomic
def propose(order, user, draft_lines, message, request=None, quiet=False):
    """Send changed lines to the customer to agree to; nothing is charged until they do."""
    if order.status not in (CateringOrder.NEW, CateringOrder.WAITING):
        raise CateringError(_("Only orders that aren’t confirmed yet can be changed."))
    before = pricing.lines_of(order)
    if not order.versions.exists():
        snapshot(order, CateringVersion.CUSTOMER)
    catalog = pricing.Catalog(before, draft_lines)
    diff = pricing.changes(before, draft_lines, catalog)
    if not diff:
        raise CateringError(_("Nothing has changed yet."))
    if not message.strip():
        raise CateringError(_("Add a reason for the changes."))
    was, now = pricing.totals(before, catalog)["total"], pricing.totals(draft_lines, catalog)["total"]
    write_lines(order, draft_lines)
    order.previous = before
    order.status, order.proposed_at = CateringOrder.WAITING, timezone.now()
    order.save()
    snapshot(order, CateringVersion.STAFF)
    _say(order, CateringMessage.STAFF, CateringMessage.CHANGES, user=user, text=message.strip(), changes=diff,
         was_total=was, now_total=now)
    if quiet:
        return
    _email("catering_changes", order, request, message=message.strip(), was=pricing.money(was), now=pricing.money(now),
           changes=[c[0] for c in diff])


@transaction.atomic
def agree(order, request=None):
    """The customer accepts the proposed changes: charge and confirm."""
    if order.status != CateringOrder.WAITING:
        raise CateringError(_("There are no changes waiting for you."))
    _say(order, CateringMessage.CUSTOMER, CateringMessage.AGREED)
    _confirm(order, request)
    _email("catering_accepted", order, request, message="")


@transaction.atomic
def counter(order, draft_lines, message="", request=None):
    """The customer suggests their own version (to the store's proposal or their own request): it goes back to the
    store as a new request to answer, compared with the version before."""
    if order.status not in (CateringOrder.NEW, CateringOrder.WAITING):
        raise CateringError(_("This request can’t be changed any more."))
    before = pricing.lines_of(order)
    catalog = pricing.Catalog(before, draft_lines)
    diff = pricing.changes(before, draft_lines, catalog)
    if not diff:
        raise CateringError(_("Make a change first."))
    if not order.versions.exists():
        snapshot(order, CateringVersion.CUSTOMER)
    was, now = pricing.totals(before, catalog)["total"], pricing.totals(draft_lines, catalog)["total"]
    write_lines(order, draft_lines)
    order.previous, order.status = before, CateringOrder.NEW
    order.save()
    version = snapshot(order, CateringVersion.CUSTOMER)
    _say(order, CateringMessage.CUSTOMER, CateringMessage.COUNTER, text=message.strip()[:600], changes=diff,
         was_total=was, now_total=now, reason=str(version.number))


@transaction.atomic
def customer_cancel(order, request=None):
    """The customer calls off a request that isn't confirmed yet. Nothing was charged."""
    if order.status not in (CateringOrder.NEW, CateringOrder.WAITING):
        raise CateringError(_("Confirmed orders are cancelled by the store. Send them a message."))
    order.status, order.closed_at, order.reason, order.previous = CateringOrder.CANCELLED, timezone.now(), _("Cancelled by the customer"), None
    order.save()
    _say(order, CateringMessage.CUSTOMER, CateringMessage.CANCELLED, reason=_("Cancelled by the customer"))


@transaction.atomic
def decline(order, message="", request=None):
    if order.status != CateringOrder.WAITING:
        raise CateringError(_("There are no changes waiting for you."))
    order.status, order.closed_at, order.reason = CateringOrder.CANCELLED, timezone.now(), _("Declined the changes")
    order.save()
    _say(order, CateringMessage.CUSTOMER, CateringMessage.DECLINED, text=message.strip())


def write(order, who, text, user=None, request=None):
    """A message from staff (emailed to the customer) or from the customer."""
    if not text.strip():
        raise CateringError(_("Write something first."))
    if order.status not in CateringOrder.OPEN:
        raise CateringError(_("This order is closed."))
    _say(order, who, user=user, text=text.strip()[:600])
    if who == CateringMessage.STAFF:
        _email("catering_message", order, request, message=text.strip())


def remind(order, request=None):
    if order.status != CateringOrder.WAITING:
        raise CateringError(_("This order isn’t waiting on the customer."))
    _say(order, CateringMessage.SYSTEM, text=_("Reminder sent to %(name)s to review the changes.") % {"name": order.first_name})
    _email("catering_reminder", order, request)


@transaction.atomic
def finish(order, user, keep_deposit=False):
    """After the event: the equipment is back (let the hold go) or isn't (keep it). Either way the order is done."""
    if order.status != CateringOrder.CONFIRMED:
        raise CateringError(_("Only confirmed orders can be completed."))
    held = order.hold_amount
    if keep_deposit:
        payments.keep_hold(order)
        text = _("Deposit of %(amount)s kept.") % {"amount": pricing.money(held)}
    else:
        payments.release_hold(order)
        text = _("Equipment returned. Deposit hold of %(amount)s released.") % {"amount": pricing.money(held)} if held else _("Order completed.")
    order.status, order.closed_at = CateringOrder.DONE, timezone.now()
    order.save()
    _say(order, CateringMessage.SYSTEM, user=user, text=text)


def daily(today=None) -> dict:
    """
    The daily job (`manage.py catering_deposits`): hold deposits that are due, and complete confirmed orders whose
    event is over and that have no hold to settle. Returns {"held": n, "completed": n, "failed": n}.
    """
    cfg = CateringSettings.load()
    today = today or timezone.localdate()
    out = {"held": 0, "completed": 0, "failed": 0}
    for order in CateringOrder.objects.filter(status=CateringOrder.CONFIRMED, hold_state=""):
        deposit = pricing.order_totals(order)["deposit"]
        if deposit and order.hold_on(cfg.hold_days) <= today and order.event_date >= today:
            try:
                with transaction.atomic():
                    payments.place_hold(order, deposit)
                    order.save()
                    _say(order, CateringMessage.SYSTEM, text=_("%(dep)s deposit held on %(card)s.") % {"dep": pricing.money(deposit), "card": order.card})
                out["held"] += 1
            except payments.PaymentFailed:
                logger.warning("Couldn't hold the deposit for %s", order.number)
                out["failed"] += 1
        elif order.event_date < today and not deposit:
            order.status, order.closed_at = CateringOrder.DONE, timezone.now()
            order.save(update_fields=["status", "closed_at"])
            _say(order, CateringMessage.SYSTEM, text=_("Order completed."))
            out["completed"] += 1
    return out
