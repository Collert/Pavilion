"""
Payments (Settings; no mockup, laid out like the Email page): connect the store's own Square account with OAuth
(payments/square_oauth.py), pick the location payments go to, and see what Square needs to know about Pavilion
(its webhook address and events). Terminals are paired from each register.
"""
import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from payments import square_oauth
from payments.models import SquareConnection, SquareTerminal

logger = logging.getLogger(__name__)

SESSION_STATE = "square_oauth_state"
WEBHOOK_EVENTS = ["terminal.checkout.updated", "device.code.paired"]


def callback_url(request) -> str:
    return request.build_absolute_uri(reverse("square_oauth_callback"))


def webhook_url(request) -> str:
    return request.build_absolute_uri(reverse("square_webhook_plain"))


def page(request) -> dict:
    connection = SquareConnection.current()
    places, problem = [], ""
    if connection:
        try:
            places = square_oauth.locations(connection)
        except Exception as e:  # Square unreachable or the token was revoked from Square's side
            logger.warning("Listing Square locations failed", exc_info=True)
            problem = str(e) or _("Square didn't answer.")
    facts = []
    if connection:
        facts = [(_("Account"), connection.business_name or connection.merchant_id),
                 (_("Square"), _("Sandbox (test payments)") if connection.environment == "sandbox" else _("Live")),
                 (_("Connected"), date_format(timezone.localtime(connection.connected_at), "M j, Y")
                  + (f" · {connection.connected_by.first_name or connection.connected_by.username}" if connection.connected_by else "")),
                 (_("Access renews"), date_format(timezone.localtime(connection.expires_at), "M j, Y") if connection.expires_at else "—")]
    return {
        "configured": square_oauth.configured(), "connection": connection, "facts": facts, "problem": problem,
        "locations": places, "environment": square_oauth.environment(),
        "fallback": not connection and bool(settings.SQUARE_ACCESS_TOKEN),
        "callback": callback_url(request), "webhook": webhook_url(request), "events": WEBHOOK_EVENTS,
        "webhook_key": bool(settings.SQUARE_WEBHOOK_SIGNATURE_KEY),
        "terminals": SquareTerminal.objects.filter(register__isnull=False, status="PAIRED").select_related("register"),
        "scopes": square_oauth.SCOPES,
    }
