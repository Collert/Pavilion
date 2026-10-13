"""
Order creation and serialization, shared by the POS (pos_server.views.pos) and the online store
(online_store.views.place_order).

Carts come from the browser as a list of items like {"pk": <dish id>, "parent": <dish id>}. The browser's
prices and totals are never trusted: everything is priced here from the database.

Pricing rules (they mirror what the cart JS shows):
- A plain dish costs its price.
- A dish with choice components (e.g. "Breakfast" with a "Drink" choice) costs its price, and each chosen
  child (Coffee) is free. The child arrives as {"pk": coffee, "parent": breakfast}.
- An "only choices" dish (every component is a choice) is never ordered itself; its chosen children are
  charged at their own price.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.translation import gettext as _

from gift_cards.models import GiftCard, GiftCardAuthorization
from inventory.views import craft_component
from .models import Dish, Order, OrderDish, OrderStation, Station

CENT = Decimal("0.01")


class OrderError(Exception):
    """A cart that can't become an order. The message is safe to show to the customer or cashier."""


def to_money(value) -> Decimal:
    """Parse a user-supplied amount into a non-negative Decimal with 2 places."""
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        raise OrderError(_("Invalid amount: %(value)s") % {"value": value})
    if amount < 0 or not amount.is_finite():
        raise OrderError(_("Invalid amount: %(value)s") % {"value": value})
    return amount


def _choice_options(dish):
    """Components of `dish` that are choices, as a list of sets of allowed child dish IDs."""
    return [
        {child.id for child in component.child_dishes.all()}
        for component in dish.components.all()
        if component.child_dishes.exists()
    ]


def price_cart(items):
    """
    Validate and price a cart.

    Returns (counts, total): counts is a Counter {Dish: quantity} of the dishes to prepare (only-choice
    parents are left out), total is the Decimal amount to charge.
    """
    try:
        ids = {int(item["pk"]) for item in items}
        ids |= {int(item["parent"]) for item in items if item.get("parent")}
    except (KeyError, TypeError, ValueError):
        raise OrderError(_("The cart is malformed."))
    dishes = Dish.objects.select_related("station").prefetch_related("components__child_dishes").in_bulk(ids)
    missing = ids - set(dishes)
    if missing:
        raise OrderError(_("Some items in your cart no longer exist. Please refresh the menu."))

    only_choice = {dish_id: dish.check_if_only_choice_dish() for dish_id, dish in dishes.items()}

    # How many free picks each priced parent dish allows: one per choice component, per parent in the cart.
    free_picks = Counter()
    for item in items:
        if not item.get("parent"):
            dish = dishes[int(item["pk"])]
            if not only_choice[dish.id]:
                free_picks[dish.id] += len(_choice_options(dish))

    counts = Counter()
    total = Decimal("0.00")
    for item in items:
        dish = dishes[int(item["pk"])]
        if not (dish.in_stock or dish.force_in_stock):
            raise OrderError(_("%(dish)s is out of stock.") % {"dish": dish.title})
        parent_id = int(item["parent"]) if item.get("parent") else None
        if parent_id:
            parent = dishes[parent_id]
            if not any(dish.id in options for options in _choice_options(parent)):
                raise OrderError(_("%(dish)s is not an option for %(parent)s.") % {"dish": dish.title, "parent": parent.title})
            if only_choice[parent_id]:
                total += dish.price
            else:
                free_picks[parent_id] -= 1
                if free_picks[parent_id] < 0:
                    raise OrderError(_("Too many choices for %(parent)s.") % {"parent": parent.title})
        elif only_choice[dish.id]:
            continue  # the parent of a choice; the children carry the price
        else:
            total += dish.price
        counts[dish] += 1
    return counts, total.quantize(CENT)


def consume_stock(dish, quantity):
    """Take the components for `quantity` x `dish` out of inventory, crafting self-crafting ones first."""
    for dc in dish.dishcomponent_set.select_related("component"):
        needed = dc.quantity * quantity
        if dc.component.crafting_option == "auto":
            craft_component(dc.component.id, needed)
            dc.component.refresh_from_db()
        dc.component.inventory -= needed
        dc.component.save()


@transaction.atomic
def create_order(*, items, channel, initial_status, gift_card_payments=(), verify_payment=None,
                 discount_amount=0, discount_percent=0, **fields):
    """
    Create an order from a cart.

    Args:
        items: cart items from the browser (see module docstring).
        channel: "store", "web" or "delivery".
        initial_status: OrderStation status for every station involved. POS orders start APPROVED,
            online orders start PENDING and wait for approval on the KDS.
        gift_card_payments: iterable of (card number, amount) to charge to gift cards.
        verify_payment: optional callable(remaining: Decimal) that raises OrderError if the part of the
            total not covered by gift cards hasn't been paid. Called inside the transaction.
        discount_amount, discount_percent: POS discounts (staff only; never pass customer input here).
            Applied like the POS cart does: total - amount - total * percent / 100, floored at 0.
        **fields: other Order fields (name, phone, special_instructions, to_go_order, start_time, authorization).

    Returns the saved Order. Raises OrderError (and rolls everything back) if anything is wrong.
    """
    counts, subtotal = price_cart(items)
    if not counts:
        raise OrderError(_("The cart is empty."))
    percent = to_money(discount_percent)
    if percent > 100:
        raise OrderError(_("A discount can't be more than 100%."))
    total = max(subtotal - to_money(discount_amount) - (subtotal * percent / 100), Decimal("0")).quantize(CENT)

    gift_total = Decimal("0.00")
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
    if verify_payment:
        verify_payment(total - gift_total)

    order = Order.objects.create(channel=channel, total=total, discount=subtotal - total, **fields)
    station_ids = set()
    for dish, quantity in counts.items():
        OrderDish.objects.create(order=order, dish=dish, quantity=quantity)
        station_ids.add(dish.station_id)
        consume_stock(dish, quantity)
    OrderStation.objects.bulk_create(
        OrderStation(order=order, station_id=station_id, status=initial_status) for station_id in station_ids
    )
    for card, amount in charges:
        card.charge_card(amount)
        GiftCardAuthorization.objects.create(card=card, order=order, charged_balance=amount)
    order.picked_up = not station_ids
    order.save()
    return order


def refund_gift_cards(order):
    """Put every gift card charge for `order` back on its card."""
    for auth in order.gift_card_auth.select_related("card"):
        auth.card.load_card(auth.charged_balance)


def orders_for_screens(queryset):
    """Prefetch everything collect_order() touches."""
    return queryset.select_related("authorization").prefetch_related(
        "station_statuses__station", "orderdish_set__dish__station", "delivery",
    )


def collect_order(order, done=False):
    """
    Serialize an order for the screens (KDS, status board, admin, order status page).

    `stations` maps each involved station's code to its OrderStation status. Stations that aren't
    involved are left out.
    """
    if not order:
        return None
    delivery = order.delivery.first()
    return {
        'order_id': order.id,
        'dishes': [
            {
                'name': od.dish.title,
                'quantity': od.quantity,
                'price': float(od.dish.price),
                'station': od.dish.station.code,
            }
            for od in order.orderdish_set.all()
        ],
        'stations': order.station_status_map(),
        'name': order.name,
        'to_go_order': order.to_go_order,
        'channel': order.channel,
        'phone': order.phone,
        'address': delivery.destination if delivery else None,
        "special_instructions": order.special_instructions,
        "timestamp": order.timestamp.isoformat(),
        "timestamp_pretty": order.timestamp,
        "start_time": order.start_time.isoformat(),
        "total": float(order.total),
        "done": done,
        "picked_up": order.picked_up,
        "payment_id": order.authorization.payment_id if order.authorization else None,
    }


def station_list():
    return [station.as_dict() for station in Station.objects.all()]
