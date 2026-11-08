"""
Devices (Settings; the canvas's AdminDevices artboard): two groups on the left, each item opening on the right.

- Computers: the computers, tablets and phones trusted to run Pavilion's staff screens (pos_server.EligibleDevice).
  Each has a name and a device code (its token). A browser becomes a device on the device setup page or by opening
  any Pavilion link with ?device-token=<code> (pos_server/devices.py), which is how kiosks that wipe their cookies
  stay set up: their start URL is a kiosk link from here. A new code stops old links working.
- Visual dongles (devices.Dongle): TVs showing a screen. "Add visual dongle" makes a pairing code and waits for a
  dongle to use it (the page refreshes on the "dongles" topic; it arrives as "New visual dongle"), then picks its screen. Each dongle's
  screen, name and orientation, its live state (online while its WebSocket is open), and commands: show its name on
  the TV, restart, change Wi-Fi, unpair.
"""
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, time_format
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


PAIR_KEY = "admin.devices.pairing"  # session: the pairing code the "Add visual dongle" sheet shows
UNPAIR_KEY = "admin.devices.unpair"  # session: the dongle whose "Unpair?" question is open


def _when(moment) -> str:
    """"2:14 PM" today, "Oct 28, 2:14 PM" before."""
    if not moment:
        return ""
    local = timezone.localtime(moment)
    if local.date() == timezone.localdate():
        return time_format(local, "TIME_FORMAT")
    return date_format(local, "M j") + ", " + time_format(local, "TIME_FORMAT")


def dongle_line(d) -> tuple[str, str]:
    """(what the list and the card say about a dongle, its tone: bad, warn or muted)."""
    if not d.online:
        return _("Offline since %(when)s") % {"when": _when(d.last_seen)} if d.last_seen else _("Offline"), "bad"
    if d.showing:
        return _("Showing %(screen)s") % {"screen": d.showing}, "muted"
    return _("No screen assigned"), "warn"


def dongle_detail(d) -> dict:
    info = d.info or {}
    net = info.get("network") or {}
    signal = net.get("signal") or 0
    word = _("Strong") if signal >= 70 else _("Good") if signal >= 50 else _("Fair") if signal >= 30 else _("Weak")
    wifi = _("Ethernet cable") if net.get("wired") else (f"{net['ssid']} · {word}" if net.get("ssid") else "—")
    portrait = d.orientation != d.LANDSCAPE
    resolution = (info.get("resolution") or "").replace("x", "×")
    online = d.online
    if not online:
        line = _("Hasn’t checked in since %(when)s, when it was showing %(screen)s.") % {
            "when": _when(d.last_seen) or _("it was paired"), "screen": d.showing or _("nothing")}
    elif d.showing:
        line = (_("Showing %(screen)s, turned to portrait.") if portrait else _("Showing %(screen)s.")) % {"screen": d.showing}
    else:
        line = _("Paired, but nothing to show yet. The TV says “No screen assigned”.")
    paired = date_format(timezone.localtime(d.paired_at), "M j")
    if d.paired_by:
        paired = _("%(when)s by %(who)s") % {"when": paired, "who": d.paired_by.get_full_name() or d.paired_by.username}
    return {
        "online": online, "portrait": portrait, "line": line,
        "tag": (_("Live · %(res)s") % {"res": resolution} if resolution else _("Live")) if online else _("Offline"),
        "facts": [(_("Wi-Fi"), wifi), (_("IP address"), net.get("ip") or "—"),
                  (_("TV"), (resolution or "—") + (" · " + _("portrait") if portrait else "")),
                  (_("Last seen"), _("Just now") if online else (_when(d.last_seen) or "—")),
                  (_("Software"), ("v" + info["version"].lstrip("v")) if info.get("version") else "—"),
                  (_("Paired"), paired)],
    }


def pairing_sheet(request) -> dict | None:
    """The "Add visual dongle" sheet: its code while it waits, or the dongle that used it."""
    from devices.models import DonglePairingCode
    code = DonglePairingCode.objects.select_related("dongle").filter(pk=request.session.get(PAIR_KEY) or 0).first()
    if code is None:
        return None
    sheet = {"code": code, "digits": list(code.code), "expires_ms": int(code.expires_at.timestamp() * 1000),
             "expired": not code.live and code.dongle_id is None,
             "address": request.get_host(), "dongle": code.dongle}
    if code.dongle_id:
        info = code.dongle.info or {}
        net = info.get("network") or {}
        bits = [_("Ethernet") if net.get("wired") else (_("On %(ssid)s") % {"ssid": net["ssid"]} if net.get("ssid") else ""),
                net.get("ip") or "", (_("%(res)s TV") % {"res": info["resolution"].replace("x", "×")}) if info.get("resolution") else ""]
        sheet["facts"] = " · ".join(b for b in bits if b)
    return sheet


def page(request, sel=None, dongle_sel=None) -> dict:
    from devices.models import Dongle
    from payments.models import SquareTerminal
    from screens.models import Screen
    devices = list(EligibleDevice.objects.all())
    dongles = list(Dongle.objects.select_related("screen", "paired_by"))
    terminals = {t.register_id: t for t in SquareTerminal.objects.filter(register__isnull=False, status="PAIRED")}
    dongle = next((d for d in dongles if d.pk == dongle_sel), None)
    current = None if dongle else next((d for d in devices if d.pk == sel), devices[0] if devices else None)
    if current is None and dongle is None and dongles:
        dongle = dongles[0]
    rows = [{"d": d, "on": d == current, "added": date_format(timezone.localtime(d.created_at), "M j, Y"),
             "terminal": terminals.get(d.pk)} for d in devices]
    dongle_rows = []
    for d in dongles:
        text, tone = dongle_line(d)
        dongle_rows.append({"d": d, "on": d == dongle, "text": text, "tone": tone, "online": d.online})
    unpair = request.session.get(UNPAIR_KEY)
    return {"rows": rows, "d": current, "links": kiosk_links(request, current) if current else [],
            "setup": request.build_absolute_uri(reverse("device_elig")),
            "terminal": terminals.get(current.pk) if current else None,
            "dongle_rows": dongle_rows, "g": dongle, "gx": dongle_detail(dongle) if dongle else None,
            "screens": list(Screen.objects.order_by("name").only("id", "name")),
            "orientations": Dongle.ORIENTATIONS,
            "pairing": pairing_sheet(request),
            "unpair": dongle if dongle and unpair == dongle.pk else None,
            "attention": sum(1 for r in dongle_rows if r["tone"] != "muted")}
