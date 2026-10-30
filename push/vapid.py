"""
Pavilion's VAPID key pair (push.VapidKeys): made once per deployment, so nobody has to generate keys to self-host.
A deployment that already had VAPID_PRIVATE_KEY in its settings keeps that pair (its browsers subscribed with it).
If SECRET_KEY changes the private key can't be read: a new pair is made, and old subscriptions fall away as push
services refuse them (push/send.py).
"""
import base64
import logging

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from py_vapid import Vapid

from payments import secrets

logger = logging.getLogger(__name__)
CACHE_KEY = "push:vapid-public"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pair(vapid) -> tuple[str, str]:
    """(public, private) as base64url: the uncompressed public point and the 32-byte private number."""
    public = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return _b64(public), _b64(private)


def _new_pair() -> tuple[str, str]:
    configured = (getattr(settings, "VAPID_PRIVATE_KEY", "") or "").strip()
    if configured:
        try:
            configured = configured.replace("\\n", "\n")  # a PEM written on one line of a .env
            return _pair(Vapid.from_pem(configured.encode()) if "-----BEGIN" in configured else Vapid.from_string(configured))
        except Exception:
            logger.warning("VAPID_PRIVATE_KEY can't be read; making a new key pair", exc_info=True)
    return _pair(Vapid(private_key=ec.generate_private_key(ec.SECP256R1())))


def _row():
    from .models import VapidKeys
    row = VapidKeys.objects.order_by("id").first()
    if row is not None and secrets.decrypt(row.private_key):
        return row
    public, private = _new_pair()
    try:
        with transaction.atomic():
            if row is not None:  # unreadable (SECRET_KEY changed): replace it
                row.public_key, row.private_key = public, secrets.encrypt(private)
                row.save()
                cache.delete(CACHE_KEY)
                return row
            return VapidKeys.objects.create(id=1, public_key=public, private_key=secrets.encrypt(private))
    except IntegrityError:  # another worker made it first
        return VapidKeys.objects.get(id=1)


def public_key() -> str:
    """What browsers subscribe with (applicationServerKey)."""
    key = cache.get(CACHE_KEY)
    if not key:
        key = _row().public_key
        cache.set(CACHE_KEY, key, 3600)
    return key


def signer() -> Vapid:
    """The private key, ready for pywebpush."""
    row = _row()
    raw = base64.urlsafe_b64decode(secrets.decrypt(row.private_key) + "==")
    key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    return Vapid(private_key=key)
