"""
The admin portal (/admin/): the store's back office. Superuser only for now. Pages follow CLAUDE.md: Django
templates, forms that POST and redirect, static/js/live-page.js to keep them current.
"""
import time

from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _

from pos_server.devices import superuser_required

from . import dashboard as dashboard_data
from .models import StoreSettings
from .nav import rail


def page(request, template, active, context=None):
    """Render an admin page with the rail and everything the layout needs."""
    user = request.user
    full_name = user.get_full_name() or user.username
    return render(request, template, {
        "rail": rail(active), "active": active, "store": StoreSettings.load(),
        "me": {"name": full_name, "initial": full_name[:1].upper(), "role": _("Owner") if user.is_superuser else _("Staff")},
        "now_ms": int(time.time() * 1000),
        **(context or {}),
    })


@superuser_required
def home(request):
    data = dashboard_data.dashboard(request.GET.get("range", "today"), request.GET.get("open") == "1")
    now = timezone.localtime()
    greeting = _("Good morning") if now.hour < 12 else _("Good afternoon") if now.hour < 17 else _("Good evening")
    return page(request, "admin_portal/home.html", "home", {
        "d": data, "greeting": greeting, "first_name": request.user.first_name or request.user.username})
