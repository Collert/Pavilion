"""
Online-store themes. Like screen widgets, each theme is self-contained and describes itself in JSON:

    online_store/themes/<name>.json          what it is and the params it exposes
    online_store/templates/themes/<name>/    its pages (home.html, ...) on its own base.html
    online_store/static/themes/<name>/       its CSS (and JS only for what HTML can't do)

The JSON:

    {
      "title": "Neighbourhood", "description": "Warm and editorial...",
      "fonts": "https://fonts.googleapis.com/css2?...",     // stylesheet for its font choices
      "css": ["themes/neighbourhood/theme.css"],
      "thumb": {"background": "paper", "text": "ink", "accent": "accent", "card": "card", "font": "heading_font"},
      "contrast": [["ink", "paper", "Text on the background"], ...],
      "app": {"theme": "paper", "background": "paper"},
      "groups": [{"name": "Colours", "params": {
          "paper": {"type": "color", "default": "#F6F0E4", "label": "Background", "presets": ["#F6F0E4", "#FFFFFF"]},
          ...}}]
    }

Param types: string (one line, `max` = length), text (a paragraph), url, bool (a switch; `help` is its line), int /
number (`min`, `max`, `step`, `control`: "stepper" or "range", `unit`), choice (`options`, `control`: "select",
"segments" or "cards"; `labels` names the options), color (#hex, `presets`), image (an uploaded file, a media name
under branding/). `show_if` names a bool param that has to be on for the field to show.

`app` names the colour params the installed store app uses (online_store/app.py): `theme` its title bar, `background`
its splash screen and what its icon's logo sits on (without a logo the icon is Pavilion's cloche).

Each theme's values live in admin_portal.StoreTheme (live, and a draft the Branding page edits); the store details
every theme shows (name, logo, address...) are on StoreSettings. `current(request)` is what a store page renders
with: the live theme, or in the Branding preview (a superuser's request from an iframe while the Branding page is
open) the draft being edited.
"""
import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage

THEMES_DIR = Path(__file__).resolve().parent / "themes"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TYPES = ("string", "text", "url", "bool", "int", "number", "choice", "color", "image")
CONTROLS = {"int": ("stepper", "range"), "number": ("stepper", "range"), "choice": ("select", "segments", "cards")}
TEXT_LIMITS = {"string": 120, "text": 600, "url": 500}
PREVIEW_KEY = "theme_preview"


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    default: object
    label: str = ""
    help: str = ""
    options: tuple = ()
    labels: tuple = ()
    presets: tuple = ()
    min: float | None = None
    max: float | None = None
    step: float = 1
    unit: str = ""
    control: str = ""
    show_if: str = ""

    def check(self, value):
        """The value as this param's type, or None when it doesn't fit."""
        kind = self.type
        if kind in TEXT_LIMITS:
            if not isinstance(value, str):
                return None
            limit = int(self.max) if kind == "string" and self.max else TEXT_LIMITS[kind]
            value = value.strip()[:limit]
            if kind == "url" and value and not (value.startswith("https://") or (value.startswith("/") and not value.startswith("//"))):
                return None
            return value
        if kind == "bool":
            return value if isinstance(value, bool) else None
        if kind in ("int", "number"):
            try:
                value = int(value) if kind == "int" else float(value)
            except (TypeError, ValueError):
                return None
            if isinstance(value, float) and value == int(value):
                value = int(value)
            if self.min is not None and value < self.min or self.max is not None and value > self.max:
                return None
            return value
        if kind == "choice":
            return value if value in self.options else None
        if kind == "color":
            return value.upper() if isinstance(value, str) and COLOR_RE.match(value) else None
        if kind == "image":
            return value if isinstance(value, str) and (value == "" or value.startswith("branding/")) else None
        return None

    @property
    def choices(self):
        """[(value, label)] for a choice."""
        return list(zip(self.options, self.labels or self.options))


@dataclass(frozen=True)
class Theme:
    name: str
    title: str
    description: str
    groups: tuple  # ((group name, (Param, ...)), ...)
    fonts: str = ""
    css: tuple = ()
    js: tuple = ()
    thumb: dict = field(default_factory=dict)
    contrast: tuple = ()  # ((param, param, "what it is"), ...): colour pairs Branding warns about below 4.5:1
    app: dict = field(default_factory=dict)  # {"theme", "background"}: colour params for the store app

    @property
    def params(self) -> dict:
        return {p.name: p for _group, params in self.groups for p in params}

    def defaults(self) -> dict:
        return {name: p.default for name, p in self.params.items()}

    def clean(self, raw) -> dict:
        """`raw` with defaults filled in; values that don't fit their param fall back to the default."""
        raw = raw if isinstance(raw, dict) else {}
        out = {}
        for name, param in self.params.items():
            value = param.check(raw[name]) if name in raw else None
            out[name] = param.default if value is None else value
        return out

    def template(self, page):
        return f"themes/{self.name}/{page}"


def _param(name, spec, where):
    if not isinstance(spec, dict) or spec.get("type") not in TYPES:
        raise ImproperlyConfigured(f"{where}: param {name!r} needs a type, one of {', '.join(TYPES)}")
    kind = spec["type"]
    control = spec.get("control", "")
    if control and control not in CONTROLS.get(kind, ()):
        raise ImproperlyConfigured(f"{where}: {name!r} can't use control {control!r}")
    if kind == "choice" and not spec.get("options"):
        raise ImproperlyConfigured(f"{where}: choice param {name!r} needs options")
    param = Param(name=name, type=kind, default=spec.get("default"), label=spec.get("label", name), help=spec.get("help", ""),
                  options=tuple(spec.get("options", ())), labels=tuple(spec.get("labels", ())), presets=tuple(spec.get("presets", ())),
                  min=spec.get("min"), max=spec.get("max"), step=spec.get("step", 1), unit=spec.get("unit", ""),
                  control=control or {"int": "stepper", "number": "stepper", "choice": "select"}.get(kind, ""), show_if=spec.get("show_if", ""))
    if param.check(param.default) is None:
        raise ImproperlyConfigured(f"{where}: {name!r} has a default that doesn't fit its type")
    return param


def _load(path):
    where = f"online_store/themes/{path.name}"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ImproperlyConfigured(f"{where} isn't valid JSON: {error}") from error
    if not NAME_RE.match(path.stem) or not spec.get("title"):
        raise ImproperlyConfigured(f"{where} needs a lowercase name and a title")
    groups = []
    seen = set()
    for group in spec.get("groups", []):
        params = tuple(_param(name, value, where) for name, value in group.get("params", {}).items())
        for p in params:
            if p.name in seen:
                raise ImproperlyConfigured(f"{where}: {p.name!r} is in two groups")
            seen.add(p.name)
        groups.append((group.get("name", ""), params))
    for _group, params in groups:
        for p in params:
            if p.show_if and p.show_if not in seen:
                raise ImproperlyConfigured(f"{where}: {p.name!r} shows if {p.show_if!r}, which isn't a param")
    contrast = tuple(tuple(pair) for pair in spec.get("contrast", ()))
    for pair in contrast:
        if len(pair) != 3 or pair[0] not in seen or pair[1] not in seen:
            raise ImproperlyConfigured(f"{where}: contrast pairs are [colour param, colour param, label]")
    app = spec.get("app", {})
    if not isinstance(app, dict) or any(k not in ("theme", "background") or v not in seen for k, v in app.items()):
        raise ImproperlyConfigured(f"{where}: app maps theme and background to colour params")
    return Theme(name=path.stem, title=spec["title"], description=spec.get("description", ""), groups=tuple(groups),
                 fonts=spec.get("fonts", ""), css=tuple(spec.get("css", ())), js=tuple(spec.get("js", ())), thumb=spec.get("thumb", {}),
                 contrast=contrast, app=app)


def _read_all():
    return {path.stem: _load(path) for path in sorted(THEMES_DIR.glob("*.json"))}


_read_cached = cache(_read_all)


def themes() -> dict:
    """{name: Theme}. Read from disk on every call in DEBUG, once per process otherwise (they're code)."""
    return _read_all() if settings.DEBUG else _read_cached()


def get(name):
    found = themes()
    return found.get(name) or next(iter(found.values()))


def media_url(name):
    return default_storage.url(name) if name else ""


@dataclass
class Current:
    """What a store page renders with: the theme, its values (`t` in templates) and the store details."""
    theme: Theme
    values: dict
    store: dict
    preview: bool = False

    @property
    def logo_url(self):
        return media_url(self.store.get("logo"))


def store_details(store, draft=False) -> dict:
    details = store.details()
    if draft:
        details.update({k: v for k, v in (store.draft or {}).items() if k in details and isinstance(v, str)})
    return details


def previewing(request) -> str:
    """The theme name the Branding page is previewing, for its own preview iframe; else ''."""
    user = getattr(request, "user", None)
    if not user or not user.is_superuser:
        return ""
    if request.GET.get("preview") or request.headers.get("Sec-Fetch-Dest") == "iframe":
        return request.session.get(PREVIEW_KEY, "")
    return ""


def current(request) -> Current:
    from admin_portal.models import StoreSettings, StoreTheme
    store = StoreSettings.load()
    preview = previewing(request)
    theme = get(preview or store.theme)
    row = StoreTheme.objects.filter(name=theme.name).first()
    values = (row.draft if preview else row.live) if row else {}
    return Current(theme=theme, values=theme.clean(values), store=store_details(store, draft=bool(preview)), preview=bool(preview))


FONT_FALLBACK = {"serif": ("Fraunces", "DM Serif Display", "Playfair Display", "Lora"), "sans-serif": ()}


def css_vars(theme, values) -> str:
    """`--name: value;` for the theme's colours, sizes (`unit`) and fonts (choices named *_font). Every value has
    been checked against its param, so it's safe to put in a style tag."""
    out = []
    for name, param in theme.params.items():
        value, var = values.get(name), "--" + name.replace("_", "-")
        if param.type == "color":
            out.append(f"{var}: {value};")
        elif param.type in ("int", "number") and param.unit:
            out.append(f"{var}: {value}{param.unit};")
        elif param.type == "choice" and name.endswith("_font"):
            fallback = "serif" if value in FONT_FALLBACK["serif"] else "sans-serif"
            out.append(f"{var}: '{value}', {fallback};")
    return " ".join(out)
