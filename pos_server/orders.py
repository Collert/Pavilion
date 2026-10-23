"""
Order creation and serialization, shared by the POS (pos_server.views.pos) and the online store
(online_store.views.place_order).

Carts are priced in pos_server.pricing (the browser's prices and totals are never trusted). This module turns
a priced cart into an Order with its items, discounts and payments, takes the stock, and charges gift cards.
"""
from collections import Counter
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext as _

from gift_cards.models import GiftCard, GiftCardAuthorization
from inventory.views import craft_component
from .models import (Component, Discount, DisposableUse, Ingredient, Order, OrderDiscount, OrderFee, OrderItem, OrderPayment,
                     OrderStation, Station)
from .pricing import OrderError, price_cart, to_money  # noqa: F401 (OrderError and to_money are re-exported)

ZERO = Decimal("0.00")


def consume_stock(use: Counter):
    """Take {component id: amount} out of inventory, crafting self-crafting components first."""
    for component in Component.objects.filter(pk__in=use):
        needed = use[component.id]
        if component.crafting_option == "auto":
            craft_component(component.id, needed)
            component.refresh_from_db()
        component.inventory = (component.inventory or 0) - needed
        component.save()


def use_disposables(order, cart):
    """Count the cart's disposables off stock, record them for the numbers, and add its fee lines."""
    for disposable, count in cart.disposables:
        ingredient = disposable.ingredient
        DisposableUse.objects.create(order=order, disposable=disposable, quantity=count, cost=ingredient.cost or 0)
        if not ingredient.unlimited:
            Ingredient.objects.filter(pk=ingredient.pk).update(inventory=F("inventory") - count)
    for fee in cart.fees:
        OrderFee.objects.create(order=order, disposable=fee.disposable, label=fee.label, quantity=fee.quantity, amount=fee.amount)


def _count_discount_uses(cart):
    """Count each coupon/offer use, refusing ones that ran out while this cart was being rung up."""
    for _line, applied in cart.applied_discounts():
        if applied.discount is None:
            continue
        discount = Discount.objects.select_for_update().get(pk=applied.discount.pk)
        if discount.max_uses is not None and discount.times_used >= discount.max_uses:
            raise OrderError(_("%(name)s has been used up.") % {"name": discount.name})
        Discount.objects.filter(pk=discount.pk).update(times_used=F("times_used") + 1)


@transaction.atomic
def create_order(*, lines, channel, initial_status, order_discount=None, payments=(), gift_card_payments=(),
                 verify_payment=None, can_manual_discount=False, approve_discount=None, expected_total=None, drawer=None,
                 tip=ZERO, staff_cards=(), staff_card_amount=None, **fields):
    """
    Create an order from a cart.

    Args:
        lines: cart lines from the browser (see pos_server.pricing).
        channel: "store", "web" or "delivery".
        initial_status: OrderStation status for every station involved. POS orders start APPROVED,
            online orders start PENDING and wait for approval on the KDS.
        order_discount: discount spec for the whole order (see pricing).
        payments: register payments, [{"method": "cash"|"card", "amount": ..., "tendered": ..., "checkoutId": <Square
            Terminal checkout, card only>}]. When given
            (the register), they plus the gift cards must add up to exactly the total.
        gift_card_payments: iterable of (card number, amount) to charge to gift cards.
        verify_payment: optional callable(remaining: Decimal) that raises OrderError if the part of the
            total not covered by gift cards hasn't been paid (online). Called inside the transaction.
        can_manual_discount: whether typed-in discounts are allowed (the register user's permission).
        approve_discount: otherwise, callable(manual spec) -> the approving manager's user id or None
            (pos_server/approvals.py: check).
        expected_total: the total the cashier saw. If the server's total differs, the order is refused.
        drawer: the register's open CashDrawerSession. Cash needs one; every register payment is linked to it.
        tip: added on top of the total (the online store's "Tip the team"); paid with the rest.
        staff_cards: gift_cards StaffCards on the order (one of each kind; gift_cards/staff_cards.py). A discount card
            takes its discount; a balance card pays `staff_card_amount` (None: as much as it can), counted with the
            gift cards; with "own order" the order goes under the holder's name.
        **fields: other Order fields (name, phone, special_instructions, to_go_order, start_time, authorization, cashier,
            device, customer, email).

    Returns the saved Order. Raises OrderError (and rolls everything back) if anything is wrong.
    """
    staff_cards = list(staff_cards or [])
    if staff_cards:
        from gift_cards.models import StaffCard
        staff_cards = list(StaffCard.objects.select_for_update().select_related("type", "user").filter(pk__in=[c.pk for c in staff_cards]))
    cart = price_cart(lines, order_discount=order_discount, channel=channel, to_go=bool(fields.get("to_go_order")),
                      can_manual=can_manual_discount, approve=approve_discount, staff_cards=staff_cards)
    if not cart.lines:
        raise OrderError(_("The cart is empty."))
    tip = to_money(tip or 0)
    total = cart.total + tip
    if expected_total is not None and to_money(expected_total) != total:
        raise OrderError(_("The total changed to $%(total)s. Please check the order and try again.") % {"total": total})

    gift_total = ZERO
    charges = []
    for number, amount in gift_card_payments:
        amount = to_money(amount)
        if amount == 0:
            continue
        card = GiftCard.objects.select_for_update().filter(number=number).first()
        if card is None:
            raise OrderError(_("Gift card x%(last4)s was not found.") % {"last4": str(number)[-4:]})
        if card.available_balance < amount:
            raise OrderError(_("Gift card x%(last4)s no longer has $%(amount)s available.") % {"last4": str(number)[-4:], "amount": amount})
        charges.append((card, amount))
        gift_total += amount
    if gift_total > total:
        raise OrderError(_("Gift card payments are more than the order total."))
    staff_pay = None  # (balance card, amount)
    for card in staff_cards:
        from gift_cards import staff_cards as staff
        if card.type.own_only:
            fields["name"] = staff.holder(card)[:140]
        if card.type.kind == card.type.BALANCE:
            can_pay = staff.payable(card, cart, total - gift_total)
            amount = can_pay if staff_card_amount is None else to_money(staff_card_amount)
            if amount < 0 or amount > can_pay:
                raise OrderError(_("%(card)s can pay at most $%(amount)s of this order.") % {"card": card.type.name, "amount": can_pay})
            if amount > 0:
                staff_pay = (card, amount)
                gift_total += amount

    tenders = []
    for payment in payments:
        method = payment.get("method") if isinstance(payment, dict) else None
        if method not in (OrderPayment.CASH, OrderPayment.CARD):
            raise OrderError(_("Unknown payment method."))
        amount = to_money(payment.get("amount"))
        tendered = None
        if method == OrderPayment.CASH:
            if drawer is None:
                raise OrderError(_("Open the cash drawer before taking cash."))
            tendered = to_money(payment.get("tendered") or amount)
            if tendered < amount:
                raise OrderError(_("Not enough cash was handed over."))
        checkout = str(payment.get("checkoutId") or "")[:64] if method == OrderPayment.CARD else ""
        tenders.append((method, amount, tendered, checkout))
    if payments and gift_total + sum((t[1] for t in tenders), ZERO) != total:
        raise OrderError(_("The payments don't add up to the total of $%(total)s.") % {"total": total})
    if verify_payment:
        verify_payment(total - gift_total)

    _count_discount_uses(cart)
    order = Order.objects.create(channel=channel, subtotal=cart.subtotal, discount=cart.discount, tax=cart.tax,
                                 fees=cart.fee_total, tip=tip, total=total, **fields)

    use = Counter()
    station_ids = set()
    for line in cart.lines:
        parent = OrderItem.objects.create(
            order=order, title=line.item.title, quantity=line.quantity, unit_price=line.unit_price,
            discount=line.discount.amount if line.discount else ZERO, note=line.note, selection=line.selection,
            **({"dish": line.item, "station": line.item.station, "mods": line.picks[0].mods} if line.kind == "dish"
               else {"combo": line.item, "mods": line.mods}),
        )
        if line.discount:
            OrderDiscount.objects.create(order=order, item=parent, discount=line.discount.discount, label=line.discount.label,
                                         kind=line.discount.kind, value=line.discount.value, amount=line.discount.amount,
                                         applied_by=fields.get("cashier"), approved_by_id=line.discount.approved_by)
        for picked in line.picks:
            if line.kind == "combo":
                OrderItem.objects.create(order=order, parent=parent, dish=picked.dish, station=picked.dish.station,
                                         title=picked.dish.title, slot=picked.slot, quantity=line.quantity, upcharge=picked.upcharge,
                                         mods=picked.mods, note=picked.note, selection=picked.selection)
            station_ids.add(picked.dish.station_id)
            for component_id, amount in picked.use.items():
                use[component_id] += amount * line.quantity
    if cart.order_discount:
        applied = cart.order_discount
        OrderDiscount.objects.create(order=order, discount=applied.discount, label=applied.label, kind=applied.kind,
                                     value=applied.value, amount=applied.amount, applied_by=fields.get("cashier"),
                                     approved_by_id=applied.approved_by)
    consume_stock(use)
    use_disposables(order, cart)

    OrderStation.objects.bulk_create(
        OrderStation(order=order, station_id=station_id, status=initial_status,
                     confirmed_at=order.timestamp if initial_status == OrderStation.STATUS_APPROVED else None)
        for station_id in station_ids
    )
    if staff_cards:
        from gift_cards import staff_cards as staff
        staff.record(order, staff_cards, cart, staff_pay[1] if staff_pay else ZERO, by=fields.get("cashier") or fields.get("customer"))
    if cart.staff_discount:
        applied = cart.staff_discount
        OrderDiscount.objects.create(order=order, label=applied.label, kind=applied.kind, value=applied.value, amount=applied.amount,
                                     applied_by=fields.get("cashier"))
    if staff_pay:
        OrderPayment.objects.create(order=order, method=OrderPayment.STAFF, amount=staff_pay[1], staff_card=staff_pay[0], drawer=drawer)
    for card, amount in charges:
        card.charge_card(amount)
        GiftCardAuthorization.objects.create(card=card, order=order, charged_balance=amount)
        OrderPayment.objects.create(order=order, method=OrderPayment.GIFT, amount=amount, gift_card=card, drawer=drawer)
    for method, amount, tendered, checkout in tenders:
        OrderPayment.objects.create(order=order, method=method, amount=amount, tendered=tendered, drawer=drawer,
                                    square_checkout_id=checkout)
    if verify_payment and total - gift_total > 0:
        OrderPayment.objects.create(order=order, method=OrderPayment.CARD, amount=total - gift_total, entry_method="online")
    order.picked_up = not station_ids
    order.save()
    return order


def refund_gift_cards(order):
    """Put every gift card charge for `order` back on its card, and staff card money back on theirs."""
    for auth in order.gift_card_auth.select_related("card"):
        auth.card.load_card(auth.charged_balance)
    from gift_cards import staff_cards
    staff_cards.put_back(order)


def orders_for_screens(queryset):
    """Prefetch everything collect_order() touches."""
    return queryset.select_related("authorization", "cashier").prefetch_related(
        "station_statuses__station", "items__station", "items__dish", "items__combo", "discounts", "payments", "delivery__courier",
    )


def _mod_tags(item):
    """
    An item's mods as [{"text", "kind"}] for the kitchen display, kind being "no" (left out), "add" (extra or
    add-on), "opt" (option or a part's substitute) or "side" (condiment). Mods are stored as readable text, so the kind comes from the
    order pricing.price_dish()/price_line() writes them in, counted from `selection`. The allergy line is left out
    (see `allergies` in _kitchen_rows). Anything that doesn't add up is shown as an option.
    """
    mods, selection = list(item.mods or []), item.selection or {}
    if item.combo_id:
        extras = selection.get("extras") or {}
        kinds = []  # a combo's own lines are its condiments
    else:
        extras = selection
        if selection.get("allergies") and mods:
            mods.pop()  # price_dish() adds the allergy line last
        kinds = ["opt"] * len(selection.get("swaps") or {})  # price_dish() writes substitutes first
        kinds += ["no" if count == 0 else "add" for count in (selection.get("parts") or {}).values()]
        for value in (selection.get("options") or {}).values():
            kinds += ["add"] * len(value) if isinstance(value, dict) else ["opt"]  # pick-any: one line per option
    kinds += ["side"] * len(extras.get("condiments") or {})
    if len(kinds) != len(mods):
        kinds = ["opt"] * len(mods)
    return [{"text": text, "kind": kind} for text, kind in zip(mods, kinds)]


def _kitchen_rows(order):
    """What stations make: standalone dishes and the dishes inside combos, by their kitchen ticket names."""
    items = list(order.items.all())
    parents = {item.id: item for item in items if item.combo_id or item.dish_id is None}
    rows = []
    for item in items:
        if item.station_id is None:
            continue
        parent = parents.get(item.parent_id)
        rows.append({
            "name": (item.dish.ticket_name if item.dish and item.dish.ticket_name else item.title),  # short name if set
            "quantity": item.quantity,
            "station": item.station.code,
            "mods": item.mods,
            "tags": _mod_tags(item),
            "allergies": (item.selection or {}).get("allergies", []),
            "note": item.note,
            "combo": ((parent.combo.ticket_name if parent.combo and parent.combo.ticket_name else parent.title) if parent else None),
            "combo_tags": _mod_tags(parent) if parent else [],
            "line": parent.id if parent else item.id,
            "slot": item.slot,
        })
    return rows


def _priced_lines(order):
    """Top-level lines with prices, for receipts and the admin."""
    items = list(order.items.all())
    children = {}
    for item in items:
        if item.parent_id:
            children.setdefault(item.parent_id, []).append(item)
    return [{
        "name": item.title,
        "quantity": item.quantity,
        "price": float(item.unit_price),
        "gross": float(item.gross),
        "discount": float(item.discount),
        "total": float(item.line_total),
        "mods": item.mods,
        "note": item.note,
        "children": [{"name": child.title, "slot": child.slot, "mods": child.mods, "note": child.note}
                     for child in children.get(item.id, [])],
    } for item in items if not item.parent_id]


def collect_order(order, done=False):
    """
    Serialize an order for the screens (KDS, status board, admin, order status page).

    `dishes` is what the stations make (combos are split into their dishes); `lines` is what was sold, with
    prices. `stations` maps each involved station's code to its OrderStation status.
    """
    if not order:
        return None
    delivery = order.delivery.first()
    return {
        'order_id': order.id,
        'dishes': _kitchen_rows(order),
        'lines': _priced_lines(order),
        'discounts': [{"label": d.label, "amount": float(d.amount), "item": d.item_id} for d in order.discounts.all()],
        'payments': [{"method": p.method, "amount": float(p.amount)} for p in order.payments.all()],
        'stations': order.station_status_map(),
        'in_progress_at': in_progress.isoformat() if (in_progress := order.in_progress_since()) else None,
        'ready_at': {row.station.code: row.ready_at.isoformat() for row in order.station_statuses.all() if row.ready_at},
        'name': order.name,
        'to_go_order': order.to_go_order,
        'channel': order.channel,
        'phone': order.phone,
        'cashier': (order.cashier.first_name or order.cashier.username) if order.cashier else None,
        'address': delivery.destination if delivery else None,
        # Delivery orders: who's taking it (None = nobody yet: every courier on shift passed, or nobody's on shift)
        'courier': (delivery.courier.first_name or delivery.courier.username) if delivery and delivery.courier else None,
        "special_instructions": order.special_instructions,
        "note_for_kitchen": order.note_for_kitchen,
        "timestamp": order.timestamp.isoformat(),
        "timestamp_pretty": order.timestamp,
        "start_time": order.start_time.isoformat(),
        "subtotal": float(order.subtotal),
        "discount": float(order.discount),
        "tax": float(order.tax),
        "total": float(order.total),
        "done": done,
        "picked_up": order.picked_up,
        "payment_id": order.authorization.payment_id if order.authorization else None,
    }


def station_list():
    return [station.as_dict() for station in Station.objects.filter(archived=False)]
