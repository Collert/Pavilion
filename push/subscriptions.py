"""
Keeping subscriptions as browsers report them. Pages post the browser's PushSubscription (JSON, as
`subscription.toJSON()` gives it) whenever they find one, so the row follows the browser: its account (a guest's
subscription gets the account once they sign in on that browser; someone else signing in there takes it over), its
language, and a new endpoint when the browser renews it (push/templates/push/sw.js posts the old one with it).
"""
import datetime
import re

from django.utils import timezone
from django.utils.translation import get_language

from .models import PushSubscription

STALE_GUESTS = datetime.timedelta(days=180)


class BadSubscription(ValueError):
    pass


def parse(data) -> dict:
    """{"endpoint", "p256dh", "auth"} from a browser's subscription JSON, or BadSubscription."""
    try:
        endpoint, keys = data["endpoint"], data["keys"]
        p256dh, auth = keys["p256dh"], keys["auth"]
    except (KeyError, TypeError):
        raise BadSubscription("Expected a push subscription")
    if not all(isinstance(v, str) for v in (endpoint, p256dh, auth)) or not endpoint.startswith("https://") \
            or len(endpoint) > 1024 or not 0 < len(p256dh) <= 255 or not 0 < len(auth) <= 255:
        raise BadSubscription("Expected a push subscription")
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def device_name(user_agent: str) -> str:
    """'Chrome on Android' and the like, from a User-Agent header."""
    ua = user_agent or ""
    browser = next((name for pattern, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"SamsungBrowser", "Samsung Internet"), (r"Firefox/|FxiOS", "Firefox"),
        (r"Chrome/|CriOS", "Chrome"), (r"Safari/", "Safari")) if re.search(pattern, ua)), "")
    system = next((name for pattern, name in (
        (r"Android", "Android"), (r"iPhone|iPod", "iPhone"), (r"iPad", "iPad"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "Mac"), (r"CrOS", "ChromeOS"), (r"Linux", "Linux")) if re.search(pattern, ua)), "")
    if browser and system:
        return f"{browser} on {system}"
    return browser or system


def save(request, app, data, replaces="", **wants) -> PushSubscription:
    """
    The row for this browser's subscription (made or brought up to date), with the signed-in person as its user.
    `wants` (order_updates, event_updates): what a new one is for (an existing one keeps its own). `replaces`: the
    endpoint it renews.
    """
    fields = parse(data)
    user = request.user if request.user.is_authenticated else None
    sub = PushSubscription.objects.filter(endpoint=fields["endpoint"]).first()
    if sub is None and replaces:
        sub = PushSubscription.objects.filter(endpoint=replaces, app=app).first()
    if sub is None:
        sub = PushSubscription(app=app, **wants)
    sub.app = app
    sub.endpoint, sub.p256dh, sub.auth = fields["endpoint"], fields["p256dh"], fields["auth"]
    if user is not None:
        sub.user = user
    sub.device = device_name(request.headers.get("User-Agent", ""))[:80]
    sub.language = (get_language() or "")[:10]
    sub.seen_at = timezone.now()
    sub.save()
    # Guests who never came back: their browser likely forgot them long ago
    PushSubscription.objects.filter(user__isnull=True, seen_at__lt=timezone.now() - STALE_GUESTS).delete()
    return sub


def forget(endpoint, user=None) -> int:
    """Drop a subscription the browser no longer has (or the person removed). Someone else's stays."""
    rows = PushSubscription.objects.filter(endpoint=endpoint)
    if user is not None and user.is_authenticated:
        from django.db.models import Q
        rows = rows.filter(Q(user=user) | Q(user__isnull=True))
    else:
        rows = rows.filter(user__isnull=True)
    return rows.delete()[0]
