"""
What staff do to gift cards (the admin's Gift cards page): issue one from the store, resend its email, freeze it,
adjust its balance, and delete cards that have been empty for 30 days (manage.py gift_cards_cleanup, daily).

A card's number is only known while it's being made: `issue` returns it once, to show, print or email, and only its
hash and last 4 are kept (models.GiftCard). Emails sent later carry a signed balance link instead (`balance_link`).
"""
import datetime
import logging
from decimal import Decimal

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from misc_tools.email import send as send_email

from .models import GiftCard, GiftCardDesign, GiftCardEvent

logger = logging.getLogger(__name__)
SALT = "gift-card-balance"


def who(user) -> str:
    return (user.get_full_name() or user.username) if user else ""


def pretty(number) -> str:
    return " ".join(number[i:i + 4] for i in range(0, len(number), 4))


def balance_link(request, card) -> str:
    """A link to the card's balance page that doesn't contain its number."""
    return request.build_absolute_uri(reverse("gift_balance", args=[signing.dumps(card.id, salt=SALT)]))


def from_token(token):
    try:
        return GiftCard.objects.filter(pk=signing.loads(token, salt=SALT)).first()
    except signing.BadSignature:
        return None


@transaction.atomic
def issue(*, design, amount: Decimal, recipient, email, message, note, by, request=None, send=False):
    """A gift from the store: (card, its full number). Emails the card when `send`."""
    card = GiftCard.objects.create(design=design, available_balance=amount, original=amount, recipient=recipient[:60], email=email[:64],
                                   message=message[:120], note=note[:120], source=GiftCard.STAFF, buyer=who(by)[:80], issued_by=by)
    number = card.number
    GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.ISSUE, amount=amount, balance=amount, by=by,
                                 note=_("Issued by %(name)s") % {"name": who(by)})
    if send and email and request is not None:
        from admin_portal.models import StoreSettings
        data = {"name": recipient, "amount": f"${amount:,.2f}", "message": message, "last4": card.last4,
                "store": StoreSettings.load().name, "card_url": request.build_absolute_uri(reverse("card_display", args=[number]))}
        transaction.on_commit(lambda: _send("gift_card_issued", email, data, fallback=settings.LANGUAGE_CODE))
    return card, number


def _send(kind, email, data, fallback=None) -> bool:
    """Send one; True only when listmonk took it. `fallback`: the language when the recipient has no account
    (the store's default for what staff send; else the page's)."""
    try:
        return bool(send_email(kind, email, data, fallback=fallback))
    except Exception:
        logger.exception("Gift card email (%s) failed", kind)
        return False


def email_sold(request, card, number, sender="", fallback=None) -> bool:
    """Email a card someone just bought (the register or the store) to its recipient, with its number."""
    from admin_portal.models import StoreSettings
    data = {"name": card.recipient or _("there"), "card_url": request.build_absolute_uri(reverse("card_display", args=[number])),
            "amount": f"${card.original:,.2f}", "last4": card.last4, "message": card.message, "store": StoreSettings.load().name}
    if sender:
        data["sender"] = sender
    return _send("gift_card_gifted" if sender else "gift_card", card.email, data, fallback=fallback)


def email_receipt(card, buyer) -> bool:
    """The buyer's note that the card they bought someone online is on its way (gift_card_receipt)."""
    if not buyer or not buyer.email or buyer.email.lower() == (card.email or "").lower():
        return False
    return _send("gift_card_receipt", buyer.email, {"name": buyer.first_name or buyer.username, "recipient": card.recipient or card.email,
                                                    "amount": f"${card.original:,.2f}", "last4": card.last4})


def card_json(card, number, sent=False) -> dict:
    """A card just made, for the screen that shows its number once. Money in cents."""
    design = card.design
    return {"number": pretty(number), "last4": card.last4, "amount": int(card.original * 100), "design": design.name if design else "",
            "color": design.color if design else "#1F4A3A", "ink": design.ink if design else "light",
            "image": design.image.url if design and design.image else "", "to": card.recipient, "email": card.email, "sent": sent}


def resend(card, request) -> bool:
    """Email the card again: its last 4, balance and a balance link (the full number is gone)."""
    from admin_portal.models import StoreSettings
    return _send("gift_card_balance", card.email, {
        "name": card.recipient, "last4": card.last4, "balance": f"${card.available_balance:,.2f}",
        "store": StoreSettings.load().name, "balance_url": balance_link(request, card)}, fallback=settings.LANGUAGE_CODE)


def set_frozen(card, frozen, by):
    card.frozen = frozen
    card.save(update_fields=["frozen"])
    GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.NOTE, balance=card.available_balance, by=by,
                                 note=(_("Frozen by %(name)s") if frozen else _("Unfrozen by %(name)s")) % {"name": who(by).split(" ")[0]})


def adjust_problem(card, delta: Decimal) -> str:
    if delta < 0 and -delta > card.available_balance:
        return _("Can’t remove more than the $%(left)s left.") % {"left": f"{card.available_balance:,.2f}"}
    if delta > 0 and card.available_balance + delta > GiftCard.MAX_BALANCE:
        return _("Cards can hold up to $1,000.")
    return ""


@transaction.atomic
def adjust(card, delta: Decimal, why, by):
    card = GiftCard.objects.select_for_update().get(pk=card.pk)
    card.available_balance += delta
    card.save()
    GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.ADJUST, amount=delta, balance=card.available_balance, by=by,
                                 note=f"{why[:80]} · {who(by).split(' ')[0]}")
    return card


def cleanup(today=None) -> int:
    """Delete cards empty for 30 days. Their orders keep their payments (the card link goes blank)."""
    today = today or timezone.localdate()
    cutoff = today - datetime.timedelta(days=GiftCard.KEEP_EMPTY_DAYS)
    cards = GiftCard.objects.filter(available_balance__lte=0, empty_since__lte=cutoff)
    count = cards.count()
    cards.delete()
    return count


def designs_on_sale():
    """What staff can issue a card in: every design that isn't deleted."""
    return GiftCardDesign.objects.filter(archived=False)
