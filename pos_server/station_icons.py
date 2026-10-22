"""The icons and colours a station can have (design: AdminStations). Icons are 24×24 line drawings (SVG path data)."""
from django.utils.translation import gettext_lazy as _

# (id, label, path)
ICONS = [
    ("flame", _("Grill"), "M12 3c1 3 4 4.5 4 8.5A4 4 0 0 1 8 11.5c0-1.6.8-2.8 1.6-3.6.2 1.5 1 2.4 1.9 2.6C11 8 11 5.5 12 3z M5 20h14 M7 17h10"),
    ("basket", _("Fryer"), "M4 9h14l-1.5 8h-11z M18 11h3 M8 9V6 M12 9V5 M16 9V6 M7 13h9"),
    ("cup", _("Bar and drinks"), "M6 8h10v6a4 4 0 0 1-4 4h-2a4 4 0 0 1-4-4z M16 10h2a2 2 0 0 1 0 4h-2 M8 3c0 1.5 1 1.5 1 3 M12 3c0 1.5 1 1.5 1 3"),
    ("croissant", _("Bakery"), "M3 15c2-6 6-9 9-9s7 3 9 9c-2 1.5-4 1.5-5 0-1 2-7 2-8 0-1 1.5-3 1.5-5 0z M9 9l1 6 M15 9l-1 6"),
    ("bell", _("Expo and pass"), "M4 18h16 M6 18a6 6 0 0 1 12 0 M12 9V7 M10 7h4"),
    ("snow", _("Cold prep"), "M12 3v18 M4.5 7.5l15 9 M19.5 7.5l-15 9 M9.5 4.5 12 6l2.5-1.5 M9.5 19.5 12 18l2.5 1.5"),
    ("leaf", _("Salads"), "M5 19C5 10 10 5 19 5c0 9-5 14-14 14z M5 19l8-8"),
    ("pizza", _("Pizza oven"), "M12 3 3 19c6 3 12 3 18 0z M9 12h.01 M14 14h.01 M11 16h.01"),
    ("wok", _("Wok and sauté"), "M3 11h16a8 6 0 0 1-16 0z M19 11l3-2 M9 6c0 1 1 1 1 2 M13 5c0 1 1 1 1 2"),
    ("pot", _("Soups and sauces"), "M5 10h14v6a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z M3 10h18 M9 6c0 1 1 1 1 2 M14 5c0 1 1 1 1 2"),
    ("knife", _("Prep"), "M4 20 15 9l2 2-9 9z M15 9l4-4a2 2 0 0 1 2 2l-4 4"),
    ("fish", _("Seafood"), "M3 12c3-4 7-6 11-6 3 0 5 2 6 6-1 4-3 6-6 6-4 0-8-2-11-6z M3 12l-1-3 M3 12l-1 3 M16 11h.01"),
    ("sandwich", _("Sandwiches"), "M3 10l9-5 9 5z M3 10v2h18v-2 M4 14h16 M4 17h16v2H4z"),
    ("coffee", _("Coffee"), "M5 9h11v5a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z M16 10h1.5a2.5 2.5 0 0 1 0 5H16 M4 21h14"),
    ("ice", _("Desserts"), "M8 10a4 4 0 0 1 8 0z M8 10l4 11 4-11"),
    ("egg", _("Breakfast"), "M12 3c3.5 0 6 5 6 9a6 6 0 0 1-12 0c0-4 2.5-9 6-9z"),
    ("bowl", _("Bowls"), "M3 11h18a9 7 0 0 1-18 0z M8 20h8 M9 7c0-2 2-3 3-3s3 1 3 3"),
    ("star", _("Specials"), "M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z"),
]
CHOICES = [(key, label) for key, label, _path in ICONS]
PATHS = {key: path for key, _label, path in ICONS}
DEFAULT = "knife"

PALETTE = [("#12875A", _("Green")), ("#C9821A", _("Amber")), ("#4E79B5", _("Blue")), ("#E87BA4", _("Pink")), ("#7A5AC8", _("Violet")),
           ("#2BA3A0", _("Teal")), ("#C2453B", _("Red")), ("#6B7A2E", _("Olive")), ("#5B6A62", _("Slate"))]


def path(key) -> str:
    return PATHS.get(key, PATHS[DEFAULT])
