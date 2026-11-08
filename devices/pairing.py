"""
Pairing visual dongles, their tokens, what they're told to show, and talking to their open WebSockets.

- An admin makes a code on the Devices page (new_code); the dongle's owner types it on their phone during the
  dongle's setup, and the dongle sends it here (views.pair → pair()). A good code makes a Dongle and gives it a
  token, "<id>.<secret>", kept only as a hash (token_hash): the dongle shows it on every WebSocket connection.
- Pavilion talks to a dongle by publishing on the broker topic "dongle:<id>" (send()); its socket (socket.py), in
  whichever worker holds it, passes the message on. A {"type": "config"} message makes the socket send the dongle
  what to show, freshly read from the database (config()).
- The dongle's browser opens its screen through a launch link (launch_path → views.open_screen), which leaves a
  signed cookie: screens.views lets that browser see that dongle's screen (can_see). A dongle can show any web
  address instead (Dongle.url, clean_url): the config then carries that address as it is.
"""
import hashlib
import hmac
import json
import logging
import secrets
from urllib.parse import urlsplit

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from pavilion.realtime import get_broker, publish

from .models import Dongle, DonglePairingCode

logger = logging.getLogger(__name__)

COOKIE = "pavilion_dongle"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
ONLINE_SECONDS = 90  # the dongle says how it is every 30 s; three missed and it's offline
LAUNCH_SALT = "dongles.open"


class PairError(Exception):
    """kind: bad_code | expired | used | bad_request; message: for the TV and the phone."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind, self.message = kind, message


def topic(dongle_id) -> str:
    return f"dongle:{dongle_id}"


def online_key(dongle_id) -> str:
    return f"dongles:online:{dongle_id}"


def is_online(dongle_id) -> bool:
    return bool(cache.get(online_key(dongle_id)))


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# ---------- codes and pairing ----------

def new_code(user=None) -> DonglePairingCode:
    """A fresh six-digit code that no other live code has."""
    now = timezone.now()
    live = set(DonglePairingCode.objects.filter(used_at__isnull=True, expires_at__gt=now).values_list("code", flat=True))
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in live:
            return DonglePairingCode.objects.create(code=code, created_by=user if user and user.is_authenticated else None,
                                                    created_at=now)


def _text(value, limit) -> str:
    return str(value or "").strip()[:limit]


def clean_info(body: dict) -> dict:
    """What a dongle says about itself, kept to known fields and sizes (it's shown on the Devices page)."""
    network = body.get("network") if isinstance(body.get("network"), dict) else {}
    try:
        signal = max(0, min(100, int(network.get("signal") or 0)))
    except (TypeError, ValueError):
        signal = 0
    info = {"model": _text(body.get("model"), 80), "version": _text(body.get("version"), 40),
            "resolution": _text(body.get("resolution"), 20),
            "network": {"ssid": _text(network.get("ssid"), 64), "signal": signal, "ip": _text(network.get("ip"), 45),
                        "wired": bool(network.get("wired"))}}
    if body.get("showing"):
        info["showing"] = _text(body.get("showing"), 20)
    for key in ("uptime", "browser_restarts"):
        try:
            info[key] = max(0, int(body.get(key) or 0))
        except (TypeError, ValueError):
            pass
    return info


def pair(code: str, body: dict) -> tuple[Dongle, str]:
    """Use a code: a new Dongle and its token, or PairError."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    hardware_id = _text(body.get("hardware_id"), 16).upper()
    if len(code) != 6 or not hardware_id:
        raise PairError("bad_request", _("The dongle sent an incomplete request."))
    with transaction.atomic():
        found = DonglePairingCode.objects.select_for_update().filter(code=code).order_by("-created_at").first()
        if found is None:
            raise PairError("bad_code", _("Pavilion doesn’t know that code."))
        if found.used_at is not None:
            raise PairError("used", _("That code was used already."))
        if found.expires_at <= timezone.now():
            raise PairError("expired", _("That code has expired."))
        secret = secrets.token_urlsafe(32)
        dongle = Dongle.objects.create(name=_("New visual dongle"), hardware_id=hardware_id,
                                       token_hash=hash_secret(secret), info=clean_info(body),
                                       paired_by=found.created_by, last_seen=timezone.now())
        found.used_at, found.dongle = timezone.now(), dongle
        found.save(update_fields=["used_at", "dongle"])
        publish("dongles")
    logger.info("Dongle %s paired as #%s", hardware_id, dongle.pk)
    return dongle, f"{dongle.pk}.{secret}"


def check_token(token: str):
    """The Dongle a token belongs to, or None."""
    ident, _dot, secret = str(token or "").partition(".")
    if not ident.isdigit() or not secret:
        return None
    dongle = Dongle.objects.select_related("screen").filter(pk=int(ident)).first()
    if dongle and hmac.compare_digest(dongle.token_hash, hash_secret(secret)):
        return dongle
    return None


# ---------- what to show ----------

def launch_path(dongle) -> str:
    """Where the dongle's browser opens its screen: signed with the dongle, its token (a re-paired dongle's old links
    stop working) and the screen (so a new screen is a new address, and the dongle opens it)."""
    value = signing.Signer(salt=LAUNCH_SALT).sign_object({"d": dongle.pk, "k": dongle.token_hash[:12], "s": dongle.screen_id})
    return reverse("dongle_open", args=[value])


def read_launch(value):
    """The Dongle a launch link is for, or None."""
    try:
        data = signing.Signer(salt=LAUNCH_SALT).unsign_object(value)
    except signing.BadSignature:
        return None
    dongle = Dongle.objects.select_related("screen").filter(pk=data.get("d")).first()
    if dongle is None or dongle.token_hash[:12] != data.get("k"):
        return None
    return dongle


def clean_url(value) -> str | None:
    """A web address a dongle may show: a full http(s) URL, or a path on this Pavilion ("/store/"), which the agent
    joins to the address it paired with. "" for none, None when it isn't one."""
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 2000 or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    if value.startswith("/"):
        return None if value.startswith("//") or "\\" in value else value
    if "://" not in value and "." in value.split("/")[0]:
        value = "https://" + value  # "example.com/menu"
    try:
        URLValidator(schemes=["http", "https"])(value)
    except ValidationError:
        return None
    return value


def url_label(url: str) -> str:
    """How a web address is named on the Devices page and the TV: its site ("example.com"), or the path."""
    if url.startswith("/"):
        return url
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


def config(dongle) -> dict:
    """The config message: the dongle's name, the store's, what to show (a launch path on this Pavilion for a screen,
    or the dongle's own web address, which wins) and how to turn it."""
    from admin_portal.models import StoreSettings
    screen = None
    if dongle.url:
        screen = {"name": url_label(dongle.url), "url": dongle.url}
    elif dongle.screen_id:
        screen = {"name": dongle.screen.name, "url": launch_path(dongle)}
    return {"type": "config", "name": dongle.name, "store": StoreSettings.load().name, "screen": screen,
            "orientation": dongle.orientation}


# ---------- the browser's cookie ----------

def set_cookie(response, dongle, secure):
    response.set_signed_cookie(COOKIE, f"{dongle.pk}:{dongle.token_hash[:12]}", salt=COOKIE, max_age=COOKIE_MAX_AGE,
                               httponly=True, samesite="Lax", secure=secure)
    return response


def from_cookie(request):
    """The Dongle whose browser this is, or None."""
    value = request.get_signed_cookie(COOKIE, default=None, salt=COOKIE, max_age=COOKIE_MAX_AGE)
    if not value:
        return None
    ident, _colon, key = value.partition(":")
    if not ident.isdigit():
        return None
    dongle = Dongle.objects.filter(pk=int(ident)).first()
    return dongle if dongle and dongle.token_hash[:12] == key else None


def can_see(request, screen) -> bool:
    """This browser is a dongle showing that screen."""
    if request.COOKIES.get(COOKIE) is None:
        return False
    dongle = from_cookie(request)
    return bool(dongle and dongle.screen_id == screen.pk)


# ---------- talking to the dongle ----------

def send(dongle_id, message: dict):
    """Pass a message to the dongle's WebSocket, wherever it's open (after the transaction commits). Not delivered
    when it's offline: commands then do nothing, and config is sent on its next connection anyway."""
    def go():
        try:
            get_broker().publish(topic(dongle_id), json.dumps(message))
        except Exception:
            logger.exception("Sending to dongle %s failed", dongle_id)
    transaction.on_commit(go)


def send_config(dongle):
    send(dongle.pk, {"type": "config"})  # the socket reads it fresh from the database


def command(dongle, name: str, **params) -> bool:
    """Send a command (identify, restart, reload, change_wifi). False when the dongle is offline."""
    if not dongle.online:
        return False
    send(dongle.pk, {"type": "command", "command": name, "id": secrets.token_hex(6), **params})
    return True
