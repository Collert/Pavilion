"""
First-time setup: until Pavilion has an account, every page goes to the setup screen (users.views.setup), which asks
about the place (name, phone, address, time zone, tax: StoreSettings) and then about the person setting it up, who
becomes the first owner (a superuser in the Owners group, with a staff profile and an optional register PIN).

Anyone who opens a fresh server first could claim it, so outside development finishing needs the setup code: made
once (in the cache, shared by every worker), written to the server's log when it's made, and shown again by
`manage.py setup_code`. Once an account exists the setup screen is gone and the check costs nothing (a cached flag).
"""
import logging
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)

DONE_KEY = "setup:done"
CODE_KEY = "setup:code"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread
EXEMPT = ("/static/", "/media/", "/events/", "/i18n/", "/square/", "/favicon")


def needed() -> bool:
    """True while there's no account yet. Remembered once there is one, so it's one cache read per request."""
    if cache.get(DONE_KEY):
        return False
    if User.objects.exists():
        cache.set(DONE_KEY, True, timeout=None)
        return False
    return True


def code_required() -> bool:
    return not settings.DEBUG


def setup_code() -> str:
    """The one-time code, made on first use and written to the log so whoever runs the server can find it."""
    code = cache.get(CODE_KEY)
    if code:
        return code
    new = "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2))
    if cache.add(CODE_KEY, new, timeout=None):
        logger.warning("Pavilion setup code: %s (open the site to set it up; `manage.py setup_code` shows it again)", new)
    return cache.get(CODE_KEY) or new


def code_matches(typed) -> bool:
    typed = "".join(ch for ch in (typed or "").upper() if ch.isalnum())
    return secrets.compare_digest(typed, setup_code().replace("-", ""))


def finished():
    cache.set(DONE_KEY, True, timeout=None)
    cache.delete(CODE_KEY)


class SetupMiddleware:
    """Sends every page to the setup screen while Pavilion has no account (not files, live streams or webhooks)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "SETUP_REDIRECT", True) and not request.path.startswith(EXEMPT) and needed():
            target = reverse("first_setup")
            if request.path != target and not request.path.endswith("/jsi18n/"):
                return redirect(target)
        return self.get_response(request)
