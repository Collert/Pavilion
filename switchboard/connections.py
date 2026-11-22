"""
Connecting a Switchboard (protocol §2) and knowing its calls afterwards (§3).

The admin approves on the consent page (views.authorize) and Switchboard gets a one-time code (new_code); it trades the
code for an API key and a webhook secret (redeem), both shown that once. Calls carry the key as a bearer token
(authenticate). Disconnecting, from either side, deletes the connection: its key stops working and events stop.
"""
import secrets as tokens
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from payments import secrets

from .models import AuthorizeCode, IdempotencyRecord, SwitchboardConnection, hash_token

KEY_PREFIX = "sbk_"
BUILDING = "pavilion"


class InvalidCode(Exception):
    """A wrong, used or expired authorize code."""


def instance_name() -> str:
    from admin_portal.models import StoreSettings
    return StoreSettings.load().name


def http_url(value) -> str:
    """`value` if it's an http(s) address with a host, else ""."""
    value = str(value or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    return value if parts.scheme in ("http", "https") and parts.netloc and len(value) <= 1000 else ""


def new_code(user, name, callback) -> str:
    """A fresh one-time code for the Switchboard whose callback the admin just approved."""
    code = tokens.token_urlsafe(32)
    AuthorizeCode.objects.create(code_hash=hash_token(code), name=str(name or "")[:120], host=urlsplit(callback).netloc[:255],
                                 created_by=user if user and user.is_authenticated else None)
    AuthorizeCode.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()  # old ones don't pile up
    return code


@transaction.atomic
def redeem(code, webhook_url, name, base_url="") -> tuple[SwitchboardConnection, str, str]:
    """Trade a code for a connection: (connection, its API key, its webhook secret). Raises InvalidCode."""
    now = timezone.now()
    row = AuthorizeCode.objects.filter(code_hash=hash_token(str(code or "")), used_at__isnull=True, expires_at__gt=now).first() if code else None
    # claimed with a conditional update, so two calls with the same code can't both win
    if row is None or not AuthorizeCode.objects.filter(pk=row.pk, used_at__isnull=True).update(used_at=now):
        raise InvalidCode()
    key, secret = KEY_PREFIX + tokens.token_urlsafe(32), tokens.token_urlsafe(32)
    connection = SwitchboardConnection.objects.create(
        name=(str(name or "").strip() or row.name or "Switchboard")[:120], webhook_url=webhook_url, base_url=base_url[:300],
        key_hash=hash_token(key), secret=secrets.encrypt(secret), connected_by=row.created_by, connected_at=now)
    _changed()
    return connection, key, secret


def authenticate(request):
    """The connection whose key this request carries, or None."""
    scheme, _sep, key = request.headers.get("Authorization", "").partition(" ")
    key = key.strip()
    if scheme.lower() != "bearer" or not key.startswith(KEY_PREFIX):
        return None
    connection = SwitchboardConnection.objects.filter(key_hash=hash_token(key)).first()
    if connection is None:
        return None
    now = timezone.now()
    if connection.last_used_at is None or now - connection.last_used_at > timedelta(minutes=1):  # not a write per call
        SwitchboardConnection.objects.filter(pk=connection.pk).update(last_used_at=now)
    return connection


def disconnect(connection):
    """The key stops working and events stop. The staff cards it issued stay (IssuedCard keeps them by host)."""
    IdempotencyRecord.objects.filter(connection=connection).delete()
    connection.delete()
    _changed()


def _changed():
    from pavilion.realtime import publish
    publish("switchboard")  # the admin's Switchboard page refetches
