"""
Emails about orders (misc_tools/emails.py): "Ready for pickup" when the kitchen finishes an online pickup order, and
"Refund issued" when one is refunded. Sent after the change is saved, in the customer's language (their account's,
else the store's default: staff did it), and a failure is only logged.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


def _send(kind, order, data):
    from misc_tools.email import EmailError, language_for, send

    def go():
        lang = language_for(order.email, user=order.customer, fallback=settings.LANGUAGE_CODE)
        with translation.override(lang):
            payload = {"name": (order.name or "").split(" ")[0] or (order.customer.first_name if order.customer else ""),
                       "number": order.id, **{k: str(v) for k, v in data().items()}}
        try:
            send(kind, order.email, payload, language=lang)
        except EmailError:
            logger.exception("The %s email for order %s wasn't sent", kind, order.id)

    transaction.on_commit(go)


def ready(order):
    """The kitchen finished an online pickup order."""
    if order.channel != "web" or not order.email:
        return
    from admin_portal.models import StoreSettings
    store = StoreSettings.load()
    _send("order_ready", order, lambda: {"address": ", ".join(filter(None, [store.address, store.city]))})


def refunded(order, refund):
    if not order.email:
        return

    def data():
        where = {"card": _("your card"), "gift": _("your gift card"), "staff": _("your staff card"), "cash": _("cash")}
        places = []
        for part in refund.parts or []:
            label = part.get("label") if part["method"] == "card" and part.get("label") else where.get(part["method"], "")
            if label and label not in places:
                places.append(label)
        return {"amount": f"${refund.amount:.2f}", "to": _(" and ").join(places) or where["card"], "reason": refund.reason}

    _send("refund_issued", order, data)
