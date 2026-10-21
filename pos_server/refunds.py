"""
Refunds on paid orders (the admin's Retail orders page; design: AdminOrders, refund sheet).

An item's refund is its share of what was paid: the line after its own discount, less its part of any whole-order
discount, plus its part of the tax. "Whole order" refunds everything still left, tips and rounding included.

Money goes back either to the original payment (card payments through Square, gift cards back onto the card) or
as cash from an open drawer (a CashMovement, so the drawer still balances). A Square refund happens last, inside
the transaction: if Square refuses, nothing is recorded.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from payments import square
from payments.models import PaymentAuthorization

from .models import CashDrawerSession, CashMovement, Order, OrderPayment, Refund, RefundItem

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class RefundError(Exception):
    pass


def cents(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def refunded_total(order) -> Decimal:
    return order.refunds.aggregate(total=Sum("amount"))["total"] or ZERO


def refunded_counts(order) -> dict:
    """{order item id: quantity already refunded}"""
    rows = RefundItem.objects.filter(refund__order=order).values("item").annotate(n=Sum("quantity"))
    return {row["item"]: row["n"] for row in rows}


def status(order) -> str:
    """'voided', 'refunded', 'partial' or 'paid'."""
    if order.voided_at:
        return "voided"
    refunded = refunded_total(order)
    if refunded and refunded >= order.total:
        return "refunded"
    return "partial" if refunded else "paid"


def item_amount(order, item, quantity) -> Decimal:
    """What `quantity` of a top-level `item` came to, after discounts and with tax."""
    items = [i for i in order.items.all() if not i.parent_id]
    line_discounts = sum((i.discount for i in items), ZERO)
    after_lines = order.subtotal - line_discounts
    order_discount = order.discount - line_discounts
    share = (after_lines - order_discount) / after_lines if after_lines else Decimal("1")
    taxable = order.subtotal - order.discount
    tax_rate = order.tax / taxable if taxable else ZERO
    each = item.line_total / item.quantity if item.quantity else ZERO
    return cents(each * quantity * share * (1 + tax_rate))


def quote(order, picks=None, whole=False):
    """
    (amount, [(item, quantity)]) for a refund. `picks` is {item id: quantity}; `whole` refunds everything left.
    Raises RefundError for anything that can't be refunded.
    """
    if order.voided_at:
        raise RefundError(_("This order was voided, so there's nothing to refund."))
    left_total = order.total - refunded_total(order)
    if left_total <= 0:
        raise RefundError(_("This order has already been refunded in full."))
    done = refunded_counts(order)
    items = {i.id: i for i in order.items.all() if not i.parent_id}
    if whole:
        lines = [(item, item.quantity - done.get(item.id, 0)) for item in items.values() if item.quantity > done.get(item.id, 0)]
        return left_total, lines
    lines = []
    for item_id, quantity in (picks or {}).items():
        item = items.get(int(item_id))
        quantity = int(quantity)
        if item is None or quantity < 0:
            raise RefundError(_("That item isn't on this order."))
        if quantity == 0:
            continue
        if quantity > item.quantity - done.get(item.id, 0):
            raise RefundError(_("Only %(n)d of %(item)s can still be refunded.") % {"n": item.quantity - done.get(item.id, 0), "item": item.title})
        lines.append((item, quantity))
    if not lines:
        raise RefundError(_("Pick something to refund."))
    return min(sum((item_amount(order, item, q) for item, q in lines), ZERO), left_total), lines


def _refunded_by_payment(order) -> dict:
    """{OrderPayment id: amount already given back to it}"""
    out = {}
    for refund in order.refunds.all():
        for part in refund.parts:
            if part.get("payment"):
                out[part["payment"]] = out.get(part["payment"], ZERO) + Decimal(part["amount"])
    return out


def cash_drawer_for(order):
    """The drawer cash refunds come out of: the order's register's, else any open one."""
    open_drawers = CashDrawerSession.objects.filter(closed_at__isnull=True)
    return (order.device_id and open_drawers.filter(device_id=order.device_id).first()) or open_drawers.first()


@transaction.atomic
def refund_order(order, *, picks=None, whole=False, reason, to, user) -> Refund:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not (reason or "").strip():
        raise RefundError(_("Pick a reason."))
    if to not in (Refund.ORIGINAL, Refund.CASH):
        raise RefundError(_("Pick where the money goes."))
    amount, lines = quote(order, picks, whole)
    refund = Refund.objects.create(order=order, amount=amount, reason=reason.strip()[:80], to=to, whole=whole, by=user)
    RefundItem.objects.bulk_create(RefundItem(refund=refund, item=item, quantity=q) for item, q in lines)

    parts, square_calls = [], []
    if to == Refund.CASH:
        drawer = cash_drawer_for(order)
        if drawer is None:
            raise RefundError(_("Open a cash drawer to refund in cash."))
        CashMovement.objects.create(drawer=drawer, amount=-amount, reason=_("Refund on order #%(n)s") % {"n": order.id}, user=user)
        parts.append({"method": "cash", "amount": str(amount), "drawer": drawer.id})
    else:
        given = _refunded_by_payment(order)
        left = amount
        # Back to cards first, then gift cards. Cash can't go back "to the original payment".
        payments = sorted(order.payments.exclude(method=OrderPayment.CASH), key=lambda p: p.method != OrderPayment.CARD)
        for payment in payments:
            room = payment.amount - given.get(payment.id, ZERO)
            part = min(room, left)
            if part <= 0:
                continue
            if payment.method == OrderPayment.GIFT:
                payment.gift_card.load_card(part)
                parts.append({"method": "gift", "amount": str(part), "payment": payment.id, "ref": payment.gift_card.number[-4:]})
            else:
                payment_id = payment.square_payment_id or (order.authorization.payment_id if order.authorization else "")
                if not payment_id:
                    raise RefundError(_("Square has no record of this card payment, so refund it in cash."))
                if order.authorization and order.authorization.status == PaymentAuthorization.AUTHORIZED:
                    raise RefundError(_("The card hasn't been charged yet. Reject the order instead."))
                parts.append({"method": "card", "amount": str(part), "payment": payment.id, "label": payment.card_label})
                square_calls.append((len(parts) - 1, payment_id, part, payment.id))
            left -= part
            if left <= 0:
                break
        if left > 0:
            raise RefundError(_("%(amount)s of this was paid in cash. Refund it in cash instead.") % {"amount": f"${left:.2f}"})
    for index, payment_id, part, payment_pk in square_calls:  # last, so a refusal rolls everything back
        try:
            parts[index]["ref"] = square.refund_payment(payment_id, part, f"refund-{refund.id}-{payment_pk}", reason)
        except square.SquareError as e:
            raise RefundError(_("Square refused the refund: %(error)s") % {"error": e}) from e
    refund.parts = parts
    refund.save(update_fields=["parts"])
    return refund
