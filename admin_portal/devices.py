"""
Devices (Settings; no mockup): the computers, tablets and phones trusted to run Pavilion's staff screens
(pos_server.EligibleDevice). Each has a name and a device code (its token). A browser becomes a device on the device
setup page or by opening any Pavilion link with ?device-token=<code> (pos_server/devices.py), which is how kiosks
that wipe their cookies stay set up: their start URL is a kiosk link from here. A new code stops old links working.
"""
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from pos_server.models import EligibleDevice

# What a kiosk link can open: (key, label, url name)
KIOSK_PAGES = [("register", _("Register"), "pos"), ("kds", _("Kitchen display"), "order_marking"),
               ("clock", _("Time clock"), "time_clock"), ("drawer", _("Cash drawer"), "cash_drawer"),
               ("cookery", _("Cookery"), "crafting"), ("apps", _("Apps"), "app_switcher_index")]


def kiosk_links(request, device) -> list[dict]:
    from screens.models import Screen
    links = [{"label": label, "url": request.build_absolute_uri(reverse(name)) + f"?device-token={device.token}"}
             for _key, label, name in KIOSK_PAGES]
    for screen in Screen.objects.order_by("name")[:20]:
        links.append({"label": _("Screen: %(name)s") % {"name": screen.name},
                      "url": request.build_absolute_uri(reverse("screen", args=[screen.slug])) + f"?device-token={device.token}"})
    return links


def page(request, sel=None) -> dict:
    from payments.models import SquareTerminal
    devices = list(EligibleDevice.objects.all())
    terminals = {t.register_id: t for t in SquareTerminal.objects.filter(register__isnull=False, status="PAIRED")}
    current = next((d for d in devices if d.pk == sel), devices[0] if devices else None)
    rows = [{"d": d, "on": d == current, "added": date_format(timezone.localtime(d.created_at), "M j, Y"),
             "terminal": terminals.get(d.pk)} for d in devices]
    return {"rows": rows, "d": current, "links": kiosk_links(request, current) if current else [],
            "setup": request.build_absolute_uri(reverse("device_elig")),
            "terminal": terminals.get(current.pk) if current else None}
