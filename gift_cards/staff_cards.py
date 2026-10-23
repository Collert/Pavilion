"""
Staff cards (gift_cards.models StaffCardType / StaffCard / StaffCardUse; edited on the admin's Staff cards page).

A card is found by its number (scanned at the register, or the signed-in staff member's own cards at the store's
checkout). A *balance* card pays like a gift card (create_order's `staff_cards`, an OrderPayment with method
"staff"); it's topped up lazily: whenever it's read, every refill moment since `refilled_at` (local midnight on the
type's day) is counted and the balance reset to `amount` or, carrying over, raised by it up to `cap`. A *discount*
card takes its percent or amount off in pricing (`apply_to`, called by price_cart): of the dishes it covers (no
alcohol: dishes made with an `Ingredient.alcohol` are left out), capped per order, as often as `uses` allows; if it
doesn't stack, it and the other discounts don't add up and the bigger one is kept. With `own_only` the order goes
under the holder's name. Rejected orders and refunds put money back (`put_back`).
"""
import calendar
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy

from .models import StaffCard, StaffCardType, StaffCardUse

ZERO, CENT = Decimal("0.00"), Decimal("0.01")
CHANNEL = {"store": "store", "web": "pickup", "delivery": "delivery"}  # Order.channel -> the type's channels
WEEKDAYS = [gettext_lazy("Monday"), gettext_lazy("Tuesday"), gettext_lazy("Wednesday"), gettext_lazy("Thursday"), gettext_lazy("Friday"),
            gettext_lazy("Saturday"), gettext_lazy("Sunday")]


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def find(number):
    """The card with this number, if it's still in use (not revoked, its type not deleted)."""
    number = str(number or "").strip()
    if not number:
        return None
    return (StaffCard.objects.select_related("type", "user")
            .filter(number=number, revoked_at__isnull=True, type__archived=False).first())


def holder(card) -> str:
    u = card.user
    return f"{u.first_name} {u.last_name}".strip() or u.username


# ---------- refills ----------

def _refills_on(t: StaffCardType, day: datetime.date) -> bool:
    if t.refill == "day":
        return True
    if t.refill == "week":
        return day.weekday() == t.refill_weekday
    if t.refill == "month":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day == (last if t.refill_day == "last" else min(int(t.refill_day or 1), last))
    return False


def refills_between(t, since, now) -> int:
    """How many refill moments (local midnight on a refill day) fall in (since, now]. "Every shift" cards refill
    when they clock in instead (clocked_in)."""
    if t.kind != StaffCardType.BALANCE or t.refill in ("never", "shift"):
        return 0
    first, last = timezone.localdate(since) + datetime.timedelta(days=1), timezone.localdate(now)
    if (last - first).days > 400:
        first = last - datetime.timedelta(days=400)
    count, day = 0, first
    while day <= last:
        count += _refills_on(t, day)
        day += datetime.timedelta(days=1)
    return count


def refill(card, now=None):
    """Top the card up for the refills it missed. Saves when something changed; returns the card."""
    now = now or timezone.now()
    n = refills_between(card.type, card.refilled_at, now)
    if n:
        t = card.type
        card.balance = t.amount if t.leftover == "reset" else min(card.balance + t.amount * n, max(t.cap, t.amount))
        card.refilled_at = now
        card.save(update_fields=["balance", "refilled_at"])
    return card


def clocked_in(user, now=None):
    """Clocking in (team/clock.py) tops up the holder's "every shift" balance cards."""
    now = now or timezone.now()
    for card in StaffCard.objects.select_related("type").filter(user=user, revoked_at__isnull=True, type__archived=False,
                                                                 type__kind=StaffCardType.BALANCE, type__refill="shift"):
        t = card.type
        card.balance = t.amount if t.leftover == "reset" else min(card.balance + t.amount, max(t.cap, t.amount))
        card.refilled_at = now
        card.save(update_fields=["balance", "refilled_at"])


def on_the_clock(user) -> bool:
    from team.models import TimeEntry
    return TimeEntry.objects.filter(user=user, clock_out__isnull=True).exists()


def next_refill(t, now=None):
    """The date of the next refill, or None."""
    if t.kind != StaffCardType.BALANCE or t.refill in ("never", "shift"):
        return None
    day = timezone.localdate(now or timezone.now())
    for _i in range(62):
        day += datetime.timedelta(days=1)
        if _refills_on(t, day):
            return day
    return None


def ordinal(n) -> str:
    return {1: _("1st"), 15: _("15th")}.get(n, f"{n}th")


def refill_text(t) -> str:
    """'every day', 'every Monday', 'on the 1st of each month', 'on the last day of each month' or ''."""
    if t.refill == "shift":
        return _("every shift")
    if t.refill == "day":
        return _("every day")
    if t.refill == "week":
        return _("every %(day)s") % {"day": str(WEEKDAYS[t.refill_weekday % 7])}
    if t.refill == "month":
        return _("on the last day of each month") if t.refill_day == "last" else _("on the %(nth)s of each month") % {"nth": ordinal(int(t.refill_day or 1))}
    return ""


# ---------- rules ----------

def window_start(uses, now):
    local = timezone.localtime(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if uses == "day":
        return midnight
    if uses == "week":
        return midnight - datetime.timedelta(days=local.weekday())
    return None


def problem(card, channel, now=None) -> str:
    """Why this card can't be used on an order of `channel` right now ("" when it can)."""
    t, now = card.type, now or timezone.now()
    if card.revoked_at or t.archived:
        return _("This staff card was revoked.")
    if not card.user.is_active:
        return _("%(name)s can’t sign in any more, so their card doesn’t work.") % {"name": holder(card)}
    if not t.live:
        return _("%(card)s cards are turned off.") % {"card": t.name}
    if t.on_shift and not on_the_clock(card.user):
        return _("%(card)s works only while %(name)s is clocked in.") % {"card": t.name, "name": card.user.first_name or holder(card)}
    if CHANNEL.get(channel, channel) not in (t.channels or []):
        return _("%(card)s doesn’t work on %(where)s orders.") % {
            "card": t.name, "where": dict(StaffCardType.CHANNELS).get(CHANNEL.get(channel, channel), channel).lower()}
    if t.kind == StaffCardType.DISCOUNT and t.uses != "any":
        since = window_start(t.uses, now)
        if card.uses.filter(kind=StaffCardUse.DISCOUNT, at__gte=since).exists():
            return _("%(name)s already used %(card)s today.") % {"name": card.user.first_name or holder(card), "card": t.name} if t.uses == "day" \
                else _("%(name)s already used %(card)s this week.") % {"name": card.user.first_name or holder(card), "card": t.name}
    if t.kind == StaffCardType.BALANCE and refill(card, now).balance <= 0:
        when = next_refill(t, now)
        return (_("Nothing left on this card until it refills on %(day)s.") % {"day": date_format(when, "M j")}
                if when else _("This card is used up."))
    return ""


def alcoholic_dish_ids(dish_ids) -> set:
    """Which of these dishes are made with an alcoholic ingredient (pos_server/dish_info.py)."""
    from pos_server.dish_info import DISH_PREFETCH, component_ingredients
    from pos_server.models import Dish
    out = set()
    for dish in Dish.objects.filter(pk__in=set(dish_ids)).prefetch_related(*DISH_PREFETCH):
        if any(i.alcohol for dc in dish.dishcomponent_set.all() for i in component_ingredients(dc.component)):
            out.add(dish.id)
    return out


def covered_gross(t, lines) -> Decimal:
    """What the card covers of the priced lines: all of it, or the lines without alcohol."""
    if not t.no_alcohol:
        return sum((line.gross for line in lines), ZERO)
    dish_ids = [p.dish.id for line in lines for p in line.picks if p.dish]
    boozy = alcoholic_dish_ids(dish_ids) if dish_ids else set()
    return sum((line.gross for line in lines if not any(p.dish and p.dish.id in boozy for p in line.picks)), ZERO)


def apply_to(cart, cards, channel, now):
    """
    pricing.price_cart's step for staff cards (at most one of each kind). Raises OrderError (from pos_server.pricing)
    for a card that can't be used. A discount card becomes cart.staff_discount (an AppliedDiscount), dropping the
    other discounts when it doesn't stack and takes more off, or leaving itself out (cart.staff_note) when they do.
    """
    from pos_server.pricing import AppliedDiscount, OrderError
    kinds = [c.type.kind for c in cards]
    if len(kinds) != len(set(kinds)):
        raise OrderError(_("Use one staff card of each kind per order."))
    for card in cards:
        why = problem(card, channel, now)
        if why:
            raise OrderError(why)
    card = next((c for c in cards if c.type.kind == StaffCardType.DISCOUNT), None)
    if card is None:
        return
    t = card.type
    covered = covered_gross(t, cart.lines)
    if covered <= 0:
        raise OrderError(_("Nothing on this order is covered by %(card)s.") % {"card": t.name})
    others = cart.discount
    base = covered - (others * covered / cart.subtotal if t.stack and cart.subtotal else ZERO)
    base = max(base, ZERO)
    if t.discount_kind == "pct":
        amount = money(base * t.discount_value / 100)
        if t.discount_cap:
            amount = min(amount, t.discount_cap)
    else:
        amount = min(t.discount_value, base)
    applied = AppliedDiscount(label=t.name, kind=t.discount_kind, value=t.discount_value, amount=amount)
    if not t.stack and others > 0:
        if amount <= others:
            cart.staff_note = _("The other discounts take more off, so %(card)s isn’t used.") % {"card": t.name}
            return
        why = _("%(card)s takes more off, so it’s used instead.") % {"card": t.name}
        for line in cart.lines:
            if line.discount:
                line.discount, line.discount_error = None, why
        if cart.order_discount:
            cart.order_discount, cart.order_discount_error = None, why
    cart.staff_discount = applied


def payable(card, cart, total) -> Decimal:
    """How much of `total` a balance card can pay: its balance, and only the covered share (no alcohol)."""
    covered = covered_gross(card.type, cart.lines)
    share = total if covered >= cart.subtotal or not cart.subtotal else money(total * covered / cart.subtotal)
    return max(min(card.balance, share), ZERO)


# ---------- after the order ----------

def record(order, cards, cart, spent, by=None):
    """Write what the cards did on a new order: the discount use, and the balance spent (already checked)."""
    now = timezone.now()
    for card in cards:
        if card.type.kind == StaffCardType.DISCOUNT and cart.staff_discount:
            StaffCardUse.objects.create(card=card, order=order, kind=StaffCardUse.DISCOUNT, amount=cart.staff_discount.amount, by=by)
        elif card.type.kind == StaffCardType.BALANCE and spent > 0:
            StaffCard.objects.filter(pk=card.pk).update(balance=card.balance - spent)
            StaffCardUse.objects.create(card=card, order=order, kind=StaffCardUse.SPEND, amount=spent, by=by)
        StaffCard.objects.filter(pk=card.pk).update(last_used=now)


def put_back(order, amount=None, card=None):
    """Money back onto balance cards for a rejected order (everything; its discount uses stop counting) or a refund
    (`amount` onto `card`)."""
    if amount is not None:
        StaffCard.objects.filter(pk=card.pk).update(balance=card.balance + amount)
        StaffCardUse.objects.create(card=card, order=order, kind=StaffCardUse.BACK, amount=amount)
        return
    for use in order.staff_card_uses.select_related("card"):
        if use.kind == StaffCardUse.SPEND:
            use.card.refresh_from_db()
            StaffCard.objects.filter(pk=use.card_id).update(balance=use.card.balance + use.amount)
            StaffCardUse.objects.create(card=use.card, order=order, kind=StaffCardUse.BACK, amount=use.amount)
        elif use.kind == StaffCardUse.DISCOUNT:
            use.delete()


def month_stats(t, now=None) -> dict:
    """{"used": money spent or taken off this month (less what went back), "orders": how many orders}."""
    local = timezone.localtime(now or timezone.now())
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    uses = StaffCardUse.objects.filter(card__type=t, at__gte=start)
    out = uses.exclude(kind=StaffCardUse.BACK).aggregate(s=Sum("amount"))["s"] or ZERO
    back = uses.filter(kind=StaffCardUse.BACK).aggregate(s=Sum("amount"))["s"] or ZERO
    return {"used": out - back, "orders": uses.exclude(kind=StaffCardUse.BACK).exclude(order=None).values("order").distinct().count()}


def cards_for(user, channel):
    """A signed-in staff member's cards that work for an online order now (one of each kind)."""
    out, kinds = [], set()
    if not (user and user.is_authenticated and user.is_staff):
        return out
    for card in StaffCard.objects.select_related("type", "user").filter(user=user, revoked_at__isnull=True, type__archived=False):
        if card.type.kind not in kinds and not problem(card, channel):
            out.append(card)
            kinds.add(card.type.kind)
    return out
