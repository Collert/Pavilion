"""
What each group lets its members do (design: AdminGroups; edited on the admin's Groups page). A permission is a code:

- an app they can open: `app.register`, `app.marking`... (APPS; checked by pos_server.devices.app_required);
- an action: `act.discount`, `act.refund`, `act.clock`... (ACTIONS; checked where it happens);
- an admin page and what they can do there: `admin.<page>.view`, `.edit` (add and change) or `.delete` (delete
  and archive). The pages are the admin's rail (admin_portal/nav.py), so a new page shows up here by itself;
  PAGE_LEVELS says which levels a page has. Edit and delete need view. Admin pages also need `app.admin`.

Groups keep their codes on users.GroupInfo.perms. Someone in several groups gets them all; superusers (the Owners
group) have every one. A user's codes are worked out once per request (two queries) and kept on the user object.
"""
from django.utils.translation import gettext_lazy as _

# (code, name, what it is, icon path)
APPS = [
    ("app.register", _("Register"), _("Take orders and payments"), "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z"),
    ("app.marking", _("Order marking"), _("Station tickets on the kitchen screens"), "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6 M9 16h3"),
    ("app.drawer", _("Cash drawer"), _("Open, count and close the till"), "M3 6h18v12H3z M12 9.4a2.6 2.6 0 1 0 0 5.2a2.6 2.6 0 1 0 0-5.2z"),
    ("app.cookery", _("Cookery"), _("Batch cooking and transforms"), "M3 11h13a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z M16 12h5"),
    ("app.availability", _("Availability"), _("Turn items off or sell past zero"), "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M8 12h8 M12 8v8"),
    ("app.courier", _("Deliveries"), _("The courier app on their phone"), "M5 3h14v18H5z M10 18h4"),
    ("app.screens", _("Screens"), _("Open screens and pair displays"), "M4 5h16v11H4z M9 20h6 M12 16v4"),
    ("app.audio", _("Audio"), _("Play music and talk on the speakers"), "M4 10v4h4l5 4V6L8 10z M16 9a4 4 0 0 1 0 6"),
    ("app.admin", _("Admin"), _("This website. Pages below say what they see"), "M4 13h6V4H4z M14 20h6v-9h-6z M14 4h6v4h-6z M4 20h6v-3H4z"),
]
ACTIONS = [
    ("act.discount", _("Give discounts"), _("Type in a discount on the register"), "M19 5 5 19 M7 7h.01 M17 17h.01"),
    ("act.cash", _("Pay cash in and out"), _("Take cash from or add it to the drawer"), "M3 8h18v12H3z M3 12h18 M10 16h4"),
    ("act.reject", _("Reject orders"), _("On order marking; the payment is refunded"), "M6 6l12 12M18 6 6 18"),
    ("act.refund", _("Issue refunds"), _("Refund paid orders in the admin"), "M4 10h11a5 5 0 0 1 0 10h-3 M8 6l-4 4 4 4"),
    ("act.clock", _("Approve clock-ins"), _("Early or off the schedule, at the time clock"), "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 7v5l3 2"),
]
LEVELS = [("view", _("View"), _("See the page")), ("edit", _("Edit"), _("Add and change")),
          ("delete", _("Delete"), _("Delete and archive"))]
# Pages without every level (the rest have view, edit and delete)
PAGE_LEVELS = {"home": ("view",), "reports": ("view",), "retail": ("view", "edit"), "catering": ("view", "edit"),
               "branding": ("view", "edit"), "hours": ("view", "edit"), "requests": ("view", "edit"),
               "giftcards": ("view", "edit")}


def page_levels(page) -> tuple:
    return PAGE_LEVELS.get(page, ("view", "edit", "delete"))


def sections() -> list[dict]:
    """The admin pages by rail group: [{id, name, icon, pages: [{id, name, levels}]}]. Only built pages."""
    from admin_portal.nav import MENU
    out = []
    for group_id, label, path, pages in MENU:
        built = [{"id": p, "name": name, "levels": page_levels(p)} for p, name, url_name in pages if url_name]
        if built:
            out.append({"id": group_id, "name": label, "icon": path, "pages": built})
    return out


def page_codes(page) -> list[str]:
    return [f"admin.{page}.{level}" for level in page_levels(page)]


def all_codes() -> list[str]:
    return ([c for c, *_rest in APPS] + [c for c, *_rest in ACTIONS]
            + [code for s in sections() for p in s["pages"] for code in page_codes(p["id"])])


def tidy(codes) -> list[str]:
    """Known codes only, in catalogue order, with view added wherever edit or delete is."""
    codes = set(codes)
    for code in list(codes):
        if code.startswith("admin.") and not code.endswith(".view"):
            codes.add(code.rsplit(".", 1)[0] + ".view")
    return [c for c in all_codes() if c in codes]


def perms(user) -> frozenset:
    """Every code this user has (cached on the user for the request)."""
    if not user or not user.is_authenticated or not user.is_active:
        return frozenset()
    cached = getattr(user, "_pavilion_perms", None)
    if cached is None:
        if user.is_superuser:
            cached = frozenset(all_codes())
        else:
            from .models import GroupInfo
            cached = frozenset(c for row in GroupInfo.objects.filter(group__user=user).values_list("perms", flat=True) for c in row or [])
        user._pavilion_perms = cached
    return cached


def can(user, code) -> bool:
    """Whether a staff member may do this (superusers may do anything)."""
    if not user or not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    return user.is_superuser or code in perms(user)


def can_page(user, page, level="view") -> bool:
    return level in page_levels(page) and can(user, "app.admin") and can(user, f"admin.{page}.{level}")


class Can:
    """`{% if can.app_register %}` in templates (users.context_processors.access): a code with _ for ."""
    def __init__(self, user):
        self.user = user

    def __getitem__(self, key):
        return can(self.user, key.replace("_", ".", 1))

    def __contains__(self, key):
        return True
