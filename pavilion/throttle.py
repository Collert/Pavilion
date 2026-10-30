"""
Rate limits, so one client can't take the whole app down. Django runs every synchronous view of a worker on one
shared thread under ASGI, so one register tapping a cart into hundreds of quotes once queued in front of the
kitchen display, the music and everything else until they all stalled.

- ThrottleMiddleware, first in MIDDLEWARE: every request (but the live streams and files) counts against its
  browser (the session or device cookie, read raw: no database) and its address. Past settings.THROTTLE_REQUESTS
  per THROTTLE_WINDOW seconds for the browser, or ten times that for the address (one address can be a whole
  café of phones and screens), it gets a 429 until the window ends. It runs on the event loop, so turning a
  flood away never takes the shared thread or the database from anyone else.
- allow(): tighter limits for endpoints a page calls on every change (the register's quote, the store's sheet and
  checkout posts).

Counted in the cache in fixed windows, so the counts hold across workers when the cache is Redis. settings.THROTTLE
turns it all off (the test runner does; tests fire requests faster than anyone could tap).

    ok, wait = throttle.allow("quote", who, limit=10, window=2)
    if not ok: answer 429 and ask the client to come back in `wait` seconds
"""
import logging
import time

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

# the live streams hold one request open; files are cheap; listmonk posts a campaign's messages in bursts (it signs in)
SKIP = ("/events/", "/static/", "/media/", "/push/listmonk")
ADDRESS_FACTOR = 10


def allow(scope: str, who, limit: int, window: int, now=None) -> tuple[bool, int]:
    """Count one call by `who` in `scope`. (True, 0) while under `limit` calls per `window` seconds, else
    (False, seconds until the window ends). The cache calls are quick (memory, or one Redis round trip), so this is
    also called straight from the middleware's event-loop path."""
    if not getattr(settings, "THROTTLE", True):
        return True, 0
    now = time.time() if now is None else now
    slot = int(now // window)
    key = f"throttle:{scope}:{who}:{slot}"
    try:
        cache.add(key, 0, timeout=window + 1)
        try:
            count = cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.set(key, 1, timeout=window + 1)
            count = 1
    except Exception:  # the cache is down or busy: let the request through rather than fail it
        logger.warning("Throttle cache unavailable", exc_info=True)
        return True, 0
    if count <= limit:
        return True, 0
    return False, max(1, int((slot + 1) * window - now + 0.999))


def client_key(request) -> str:
    """Who's calling, from what's on the request without asking the database: the browser's session cookie (so a
    register and a kitchen screen signed in as the same person count apart), else the registered device's cookie,
    else the address."""
    session = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
    if session:
        return "s" + session[:64]
    from pos_server.devices import DEVICE_COOKIE
    device = request.COOKIES.get(DEVICE_COOKIE)
    if device:
        return "d" + device[:64]
    return "ip" + address(request)


def address(request) -> str:
    return request.META.get("REMOTE_ADDR", "")


def too_many(request, wait: int):
    """The 429: JSON for the apps' fetches, a short page otherwise."""
    message = _("Too many requests. Wait a few seconds and try again.")
    if "application/json" in request.headers.get("Accept", "") or request.content_type == "application/json":
        response = JsonResponse({"message": message, "retryAfter": wait}, status=429)
    else:
        response = HttpResponse(f"<!doctype html><meta charset=utf-8><title>429</title><p>{message}</p>", status=429)
    response["Retry-After"] = str(wait)
    return response


def check(request):
    """The 429 for a client over its limits, or None."""
    if request.path_info.startswith(SKIP):
        return None
    limit, window = settings.THROTTLE_REQUESTS, settings.THROTTLE_WINDOW
    who = client_key(request)
    ok, wait = allow("all", who, limit * (ADDRESS_FACTOR if who.startswith("ip") else 1), window)
    if ok and not who.startswith("ip"):  # a new cookie every time still counts against the address
        ok, wait = allow("addr", address(request), limit * ADDRESS_FACTOR, window)
    return None if ok else too_many(request, wait)


class ThrottleMiddleware:
    """A per-client ceiling on every request. Async under ASGI, so a flood is turned away on the event loop."""
    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self._acall(request)
        return check(request) or self.get_response(request)

    async def _acall(self, request):
        return check(request) or await self.get_response(request)
