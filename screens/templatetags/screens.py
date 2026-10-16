"""Filters for widget templates. Sizes are design pixels; `u` turns them into CSS that scales with the display."""
from django import template

from ..layout import SIZE_RE, _is_number

register = template.Library()


@register.filter
def u(value):
    """Design pixels → CSS length: {{ 24|u }} → calc(var(--u) * 24)."""
    try:
        return f"calc(var(--u) * {float(value):g})"
    except (TypeError, ValueError):
        return "0"


def _track(size):
    if _is_number(size) and size > 0:
        return f"minmax(0, {size:g}fr)"
    if isinstance(size, str) and SIZE_RE.match(size):
        if size.endswith("px"):
            return u(size[:-2])
        if size.endswith("fr"):
            return f"minmax(0, {size})"
        return size
    return "minmax(0, 1fr)"


@register.filter
def tracks(weights, count):
    """A grid-template value for `count` tracks sized by `weights` (missing ones are 1fr): [1, "132px"] → ..."""
    weights = list(weights or [])
    return " ".join(_track(weights[n] if n < len(weights) else 1) for n in range(int(count)))
