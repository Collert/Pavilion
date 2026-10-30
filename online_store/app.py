"""
The installable store app: everything under /store/ (its scope), named, coloured and drawn after the store. The
manifest is written per request from the store's name, logo and live theme (its `app` colours, online_store/themes.py),
and the icons are drawn from the logo (on the theme's background), or Pavilion's cloche (pavilion/brand.py) without one.

Push (push/): the store's service worker (/store/sw.js) shows order and event notifications. Pages post this
browser's subscription when they find one (static/online_store/push.js):
- sync: on every store page while notifications are on, so the row follows the browser (a guest's gets the account
  once they sign in here; its language);
- order: the order page links it to that order (its updates come here). By itself (`auto`) only when the
  subscription still wants order updates; after a tap on "Get notified" always;
- forget: notifications were turned off, or the person removed this device.
"""
import hashlib
import io
import json

from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from pavilion import throttle
from pos_server.models import Order
from push import subscriptions, views as push_views
from push.models import PushSubscription

from . import guest_orders, themes

ICON_SIZES = (192, 512)
APPLE_ICON = 180


def _look(request=None):
    """(store name, logo media name, {app colour: hex}) for the live theme (the draft in the admin's preview)."""
    cur = themes.current(request)
    names = {"theme": "paper", "background": "paper", **cur.theme.app}
    defaults = {"theme": "#FFFFFF", "background": "#FFFFFF"}
    colours = {key: cur.values.get(param) or defaults[key] for key, param in names.items()}
    return cur.store.get("name") or "", cur.store.get("logo") or "", colours


def _version(request) -> str:
    """Changes when the icons would look different, so browsers fetch them again."""
    name, logo, colours = _look(request)
    return hashlib.sha256(json.dumps([name, logo, colours]).encode()).hexdigest()[:10]


def install(request):
    """Get the app: what it is and isn't, the install button where the browser offers one, and Apple's way."""
    from django.core.files.storage import default_storage
    from admin_portal.models import StoreSettings
    from .views import theme_page
    shot = next((s for s in StoreSettings.load().app_screenshots or [] if isinstance(s, dict) and s.get("form") == "narrow" and s.get("name")), None)
    return theme_page(request, "install.html", {"icon": reverse("store_icon", args=[512]) + f"?v={_version(request)}",
                                                "screenshot": default_storage.url(shot["name"]) if shot else "",
                                                "host": request.get_host()})


def _icon_url() -> str:
    """The app's icon, for notifications."""
    return reverse("store_icon", args=[192]) + f"?v={_version(None)}"


def manifest(request):
    name, _logo, colours = _look(request)
    home, v = reverse("store_home"), _version(request)
    icons = [{"src": reverse("store_icon", args=[n]) + f"?v={v}", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any"}
             for n in ICON_SIZES]
    icons.append({"src": reverse("store_icon_maskable") + f"?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"})
    response = JsonResponse({
        "screenshots": _screenshots(),
        "id": home, "name": name or _("Online store"), "short_name": (name or _("Store"))[:12], "start_url": home, "scope": home,
        "display": "standalone", "background_color": colours["background"], "theme_color": colours["theme"],
        "description": _("Order from %(store)s and follow your order.") % {"store": name} if name else "",
        "icons": icons,
    }, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-cache"
    return response


def _screenshots() -> list:
    """The store's screenshots for Chrome's install sheet (online_store/screenshots.py). The first time there are
    none (and a screenshot server is set up), they're taken."""
    from django.core.files.storage import default_storage
    from admin_portal.models import StoreSettings
    from . import screenshots
    shots = [s for s in StoreSettings.load().app_screenshots or [] if isinstance(s, dict) and s.get("name")]
    if not shots and screenshots.configured() and cache.add("store-screenshots:first", True, 3600):
        screenshots.refresh_later()
    labels = {"store_home": _("The store’s home page"), "menu": _("The menu")}
    return [{"src": default_storage.url(s["name"]), "sizes": f"{s['width']}x{s['height']}", "type": "image/png",
             "form_factor": s.get("form", "narrow"), "label": str(labels.get(s.get("page"), ""))} for s in shots]


def _hex(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def _draw(name, logo, colours, size, maskable) -> bytes:
    """The logo on the theme's background, or Pavilion's cloche (pavilion/brand.py) while the store has none. Full bleed
    for maskable icons and Apple's (they round the corners themselves); the plain ones keep the cloche's rounded tile."""
    from django.core.files.storage import default_storage
    from PIL import Image, ImageOps
    from pavilion import brand
    picture = None
    if logo:
        try:
            with default_storage.open(logo, "rb") as f:
                picture = ImageOps.exif_transpose(Image.open(f)).convert("RGBA")
        except Exception:  # missing or not a picture: Pavilion's instead
            picture = None
    if picture is not None:
        canvas = Image.new("RGBA", (size, size), _hex(colours["background"]) + (255,))
        room = int(size * (0.62 if maskable else 0.8))  # a maskable icon's safe zone is the middle 80% circle
        picture.thumbnail((room, room), Image.LANCZOS)
        canvas.alpha_composite(picture, ((size - picture.width) // 2, (size - picture.height) // 2))
        canvas = canvas.convert("RGB")
    else:
        full_bleed = maskable or size == APPLE_ICON
        canvas = brand.draw(size, small=False, rounded=not full_bleed, scale=0.8 if maskable else 1)
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def _icon_response(request, size, maskable=False):
    name, logo, colours = _look(request)
    key = f"store-icon:{_version(request)}:{size}:{int(maskable)}"
    png = cache.get(key)
    if png is None:
        png = _draw(name, logo, colours, size, maskable)
        cache.set(key, png, 24 * 3600)
    response = HttpResponse(png, content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def icon(request, size):
    if size not in ICON_SIZES + (APPLE_ICON,):
        return HttpResponseBadRequest("No such size")
    return _icon_response(request, size)


def icon_maskable(request):
    return _icon_response(request, 512, maskable=True)


def service_worker(request):
    name, _logo, _colours = _look(request)
    return push_views.service_worker(request, app=PushSubscription.STORE, name=name, scope=reverse("store_home"),
                                     renew=reverse("store_push_renew"), icon=_icon_url())


@csrf_exempt  # a service worker posts it; push.views.renew trusts only the old endpoint
def renew(request):
    return push_views.renew(request, PushSubscription.STORE)


# ---------- this browser's subscription ----------

def _body(request):
    if request.method != "POST":
        return None, HttpResponseNotAllowed(["POST"])
    ok, wait = throttle.allow("store-push", throttle.client_key(request), 30, 60)
    if not ok:
        return None, JsonResponse({"error": "Too many requests", "retryAfter": wait}, status=429)
    try:
        return json.loads(request.body), None
    except ValueError:
        return None, HttpResponseBadRequest("Expected JSON")


def _state(sub) -> dict:
    return {"saved": True, "orders": sub.order_updates, "events": sub.event_updates}


def push_sync(request):
    """POST {subscription}: this browser has notifications on for the store. Keeps its row up to date."""
    body, error = _body(request)
    if error:
        return error
    try:
        sub = subscriptions.save(request, PushSubscription.STORE, body.get("subscription"))
    except subscriptions.BadSubscription as e:
        return HttpResponseBadRequest(str(e))
    return JsonResponse(_state(sub))


def push_order(request, id):
    """POST {subscription, auto}: send this order's updates to this browser (only for whoever may see the order)."""
    body, error = _body(request)
    if error:
        return error
    order = Order.objects.filter(pk=id).only("id", "customer_id").first()
    if order is None or not guest_orders.can_see(request, order.id, order.customer_id):
        return JsonResponse({"error": _("This order isn’t yours.")}, status=403)
    try:
        sub = subscriptions.save(request, PushSubscription.STORE, body.get("subscription"))
    except subscriptions.BadSubscription as e:
        return HttpResponseBadRequest(str(e))
    if not sub.order_updates:
        if body.get("auto"):
            return JsonResponse({**_state(sub), "linked": False})  # they turned order updates off for this device
        sub.order_updates = True  # they asked again: order updates are back on
        sub.save(update_fields=["order_updates"])
    sub.orders.add(order)
    return JsonResponse({**_state(sub), "linked": True})


def push_forget(request):
    """POST {endpoint}: this browser no longer has its subscription (notifications turned off, or it was removed)."""
    body, error = _body(request)
    if error:
        return error
    subscriptions.forget(str(body.get("endpoint") or ""), request.user)
    return JsonResponse({"saved": False})
