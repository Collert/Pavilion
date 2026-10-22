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

Every widget has a **kind**: "visible" (the default; placed in the layout's slots), "popup" (shown over the screen
when its `trigger` fires: order_ready, announcement, menu_change, event_reminder or order_summary) or "audio" (a sound
channel, `channel` "music" or "voice"; its source and level are its params). The admin's screen editor lists them on
its Layout, Pop-ups and Audio tabs. Optional `icon` (24×24 SVG path data) and `color` are how the editor shows it.

Params may also say how the editor shows them: `control` ("slider" for a number), `unit` ("s", "min", "%"),
`step`, `presets` (colours to pick from) and `source` (the value is picked from a list instead: "menus", "dishes",
"audio:music" or "audio:voice", the screen's audio channels of that kind).

The widget's name is its file name. Definitions are read from disk on every call in DEBUG (edit and reload) and
once per process otherwise; they are part of the code, not cross-request state.

A **menu template** is a special kind of widget ("kind": "menu_template", file name menu_<name>.json): a menu-board
layout. Screens don't place it directly; the "menu_board" widget shows a menu with the template picked in the menu's
Display tab (admin), and its `params` are the template's look (colours, fonts, texts), set per menu there too. It also
says how it lays a menu out:

    "orientation": "portrait",   // or "landscape"
    "width": 1080, "height": 1920, // the design canvas, in design pixels
    "groups": 3,                  // group slots per page (a group is a station's dishes, or the combos)
    "per_group": 6,               // items a slot shows at once; more rotate through screens
    "featured": "optional",       // or "none": a featured item per page
    "fonts": "https://fonts.googleapis.com/css2?..."  // optional stylesheet for its fonts
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
PARAM_TYPES = ("string", "text", "url", "image", "int", "number", "bool", "choice", "color", "size", "list")
KINDS = ("visible", "popup", "audio", "menu_template")
TRIGGERS = ("order_ready", "announcement", "menu_change", "event_reminder", "order_summary")
SOURCES = ("menus", "dishes", "audio:music", "audio:voice")


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
    control: str = ""        # how the editor shows it: "slider" for numbers
    unit: str = ""
    step: float | None = None
    presets: tuple = ()      # color: swatches to pick from
    source: str = ""         # the value is picked from a list (see SOURCES)


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
    kind: str = "visible"  # "popup", "audio" or "menu_template"
    layout: dict = field(default_factory=dict)  # menu templates: orientation, width, height, groups, per_group, featured, fonts
    icon: str = ""
    color: str = "#3D4D45"
    trigger: str = ""   # popups
    channel: str = ""   # audio: "music" or "voice"

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
    if spec.get("source", "") and spec["source"] not in SOURCES:
        raise ImproperlyConfigured(f"{where}: param {name!r} source must be one of {', '.join(SOURCES)}")
    return Param(name=name, type=kind, default=spec.get("default"), label=spec.get("label", ""),
                 help=spec.get("help", ""), options=tuple(spec.get("options", ())), item=item,
                 min=spec.get("min"), max=spec.get("max"), control=spec.get("control", ""), unit=spec.get("unit", ""),
                 step=spec.get("step"), presets=tuple(spec.get("presets", ())), source=spec.get("source", ""))


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
    kind = spec.get("kind", "visible")
    if kind == "widget":
        kind = "visible"
    if kind not in KINDS:
        raise ImproperlyConfigured(f"{where}: kind is one of {', '.join(KINDS)}")
    if kind == "popup" and spec.get("trigger") not in TRIGGERS:
        raise ImproperlyConfigured(f"{where}: a popup needs a trigger, one of {', '.join(TRIGGERS)}")
    if kind == "audio" and spec.get("channel") not in ("music", "voice"):
        raise ImproperlyConfigured(f"{where}: an audio widget needs a channel, music or voice")
    layout = {}
    if kind == "menu_template":
        layout = {"orientation": spec.get("orientation", "portrait"), "width": int(spec.get("width", 1080)),
                  "height": int(spec.get("height", 1920)), "groups": int(spec.get("groups", 3)),
                  "per_group": int(spec.get("per_group", 6)), "featured": spec.get("featured", "none"), "fonts": spec.get("fonts", "")}
        if layout["orientation"] not in ("portrait", "landscape") or layout["featured"] not in ("optional", "none") \
                or not 1 <= layout["groups"] <= 8 or layout["per_group"] < 1:
            raise ImproperlyConfigured(f"{where}: check orientation, featured, groups and per_group")
    return Widget(name=name, title=spec["title"], template=spec["template"], description=spec.get("description", ""),
                  params=params, context=spec.get("context", ""), live=tuple(spec.get("live", ())),
                  css=tuple(spec.get("css", ())), js=tuple(spec.get("js", ())), slots=slots, kind=kind, layout=layout,
                  icon=spec.get("icon", ""), color=spec.get("color", "#3D4D45"), trigger=spec.get("trigger", ""),
                  channel=spec.get("channel", ""))


def _read_all():
    return {path.stem: _load(path) for path in sorted(WIDGETS_DIR.glob("*.json"))}


_read_cached = cache(_read_all)


def _everything() -> dict:
    return _read_all() if settings.DEBUG else _read_cached()


def widgets() -> dict:
    """{name: Widget} for every visible widget a screen's layout can place (menu templates aren't placed directly)."""
    return {name: w for name, w in _everything().items() if w.kind == "visible"}


def popups() -> dict:
    """{name: Widget} for every pop-up a screen can have."""
    return {name: w for name, w in _everything().items() if w.kind == "popup"}


def audio() -> dict:
    """{name: Widget} for every kind of audio channel a screen can have."""
    return {name: w for name, w in _everything().items() if w.kind == "audio"}


def any_kind(name):
    """The widget, pop-up or audio channel called `name`, or None."""
    w = _everything().get(name)
    return w if w and w.kind != "menu_template" else None


def get(name):
    """The widget called `name`, or None."""
    return widgets().get(name)


def menu_templates() -> dict:
    """{name: Widget} for every menu template."""
    return {name: w for name, w in _everything().items() if w.kind == "menu_template"}
