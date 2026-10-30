"""Line icons for the courier app (from the Courier design)."""
from django import template
from django.utils.html import format_html

register = template.Library()

ICONS = {
    "walking": "M13 4.5a1.5 1.5 0 1 0 0-3a1.5 1.5 0 0 0 0 3z M9 20l2-6 3 3v5 M7 12l2-4 4 1 2 3 3 1 M11 14l-1-5",
    "bicycling": "M2 17.5a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0z M15 17.5a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0z M14 5a1 1 0 1 0 2 0a1 1 0 1 0-2 0z M12 17.5V14l-3-3 4-3 2 3h2",
    "driving": "M5 17h14v-4l-2-5H7l-2 5z M5 13h14 M7.5 17v2 M16.5 17v2 M7.5 15h.01 M16.5 15h.01",
    "pin": "M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z M9.5 9.5a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0",
    "bag": "M5 8h14l-1 12H6z M9 8V6.5a3 3 0 0 1 6 0V8",
    "bag-x": "M5 8h14l-1 12H6z M9 8V6.5a3 3 0 0 1 6 0V8 M10 12.5l4 4 M14 12.5l-4 4",
    "chevron": "m9 6 6 6-6 6", "back": "M15 6l-6 6 6 6",
    "check": "m5 12.5 4.5 4.5L19 7.5", "big-check": "m5 12 5 5 9-10",
    "phone": "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
    "navigate": "M3 11 21 3l-8 18-2-8z",
    "list": "M9 6h11 M9 12h11 M9 18h11 M4.5 6h.01 M4.5 12h.01 M4.5 18h.01",
    "power": "M12 3v8 M6.3 6.3a8 8 0 1 0 11.4 0",
    "camera": "M4 8h3l2-3h6l2 3h3v11H4z M8.5 13a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0",
    "info": "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 11v5 M12 8h.01",
    "warn": "M12 3 2 20h20z M12 10v4 M12 17h.01",
    "spinner": "M12 3a9 9 0 1 0 9 9",
}


@register.simple_tag
def icon(name, size=20, width=2, css=""):
    """An inline line icon (constant markup, decorative)."""
    return format_html('<svg aria-hidden="true"{} width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                       'stroke-width="{}" stroke-linecap="round" stroke-linejoin="round"><path d="{}"></path></svg>',
                       format_html(' class="{}"', css) if css else "", size, size, width, ICONS[name])
