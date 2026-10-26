"""
Store info (Settings; no mockup, laid out like the Email and Payments pages): what the store is and how it runs,
kept on StoreSettings instead of the server's environment so whoever runs the store can change it. One form with
Save.

    details   name, phone, email, street address, city, map link. The Online store page edits the same details as a
              draft it publishes; saving here updates that draft too, so publishing doesn't put old ones back.
    tax       the sales tax rate, charged on orders after discounts (pos_server/pricing.py, catering, services)
    time zone the store's clock: store hours, schedules and reports. Applied when Pavilion starts (`load_time_zone`,
              from AdminPortalConfig.ready), so saving asks for a restart (languages.restart)
    music     how much space the music library may use (audio/library.py)
    services  the Valhalla and Nominatim servers couriers' travel times come from (deliveries/routes.py; empty: the
              VALHALLA_URL / GEOCODER_URL settings), and the weatherapi.com key for screens, encrypted with a key from
              SECRET_KEY (payments/secrets.py; empty: the WEATHER_API_KEY setting).

Everything else reads these through the functions below, which use StoreSettings.load() (cached).
"""
import re
import sys
import warnings
import zoneinfo
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.utils.translation import gettext as _

from payments import secrets

from .models import StoreSettings

DETAILS = ("name", "phone", "email", "address", "city", "directions")
BRANDING_DETAILS = ("name", "address", "city", "phone", "directions")  # the ones the Online store page drafts too
RUNNING = {"time_zone": settings.TIME_ZONE}


# ---------- what the rest of Pavilion asks ----------

def store() -> StoreSettings:
    return StoreSettings.load()


def tax_rate() -> Decimal:
    """The sales tax as a fraction (0.05 = 5%)."""
    return (store().tax_rate or Decimal("0")) / 100


def full_address() -> str:
    """Street address and city on one line, for maps and travel times ("" when there's no street address)."""
    s = store()
    return ", ".join(part for part in (s.address.strip(), s.city.strip()) if part) if s.address.strip() else ""


def music_limit_bytes() -> int:
    return int((store().music_storage_gb or 0) * 1000 ** 3)


def routing_url() -> str:
    return store().routing_url or settings.VALHALLA_URL


def geocoder_url() -> str:
    return store().geocoder_url or settings.GEOCODER_URL


def weather_key() -> str:
    return secrets.decrypt(store().weather_key) or (settings.WEATHER_API_KEY or "")


def contact_email() -> str:
    return store().email or settings.VAPID_CONTACT_EMAIL


# ---------- time zone ----------

def apply_time_zone(name):
    """Set this process's time zone (at startup, before any request)."""
    if name not in zoneinfo.available_timezones():
        return
    settings.TIME_ZONE = name
    from django.utils import timezone
    timezone.get_default_timezone.cache_clear()  # what Django does when the setting changes
    RUNNING["time_zone"] = name


def load_time_zone():
    """Apply the saved time zone. Skipped by tests, and quietly when the table isn't there yet (before migrate)."""
    if "test" in sys.argv[1:2]:
        return
    from django.db import DatabaseError, connection
    row = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # "accessing the database during app initialization"
            row = StoreSettings.objects.filter(pk=1).values("time_zone").first()
    except DatabaseError:
        pass
    finally:
        connection.close()
    if row:
        apply_time_zone(row["time_zone"])


def zones() -> list:
    """[(region, [(zone, label)])] for the picker, by region."""
    grouped = {}
    for name in sorted(zoneinfo.available_timezones()):
        region, sep, rest = name.partition("/")
        if not sep or region in ("Etc", "SystemV", "US", "Canada", "Mexico", "Brazil", "Chile") or name.startswith("posix/"):
            continue
        grouped.setdefault(region, []).append((name, rest.replace("_", " ").replace("/", " / ")))
    return sorted(grouped.items())


# ---------- the page ----------

def values_of(s: StoreSettings) -> dict:
    return {"name": s.name, "phone": s.phone, "email": s.email, "address": s.address, "city": s.city,
            "directions": s.directions, "tax_rate": _plain(s.tax_rate), "time_zone": s.time_zone,
            "music_storage_gb": _plain(s.music_storage_gb), "routing_url": s.routing_url, "geocoder_url": s.geocoder_url,
            "weather_key": ""}


def _plain(number) -> str:
    """5.000 -> "5", 7.250 -> "7.25"."""
    text = f"{Decimal(number):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def from_post(post) -> dict:
    v = {k: post.get(k, "").strip() for k in DETAILS + ("tax_rate", "time_zone", "music_storage_gb", "routing_url", "geocoder_url")}
    v["weather_key"] = post.get("weather_key", "").strip()
    v["clear"] = [k for k in ("weather_key",) if post.get(f"clear_{k}")]
    return v


def _number(text, low, high, places):
    try:
        n = Decimal(text.replace(",", ".").rstrip("%").strip() or "0")
    except InvalidOperation:
        return None
    if not n.is_finite() or n < low or n > high or n.as_tuple().exponent < -places:
        return None
    return n


def problems(v) -> dict:
    """{field: what's wrong}."""
    out = {}
    if not v["name"]:
        out["name"] = _("The store needs a name.")
    elif len(v["name"]) > 60:
        out["name"] = _("Keep the name under 60 characters.")
    if v["email"]:
        try:
            validate_email(v["email"])
        except ValidationError:
            out["email"] = _("That doesn’t look like an email address.")
    if v["directions"]:
        try:
            URLValidator(schemes=["http", "https"])(v["directions"])
        except ValidationError:
            out["directions"] = _("Paste the whole link, starting with https://")
    for key in ("routing_url", "geocoder_url"):  # servers may be Docker names (http://valhalla:8002): no domain needed
        parts = urlsplit(v[key])
        if v[key] and (parts.scheme not in ("http", "https") or not parts.hostname or " " in v[key] or len(v[key]) > 200):
            out[key] = _("Paste the whole address, starting with https:// (or http:// on your own network)")
    if len(v["phone"]) > 40 or (v["phone"] and not re.fullmatch(r"[0-9+()./ \-x]+", v["phone"])):
        out["phone"] = _("Use digits, spaces and + ( ) - only.")
    for key, limit in (("address", 120), ("city", 120)):
        if len(v[key]) > limit:
            out[key] = _("Keep it under %(n)s characters.") % {"n": limit}
    if _number(v["tax_rate"], 0, 100, 3) is None:
        out["tax_rate"] = _("A percent from 0 to 100, like 5 or 12.5.")
    if v["time_zone"] not in zoneinfo.available_timezones():
        out["time_zone"] = _("Pick a time zone from the list.")
    if _number(v["music_storage_gb"], 0, 100000, 1) is None:
        out["music_storage_gb"] = _("A number of GB, like 10 or 2.5.")
    return out


def save(v):
    s = StoreSettings.load()
    s = StoreSettings.objects.get(pk=s.pk)  # the row itself, not the cached copy
    for k in DETAILS:
        setattr(s, k, v[k])
    s.tax_rate = _number(v["tax_rate"], 0, 100, 3)
    s.music_storage_gb = _number(v["music_storage_gb"], 0, 100000, 1)
    s.time_zone = v["time_zone"]
    s.routing_url, s.geocoder_url = v["routing_url"], v["geocoder_url"]
    if "weather_key" in v["clear"]:
        s.weather_key = ""
    elif v["weather_key"]:
        s.weather_key = secrets.encrypt(v["weather_key"])
    if s.draft:  # keep the Online store page's draft in step, so its Publish doesn't put the old details back
        s.draft = {**s.draft, **{k: v[k] for k in BRANDING_DETAILS if k in s.draft}}
    s.save()
    return s


def restart_pending(s: StoreSettings) -> bool:
    return s.time_zone != RUNNING["time_zone"]


def page(s: StoreSettings, v=None, errors=None) -> dict:
    from audio.library import used_bytes
    used = used_bytes()
    return {
        "v": v or values_of(s), "errors": errors or {}, "zones": zones(),
        "has_weather": bool(s.weather_key), "env_weather": not s.weather_key and bool(settings.WEATHER_API_KEY),
        "env_routing": settings.VALHALLA_URL, "env_geocoder": settings.GEOCODER_URL,
        "public_geocoder": "openstreetmap.org" in ((v or {}).get("geocoder_url") or s.geocoder_url or settings.GEOCODER_URL),
        "music_used_gb": f"{used / 1000 ** 3:.1f}", "restart": restart_pending(s), "running_zone": RUNNING["time_zone"],
    }
