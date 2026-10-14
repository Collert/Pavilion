import uuid

from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from pos_server.devices import DEVICE_COOKIE, staff_required
from pos_server.models import EligibleDevice

# Tiles on the app switcher: (url name, title, description, icon path, icon background, icon colour, superusers only)
APPS = [
    ("pos", _("Register"), _("Ring up orders and take payment"),
     "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z", "#F4A93B", "#14261F", False),
    ("order_marking", _("Order marking"), _("See station tickets and move them along"),
     "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6 M9 16h3", "#1F4A3A", "#FFFFFF", False),
    ("crafting", _("Cookery"), _("Craft components and run transforms"),
     "M7 14.5h10V20H7z M7 14.5a4 4 0 0 1-1.3-7.8A4.6 4.6 0 0 1 12 4a4.6 4.6 0 0 1 6.3 2.7A4 4 0 0 1 17 14.5 M10 17.5h4",
     "#FDEBD0", "#8A4B00", False),
    ("component_availability", _("Availability"), _("Turn items off or keep selling past zero"),
     "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M8 12h8 M12 8v8", "#DCEBE2", "#1F4A3A", False),
    ("cash_drawer", _("Cash drawer"), _("Open, count and close the till"),
     "M3 6h18v12H3z M12 9.4a2.6 2.6 0 1 0 0 5.2a2.6 2.6 0 1 0 0-5.2z", "#14261F", "#F4A93B", False),
    ("pos_output", _("Customer display"), _("The menu and the order being rung up, for customers"),
     "M3 4h18v12H3z M8 20h8 M12 16v4", "#E6ECE8", "#1F4A3A", False),
    ("order_progress", _("Status board"), _("Orders in progress and ready, for the pickup counter"),
     "M4 5h16 M4 10h16 M4 15h10 M4 20h7", "#FBE9E5", "#8E2914", False),
    ("admin-index", _("Admin"), _("Menus, staff, stations and reports"),
     "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z M12 2v3 M12 19v3 M2 12h3 M19 12h3 M4.9 4.9l2.1 2.1 M17 17l2.1 2.1 M4.9 19.1 7 17 M17 7l2.1-2.1",
     "#F1F4F2", "#14261F", True),
]


def device_name(request):
    """This device's name if it's registered (one indexed lookup), else None."""
    token = request.get_signed_cookie(DEVICE_COOKIE, default=None)
    try:
        token = uuid.UUID(str(token)) if token else None
    except ValueError:
        return None
    return EligibleDevice.objects.filter(token=token).values_list("name", flat=True).first() if token else None


@staff_required
def index(request):
    """
    The app launcher. Deliberately light: besides the signed-in user, the only query is the device's name.
    "Today so far" comes from this browser tab's sessionStorage (pos_server/static/pos_server/device-stats.js).
    """
    user = request.user
    apps = [{"url": reverse(name), "title": title, "desc": desc, "path": path, "bg": bg, "fg": fg}
            for name, title, desc, path, bg, fg, superuser_only in APPS
            if user.is_superuser or not superuser_only]
    return render(request, "app_switcher/index.html", {
        "apps": apps,
        "device_name": device_name(request),
        "first_name": user.first_name or user.username,
    })
