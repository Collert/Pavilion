"""
The money side of a catering order, on the customer's card on file (payments/square.py): charge it in full when the
order is accepted, refund it when a confirmed order is cancelled, and hold equipment deposits close to the event
(placed by `manage.py catering_deposits`), then let the hold go when the equipment comes back or keep it.

Cards whose id starts with DEMO are seed_demo's made-up cards (seed_demo only runs with DEBUG on): they're
"charged" without calling Square so the demo works offline.
"""
import logging
from decimal import Decimal

from django.utils.translation import gettext as _

from payments import square

from .models import CateringOrder

logger = logging.getLogger(__name__)
DEMO = "demo:"


class PaymentFailed(Exception):
    """The card couldn't be charged, refunded or held; the message is for staff."""


def _demo(order) -> bool:
    return order.square_card_id.startswith(DEMO)


def _cents(amount) -> int:
    return square.to_cents(amount)


def _square(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except square.SquareError as exc:
        logger.warning("Square refused a catering payment: %s", exc)
        detail = exc.args[0] if exc.args else ""
        if isinstance(detail, list) and detail:
            detail = detail[0].get("detail") or detail[0].get("code") or ""
        raise PaymentFailed(_("The card was declined or Square couldn’t be reached. %(detail)s") % {"detail": detail}) from exc
    except Exception as exc:  # network trouble, a missing setting
        logger.exception("Catering payment failed")
        raise PaymentFailed(_("Square couldn’t be reached. Try again in a minute.")) from exc


def charge(order, amount: Decimal):
    """Charge `amount` in full. Saves nothing; the caller saves the order."""
    if amount <= 0:
        return
    if not order.square_card_id:
        raise PaymentFailed(_("There’s no card on file for this order."))
    if _demo(order):
        payment_id = f"{DEMO}pay-{order.id}-{_cents(amount)}"
    else:
        payment_id = _square(square.charge_card, order.square_customer_id, order.square_card_id, amount,
                             key=f"cat-{order.id}-chg-{_cents(amount)}", note=f"Catering {order.number}")
    order.payment_id, order.charged = payment_id, amount


def refund(order, reason=""):
    """Give back what was charged and not refunded yet."""
    amount = order.charged - order.refunded
    if amount <= 0 or not order.payment_id:
        return Decimal("0")
    if not _demo(order):
        _square(square.refund_payment, order.payment_id, amount, f"cat-{order.id}-ref-{_cents(amount)}", reason)
    order.refunded += amount
    return amount


def place_hold(order, amount: Decimal):
    """Hold the equipment deposit on the card (not charged)."""
    if amount <= 0 or order.hold_state:
        return
    if not order.square_card_id:
        raise PaymentFailed(_("There’s no card on file for this order."))
    if _demo(order):
        payment_id = f"{DEMO}hold-{order.id}"
    else:
        payment_id = _square(square.charge_card, order.square_customer_id, order.square_card_id, amount,
                             key=f"cat-{order.id}-hold-{_cents(amount)}", note=f"Deposit, catering {order.number}",
                             autocomplete=False)
    order.hold_payment_id, order.hold_amount, order.hold_state = payment_id, amount, CateringOrder.HELD


def release_hold(order):
    """Let the deposit hold go (the equipment came back, or the order was cancelled)."""
    if order.hold_state != CateringOrder.HELD:
        return
    if not _demo(order):
        _square(square.cancel_payment, order.hold_payment_id)
    order.hold_state = CateringOrder.RELEASED


def keep_hold(order):
    """Take the deposit (something didn't come back or came back broken)."""
    if order.hold_state != CateringOrder.HELD:
        return
    if not _demo(order):
        _square(square.complete_payment, order.hold_payment_id)
    order.hold_state = CateringOrder.KEPT
