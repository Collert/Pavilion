"""What the Team pages share (designs: AdminStaff, AdminRoles, AdminGroups): role icons, avatar colours, names."""
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

ROLE_ICONS = {
    "pan": (_("Pan"), "M3 11h13a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z M16 12h5 M7 7c0-1 1-1 1-2 M11 7c0-1 1-1 1-2"),
    "register": (_("Register"), "M4 12h16v8H4z M7 12V8h10v4 M9 4h6v4H9z M8 16h2 M14 16h2"),
    "cup": (_("Coffee"), "M5 9h11v5a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z M16 10h1.5a2.5 2.5 0 0 1 0 5H16 M4 21h14"),
    "cloche": (_("Serving"), "M3 17h18 M5 17a7 7 0 0 1 14 0 M12 8V6 M10 6h4"),
    "croissant": (_("Bakery"), "M3 15c2-6 6-9 9-9s7 3 9 9c-2 1.5-4 1.5-5 0-1 2-7 2-8 0-1 1.5-3 1.5-5 0z M9 9l1 6 M15 9l-1 6"),
    "drop": (_("Dishes"), "M12 3c3 4 6 7 6 10a6 6 0 0 1-12 0c0-3 3-6 6-10z"),
    "bike": (_("Delivery"), "M2.5 17.5a3 3 0 1 0 6 0 3 3 0 1 0-6 0z M15.5 17.5a3 3 0 1 0 6 0 3 3 0 1 0-6 0z M5.5 17.5 9 10h6l3.5 7.5 M9 10 7.5 7H5 M13 7h3l2 3"),
    "star": (_("Lead"), "M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z"),
    "knife": (_("Prep"), "M4 20 15 9l2 2-9 9z M15 9l4-4a2 2 0 0 1 2 2l-4 4"),
    "door": (_("Host"), "M6 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16 M3 21h18 M14 12h.01"),
    "spark": (_("Cleaning"), "M12 3v4 M12 17v4 M3 12h4 M17 12h4 M6 6l2.5 2.5 M15.5 15.5 18 18 M6 18l2.5-2.5 M15.5 8.5 18 6"),
    "box": (_("Stock"), "M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z M3 7.5l9 4.5 9-4.5 M12 12v9"),
}
GROUP_ICONS = {  # GroupInfo.icon: the tile on the Groups page
    "people": "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M2.5 20a6.5 6.5 0 0 1 13 0 M16 4.3a3.5 3.5 0 0 1 0 6.4 M18 14.2a6.5 6.5 0 0 1 3.5 5.8",
    "star": ROLE_ICONS["star"][1],
    "shield": "M9 12l2 2 4-4 M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z",
    "register": ROLE_ICONS["register"][1],
    "pan": ROLE_ICONS["pan"][1],
    "bike": ROLE_ICONS["bike"][1],
}
PALETTE = [("#12875A", _("Green")), ("#C9821A", _("Amber")), ("#4E79B5", _("Blue")), ("#2BA3A0", _("Teal")), ("#E87BA4", _("Pink")),
           ("#6B7A2E", _("Olive")), ("#7A5AC8", _("Violet")), ("#C2453B", _("Red")), ("#14261F", _("Ink")), ("#5B6A62", _("Slate"))]
FACES = ["#1F4A3A", "#C9821A", "#4E79B5", "#7A5AC8", "#2BA3A0", "#C2453B", "#6B7A2E", "#E87BA4"]


def face(user_id) -> str:
    return FACES[(user_id or 0) % len(FACES)]


def full_name(first, last, fallback="") -> str:
    return f"{first} {last}".strip() or fallback or _("Unnamed")


def initials(first, last, fallback="?") -> str:
    return ((first[:1] + last[:1]).upper()) or fallback[:2].upper() or "?"


def when(dt) -> str:
    """"Now", "Today, 9:12 AM", "Yesterday", "Oct 18" or "Never"."""
    if not dt:
        return _("Never")
    now = timezone.localtime()
    dt = timezone.localtime(dt)
    if (now - dt).total_seconds() < 120:
        return _("Now")
    if dt.date() == now.date():
        return _("Today, %(time)s") % {"time": date_format(dt, "TIME_FORMAT")}
    if (now.date() - dt.date()).days == 1:
        return _("Yesterday")
    return date_format(dt, "M j" if dt.year == now.year else "M j, Y")
