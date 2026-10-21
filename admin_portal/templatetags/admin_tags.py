"""Template helpers for the admin pages: line icons and money."""
from decimal import Decimal, ROUND_HALF_UP

from django import template
from django.utils.html import format_html

from admin_portal.units import fmt_qty

register = template.Library()

ICONS = {
    "up": "M12 19V5 M6 11l6-6 6 6",
    "down": "M12 5v14 M6 13l6 6 6-6",
    "sales": "M12 3v18 M16.5 7.5c0-1.9-2-3-4.5-3s-4.5 1.2-4.5 3.2c0 4.6 9 2.4 9 7 0 2-2 3.3-4.5 3.3s-4.5-1.2-4.5-3",
    "receipt": "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6",
    "card": "M4 7h16v12H4z M4 11h16 M8 15h3",
    "clock": "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 7v5l3 2",
    "critical": "M12 3 2 20h20z M12 10v4 M12 17h.01",
    "warning": "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 8v5 M12 16h.01",
    "info": "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 11v5 M12 8h.01",
    "chevron": "m9 6 6 6-6 6",
    "chevron-down": "m6 9 6 6 6-6",
    "back": "M15 6l-6 6 6 6",
    "apps": "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
    "logo": "M3 11 12 4l9 7 M6 11v8 M12 11v8 M18 11v8 M4 19h16",
    "check": "m5 12.5 4.5 4.5L19 7.5",
    "plus": "M12 5v14 M5 12h14",
    "close": "M6 6l12 12 M18 6 6 18",
}


@register.simple_tag
def icon(name, size=20, width=2):
    """An inline line icon. `name` is a key of ICONS or a raw SVG path (constant markup from our own code)."""
    return format_html('<svg aria-hidden="true" width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                       'stroke-width="{}" stroke-linecap="round" stroke-linejoin="round"><path d="{}"></path></svg>',
                       size, size, width, ICONS.get(name, name))


def _money(value, places):
    value = Decimal(str(value or 0)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "−" if value < 0 else ""
    return f"{sign}${abs(value):,.{places}f}"


@register.filter
def money(value):
    """$1,234.50"""
    return _money(value, 2)


@register.filter
def money0(value):
    """$1,235"""
    return _money(value, 0)


@register.filter
def qty(value, unit):
    """'2.5 kg' from 2500 g (see admin_portal.units)."""
    return fmt_qty(value, unit)
