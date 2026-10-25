"""
Connecting the store's own Square account (the admin's Payments page) with OAuth.

Pavilion is one Square app (SQUARE_APPLICATION_ID and SQUARE_APPLICATION_SECRET, from whoever runs it; its OAuth
redirect URL is https://<host>/admin/payments/square/callback). A manager presses "Connect with Square", signs in to
their Square account and allows Pavilion; Square sends them back with a code, which becomes an access token for
their account (payments.SquareConnection, encrypted). Every Square call then uses it (payments/square.py:
square_client), with the location they pick. Access tokens last 30 days: they're refreshed when a call finds one with
less than a week left, and daily by `manage.py square_refresh`. Disconnecting revokes Pavilion's access in Square.
"""
import datetime
import logging
import secrets as pysecrets
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from square.client import Client

from .models import SquareConnection
from .secrets import decrypt, encrypt

logger = logging.getLogger(__name__)

# What Pavilion does in the store's account: payments, Terminal checkouts and refunds; pairing Terminals; its
# locations and name; customers and their saved cards
SCOPES = ["PAYMENTS_READ", "PAYMENTS_WRITE", "DEVICE_CREDENTIAL_MANAGEMENT", "MERCHANT_PROFILE_READ", "CUSTOMERS_READ", "CUSTOMERS_WRITE"]
REFRESH_WHEN_LEFT = datetime.timedelta(days=7)


class OAuthError(Exception):
    """Square refused, or Pavilion's Square app isn't set up. The message can be shown."""


def environment() -> str:
    return "production" if (settings.SQUARE_ENVIRONMENT or "").lower() == "production" else "sandbox"


def configured() -> bool:
    return bool(settings.SQUARE_APPLICATION_ID and getattr(settings, "SQUARE_APPLICATION_SECRET", ""))


def _app_client():
    """A client with no seller token, for the OAuth calls themselves."""
    return Client(environment=environment())


def seller_client(token: str):
    from square.http.auth.o_auth_2 import BearerAuthCredentials
    return Client(bearer_auth_credentials=BearerAuthCredentials(access_token=token), environment=environment())


def new_state() -> str:
    return pysecrets.token_urlsafe(24)


def authorize_url(state: str, redirect_uri: str) -> str:
    """Where "Connect with Square" sends the manager."""
    host = "https://connect.squareup.com" if environment() == "production" else "https://connect.squareupsandbox.com"
    query = {"client_id": settings.SQUARE_APPLICATION_ID, "scope": " ".join(SCOPES), "session": "false",
             "state": state, "redirect_uri": redirect_uri}
    return f"{host}/oauth2/authorize?{urlencode(query)}"


def _errors(result) -> str:
    return "; ".join(str(e.get("detail") or e.get("code") or e) for e in (result.errors or [])) or "Square refused"


def _store_tokens(connection, body):
    connection.access_token = encrypt(body["access_token"])
    if body.get("refresh_token"):
        connection.refresh_token = encrypt(body["refresh_token"])
    connection.expires_at = parse_datetime(body["expires_at"]) if body.get("expires_at") else None


@transaction.atomic
def connect(code: str, redirect_uri: str, user=None) -> SquareConnection:
    """Swap the code Square sent back for the store's tokens, and remember its account. Replaces any earlier one."""
    if not configured():
        raise OAuthError("Pavilion's Square app isn't set up: SQUARE_APPLICATION_ID and SQUARE_APPLICATION_SECRET.")
    result = _app_client().o_auth.obtain_token({
        "client_id": settings.SQUARE_APPLICATION_ID, "client_secret": settings.SQUARE_APPLICATION_SECRET,
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    if not result.is_success():
        raise OAuthError(_errors(result))
    body = result.body
    SquareConnection.objects.all().delete()
    connection = SquareConnection(merchant_id=body.get("merchant_id", ""), environment=environment(), connected_by=user)
    _store_tokens(connection, body)
    connection.save()
    seller = seller_client(body["access_token"])
    merchant = seller.merchants.retrieve_merchant(connection.merchant_id) if connection.merchant_id else None
    if merchant is not None and merchant.is_success():
        connection.business_name = merchant.body.get("merchant", {}).get("business_name", "")
    places = locations(connection, client=seller)
    if len(places) == 1:  # one location: that's where payments go
        connection.location_id, connection.location_name = places[0]["id"], places[0]["name"]
    connection.save()
    return connection


def refresh(connection) -> SquareConnection:
    """A new access token from the refresh token (Square keeps the refresh token valid while it's used)."""
    token = decrypt(connection.refresh_token)
    if not token or not configured():
        raise OAuthError("This Square connection can't be refreshed. Connect Square again.")
    result = _app_client().o_auth.obtain_token({
        "client_id": settings.SQUARE_APPLICATION_ID, "client_secret": settings.SQUARE_APPLICATION_SECRET,
        "refresh_token": token, "grant_type": "refresh_token"})
    if not result.is_success():
        raise OAuthError(_errors(result))
    _store_tokens(connection, result.body)
    connection.refreshed_at = timezone.now()
    connection.save(update_fields=["access_token", "refresh_token", "expires_at", "refreshed_at"])
    return connection


def access_token(connection) -> str:
    """The connection's access token, refreshed first when it has less than a week left (once, across workers)."""
    if connection.expires_at and connection.expires_at - timezone.now() < REFRESH_WHEN_LEFT:
        with transaction.atomic():
            fresh = SquareConnection.objects.select_for_update().get(pk=connection.pk)
            if fresh.expires_at and fresh.expires_at - timezone.now() < REFRESH_WHEN_LEFT:
                try:
                    fresh = refresh(fresh)
                except OAuthError:
                    logger.warning("Refreshing the Square token failed", exc_info=True)
            connection = fresh
    return decrypt(connection.access_token)


def locations(connection, client=None) -> list[dict]:
    """The account's active locations: [{"id", "name", "address"}]."""
    client = client or seller_client(access_token(connection))
    result = client.locations.list_locations()
    if not result.is_success():
        raise OAuthError(_errors(result))
    out = []
    for place in result.body.get("locations", []):
        if place.get("status", "ACTIVE") != "ACTIVE":
            continue
        address = place.get("address", {})
        out.append({"id": place["id"], "name": place.get("name", ""),
                    "address": ", ".join(x for x in (address.get("address_line_1"), address.get("locality")) if x)})
    return out


def disconnect(connection):
    """Revoke Pavilion's access in Square and forget the tokens. The tokens go even if Square can't be reached."""
    token = decrypt(connection.access_token)
    if token and configured():
        try:
            result = _app_client().o_auth.revoke_token({"client_id": settings.SQUARE_APPLICATION_ID, "access_token": token},
                                                       f"Client {settings.SQUARE_APPLICATION_SECRET}")
            if not result.is_success():
                logger.warning("Square didn't revoke the token: %s", _errors(result))
        except Exception:
            logger.warning("Revoking the Square token failed", exc_info=True)
    connection.delete()
