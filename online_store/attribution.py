"""
Order attribution: where an order came from. A link to the store can carry a code (/store/?attr=spring-flyer, any
store page; old addresses keep it through their redirect). The code is kept in the visitor's session (the newest
one wins) and saved on the orders they place: Order.attribution and CateringOrder.attribution. The admin's order
lists filter by it and the day report adds them up (admin_portal/reports.py).
"""
from django.urls import reverse

PARAM = "attr"
SESSION_KEY = "attribution"
MAX_LENGTH = 25  # the model fields' max_length


def clean(value) -> str:
    """The code as it's stored: printable, spaces trimmed, at most MAX_LENGTH characters."""
    return "".join(c for c in str(value or "") if c.isprintable()).strip()[:MAX_LENGTH].strip()


def current(request) -> str:
    """The code this visitor came in with ("" when none)."""
    return request.session.get(SESSION_KEY, "")


class AttributionMiddleware:
    """Remembers ?attr=<code> from any store page. After SessionMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.prefix = None

    def __call__(self, request):
        if request.method == "GET" and PARAM in request.GET:
            if self.prefix is None:
                self.prefix = reverse("store_home")
            code = clean(request.GET.get(PARAM))
            if code and request.path.startswith(self.prefix) and current(request) != code:
                request.session[SESSION_KEY] = code
        return self.get_response(request)
