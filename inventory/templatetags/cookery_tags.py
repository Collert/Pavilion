"""Formatting for the Cookery templates: amounts in friendly units, durations, countdowns, and the cooking scenes."""
from django import template
from django.utils.translation import gettext as _

register = template.Library()

# Stored unit -> (unit people count in, how many stored units make one of it)
DISPLAY = {"ea": ("pcs", 1), "g": ("kg", 1000), "ml": ("L", 1000)}


def trim(n) -> str:
    """12.0 -> "12", 0.125 -> "0.13"."""
    return f"{round(float(n or 0), 2):.2f}".rstrip("0").rstrip(".")


def display_unit(unit):
    label, factor = DISPLAY.get(unit, (unit, 1))
    return (_("pcs") if unit == "ea" else label), factor


@register.filter
def amount(n, unit):
    """An ingredient amount (stored in g, ml or pieces): big grams as kilos, big millilitres as litres."""
    n = float(n or 0)
    if unit == "g" and abs(n) >= 1000:
        return f"{trim(n / 1000)} kg"
    if unit == "ml" and abs(n) >= 1000:
        return f"{trim(n / 1000)} L"
    if unit == "ea":
        return trim(n)
    return f"{trim(n)} {unit}"


@register.filter
def shown(n, unit):
    """A component amount in the unit people count in: "24 pcs", "2.5 kg", "12 L"."""
    label, factor = display_unit(unit)
    return f"{trim(float(n or 0) / factor)} {label}"


@register.filter
def shown_number(n, unit):
    return trim(float(n or 0) / display_unit(unit)[1])


@register.filter
def shown_unit(unit):
    return display_unit(unit)[0]


@register.filter
def duration(seconds):
    """1500 -> "25 min", 5100 -> "1 h 25 min"."""
    minutes = round(float(seconds or 0) / 60)
    if minutes < 60:
        return _("%(m)s min") % {"m": minutes}
    hours, rest = divmod(minutes, 60)
    return _("%(h)s h %(m)s min") % {"h": hours, "m": rest} if rest else _("%(h)s h") % {"h": hours}


@register.filter
def clock_left(ms):
    """Milliseconds left as a countdown: "4:05" or "1:02:03". (The page keeps it ticking.)"""
    seconds = max(0, -(-int(ms or 0) // 1000))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


SCENE_LABELS = {
    "boil": "Pot on the boil", "simmer": "Pot simmering", "fry": "Pan sizzling", "bake": "Oven baking",
    "rise": "Dough rising", "rest": "Dough resting", "chill": "Chilling", "freeze": "Freezing", "thaw": "Thawing",
    "prep": "Prep work",
}


@register.inclusion_tag("inventory/cookery/scene.html")
def cook_scene(kind, running=True, size=240):
    """The animated kitchen scene for a step or transform (docs/mockups/cookery/Cooking scene-html)."""
    kind = kind if kind in SCENE_LABELS else "prep"
    cold = kind in ("chill", "freeze", "thaw")
    return {
        "kind": kind, "run": bool(running), "w": size, "h": round(size * 0.75), "label": _(SCENE_LABELS[kind]),
        "bg": "#E3F0F4" if cold else "#FDEBD0" if kind in ("bake", "fry") else "#DCEBE2",
        "pot": kind in ("boil", "simmer"), "liquid": "#C98A4B" if kind == "simmer" else "#CFE6EE",
        "bubble": "2.2s" if kind == "simmer" else "1.1s", "cold": cold,
    }


ICONS = {
    "back": "M15 6l-6 6 6 6", "next": "M5 12h14M13 6l6 6-6 6", "play": "M8 5.5v13l10-6.5z", "pause": "M9 6v12 M15 6v12",
    "reset": "M4 12a8 8 0 1 0 2.3-5.6 M4 4v4h4", "plus": "M12 5v14M5 12h14", "minus": "M5 12h14", "check": "m5 12.5 4.5 4.5L19 7.5",
    "recipe": "M6 3h10l3 3v15H6z M9 9h7 M9 13h7 M9 17h4", "hand": "M12 3v9 M8 7v6 M16 7v6 M5 11v3a7 7 0 0 0 14 0v-3",
    "clock": "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 7.5V12l3 2", "steps": "M4 6h16 M4 12h16 M4 18h10",
    "yield": "M4 20h16 M7 20V10 M12 20V5 M17 20v-7", "warn": "M12 7v6 M12 17v.01", "alert": "M12 3.5 2.5 20h19z M12 10v4.5 M12 17.4v.1",
    "search": "M11 4.5a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13z m9 15.5-4-4", "chevron": "m9 6 6 6-6 6", "big": "m5 12 5 5 9-10",
    "backspace": "M9 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-6-6 6-6z m4 4 4 4M17 10l-4 4",
    "make": "M4 10h16v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z M2 10h20 M9 6.5c0-1.5 1-2 1-3.5 M14 6.5c0-1.5 1-2 1-3.5",
    "swap": "M4 8h13l-3-3 M20 16H7l3 3",
}


@register.simple_tag
def icon(name, size=20, width=2.4):
    """A line icon from ICONS (constant markup)."""
    from django.utils.html import format_html
    return format_html('<svg aria-hidden="true" width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                       'stroke-width="{}" stroke-linecap="round" stroke-linejoin="round"><path d="{}"></path></svg>',
                       size, size, width, ICONS[name])
