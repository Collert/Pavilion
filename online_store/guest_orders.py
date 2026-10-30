"""
Who may see an order placed in the online store. An order with a customer (placed signed in) is theirs: they see it
on any device they sign in on. A guest's order belongs to the browser that placed it, which keeps its number in a
signed cookie (a year, the last 20), so the order page, the "your order" pill and push sign-ups only answer there.
Signing out doesn't forget them (the session would), and nobody can open someone else's order by guessing its number.
"""
from django.conf import settings
from django.core import signing

COOKIE = "pavilion_orders"
SALT = "online_store.guest_orders"
KEEP = 20
AGE = 365 * 24 * 3600


def ids(request) -> list[int]:
    """The guest orders this browser placed, oldest first."""
    if hasattr(request, "_guest_orders"):
        return request._guest_orders
    try:
        raw = request.get_signed_cookie(COOKIE, default="", salt=SALT, max_age=AGE)
    except signing.BadSignature:
        raw = ""
    out = [int(n) for n in raw.split(",") if n.isdigit()]
    for n in request.session.get("orders", []):  # kept in the session before the cookie
        if isinstance(n, int) and n not in out:
            out.append(n)
    request._guest_orders = out[-KEEP:]
    return request._guest_orders


def remember(request, order):
    """This browser placed `order` as a guest: it may see it from now on (the middleware writes the cookie)."""
    if order.customer_id:
        return
    request._guest_orders = [n for n in ids(request) if n != order.id][-(KEEP - 1):] + [order.id]
    request._guest_orders_changed = True


def can_see(request, order_id, customer_id) -> bool:
    """Whether this visitor may see the order: its customer, or (for a guest's) the browser that placed it."""
    if customer_id:
        return request.user.is_authenticated and request.user.pk == customer_id
    return order_id in ids(request)


class GuestOrdersMiddleware:
    """Writes the cookie after a request that remembered an order."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, "_guest_orders_changed", False):
            response.set_signed_cookie(COOKIE, ",".join(str(n) for n in request._guest_orders), salt=SALT, max_age=AGE,
                                       httponly=True, samesite="Lax", secure=settings.SESSION_COOKIE_SECURE)
            request.session.pop("orders", None)
        return response
