"""
The widget catalogue: every `screens/widgets/<name>.json` file describes one pre-made widget.

A definition looks like this (see the "Screens and widgets" section of CLAUDE.md for the full format):

    {
      "title": "Order list",
      "description": "Orders being prepared or ready for pickup.",
      "template": "screens/widgets/order_list.html",
      "context": "screens.providers.order_list",   // optional: fn(request, params) -> dict, given to the template as `data`
      "live": ["orders"],                           // optional: SSE topics that make the screen refetch
      "css": ["screens/widgets/order_list.css"],    // optional static files the screen page loads once
      "js": [],
      "slots": "count",                             // optional: the int param holding how many child widgets it takes
      "params": {"title": {"type": "string", "default": "Preparing", "label": "Heading"}, ...}
    }

The widget's name is its file name. Definitions are read from disk on every call in DEBUG (edit and reload) and
once per process otherwise; they are part of the code, not cross-request state.
"""
import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

WIDGETS_DIR = Path(__file__).resolve().parent / "widgets"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Param types and what a value must look like. "size" is a grid track: a number (fr) or "132px" / "40%" / "auto".
PARAM_TYPES = ("string", "text", "url", "int", "number", "bool", "choice", "color", "size", "list")


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    default: object = None
    label: str = ""
    help: str = ""
    options: tuple = ()      # choice
    item: "Param | None" = None  # list: the type of each item
    min: float | None = None  # int/number: value bounds; list: length bounds
    max: float | None = None


@dataclass(frozen=True)
class Widget:
    name: str
    title: str
    template: str
    description: str = ""
    params: dict = field(default_factory=dict)  # name -> Param
    context: str = ""
    live: tuple = ()
    css: tuple = ()
    js: tuple = ()
    slots: str = ""  # name of the int param giving the number of child slots; "" = takes no children

    def provider(self):
        return import_string(self.context) if self.context else None


def _param(name, spec, where):
    if not isinstance(spec, dict) or spec.get("type") not in PARAM_TYPES:
        raise ImproperlyConfigured(f"{where}: param {name!r} needs a type, one of {', '.join(PARAM_TYPES)}")
    kind = spec["type"]
    item = None
    if kind == "list":
        item = _param(f"{name}[]", spec.get("item"), where)
    if kind == "choice" and not spec.get("options"):
        raise ImproperlyConfigured(f"{where}: choice param {name!r} needs options")
    return Param(name=name, type=kind, default=spec.get("default"), label=spec.get("label", ""),
                 help=spec.get("help", ""), options=tuple(spec.get("options", ())), item=item,
                 min=spec.get("min"), max=spec.get("max"))


def _load(path):
    where = f"screens/widgets/{path.name}"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ImproperlyConfigured(f"{where} isn't valid JSON: {error}") from error
    name = path.stem
    if not NAME_RE.match(name):
        raise ImproperlyConfigured(f"{where}: widget names are lowercase letters, digits and _")
    if not spec.get("title") or not spec.get("template"):
        raise ImproperlyConfigured(f"{where} needs a title and a template")
    params = {key: _param(key, value, where) for key, value in spec.get("params", {}).items()}
    slots = spec.get("slots", "")
    if slots and (slots not in params or params[slots].type != "int"):
        raise ImproperlyConfigured(f"{where}: slots must name an int param")
    return Widget(name=name, title=spec["title"], template=spec["template"], description=spec.get("description", ""),
                  params=params, context=spec.get("context", ""), live=tuple(spec.get("live", ())),
                  css=tuple(spec.get("css", ())), js=tuple(spec.get("js", ())), slots=slots)


def _read_all():
    return {path.stem: _load(path) for path in sorted(WIDGETS_DIR.glob("*.json"))}


_read_cached = cache(_read_all)


def widgets() -> dict:
    """{name: Widget} for every widget definition."""
    return _read_all() if settings.DEBUG else _read_cached()


def get(name):
    """The widget called `name`, or None."""
    return widgets().get(name)
