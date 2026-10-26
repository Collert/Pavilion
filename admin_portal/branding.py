"""
The admin's Branding page (design: AdminBranding): pick the online store's theme and set what it exposes (its params,
online_store/themes/<name>.json) plus the store details every theme shows. Every change is kept as a draft right away
(StoreTheme.draft, StoreSettings.draft) so the preview, the real store in an iframe, can show it; Publish makes it
what customers see.
"""
import hashlib
import json
import os
import uuid

from django.core.files.storage import default_storage
from django.utils.translation import gettext as _

from online_store import themes

from .models import StoreSettings, StoreTheme

UPLOAD_LIMIT = 8 * 1024 * 1024
STORE_FIELDS = [
    themes.Param("name", "string", "", label=_("Store name"), max=60),
    themes.Param("logo", "image", "", label=_("Logo"), help=_("Square works best, at least 256 px")),
    themes.Param("address", "string", "", label=_("Street address"), max=120),
    themes.Param("city", "string", "", label=_("City and postal code"), max=120),
    themes.Param("phone", "string", "", label=_("Phone"), max=40),
    themes.Param("directions", "url", "", label=_("Map link"), help=_("Leave empty to search the map for your address")),
]


def row(theme) -> StoreTheme:
    found, _created = StoreTheme.objects.get_or_create(name=theme.name)
    return found


def draft_of(theme):
    """(theme values, store details) being edited."""
    store = StoreSettings.load()
    return theme.clean(row(theme).draft), themes.store_details(store, draft=True)


def from_post(theme, post):
    """The draft from the form: theme params as `v-<name>`, store details as `s-<name>`."""
    values = {}
    for name, param in theme.params.items():
        key = f"v-{name}"
        if param.type == "bool":
            values[name] = post.get(key) == "1"
        elif key in post:
            raw = post.get(key)
            if param.type == "color" and raw and not raw.startswith("#"):
                raw = "#" + raw
            values[name] = raw
    store = {p.name: p.check(post.get(f"s-{p.name}", "")) for p in STORE_FIELDS}
    return theme.clean(values), {k: v or "" for k, v in store.items()}


def apply_op(theme, values, store, op):
    """toggle:<param>, set:<param>:<value>, less|more:<param>, clear:<param or s.logo>, reset."""
    name, _sep, rest = op.partition(":")
    params = theme.params
    if name == "toggle" and rest in params:
        values[rest] = not values[rest]
    elif name == "set":
        key, _sep, value = rest.partition(":")
        if key in params and params[key].check(value) is not None:
            values[key] = params[key].check(value)
    elif name in ("less", "more") and rest in params:
        p = params[rest]
        step = p.step if name == "more" else -p.step
        nudged = p.check(values[rest] + step)
        if nudged is not None:
            values[rest] = nudged
    elif name == "clear":
        if rest == "s.logo":
            store["logo"] = ""
        elif rest in params:
            values[rest] = ""
    elif name == "reset":
        values = theme.defaults()
    return values, store


def upload(file, kind) -> str:
    """Keep an uploaded image; returns its media name ("" when it's not an image or too big)."""
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg") or file.size > UPLOAD_LIMIT:
        return ""
    return default_storage.save(f"branding/{kind}-{uuid.uuid4().hex[:8]}{ext}", file)


def save_draft(theme, values, store):
    StoreTheme.objects.filter(pk=row(theme).pk).update(draft=values)
    settings = StoreSettings.load()
    settings.draft = store
    settings.save(update_fields=["draft"])


def publish(theme, values, store):
    StoreTheme.objects.filter(pk=row(theme).pk).update(draft=values, live=values)
    s = StoreSettings.load()
    s.name = store["name"].strip() or s.name
    s.address, s.city, s.phone, s.directions = store["address"], store["city"], store["phone"], store["directions"]
    s.logo.name = store["logo"] or None
    s.theme, s.draft = theme.name, store
    s.save()


def is_dirty(theme, values, store) -> bool:
    return values != theme.clean(row(theme).live) or store != StoreSettings.load().details() or StoreSettings.load().theme != theme.name


# ---------- colours ----------

def _lum(hex_):
    def chan(i):
        v = int(hex_[i:i + 2], 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(1) + 0.7152 * chan(3) + 0.0722 * chan(5)


def contrast(a, b) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


# ---------- the page ----------

def _field(param, value, prefix, warns):
    shown = {"name": f"{prefix}{param.name}", "p": param, "value": value, "warn": warns.get(param.name, ""),
             "count": f"{len(value)} / {int(param.max)}" if param.type == "string" and param.max else ""}
    if param.type in ("string", "text") and not str(value).strip() and prefix == "v-":
        shown["empty"] = _("This is empty, so it’s hidden on the store.")
    if param.type == "image":
        shown["url"] = themes.media_url(value)
    if param.type in ("int", "number"):
        shown["less_off"] = param.min is not None and value - param.step < param.min
        shown["more_off"] = param.max is not None and value + param.step > param.max
    if param.type == "choice":
        shown["choices"] = [{"value": v, "label": label, "on": v == value} for v, label in param.choices]
    if param.type == "color":
        shown["presets"] = [{"value": c, "on": c.upper() == str(value).upper()} for c in param.presets]
    return shown


def _thumb(theme, values):
    k = theme.thumb
    get = lambda key, default: values.get(k.get(key, ""), default)  # noqa: E731
    return {"bg": get("background", "#FFFFFF"), "fg": get("text", "#111111"), "accent": get("accent", "#111111"),
            "card": get("card", "#FFFFFF"), "font": get("font", "")}


def page(theme, values, store, device="desktop"):
    live_name = StoreSettings.load().theme
    warns = {}
    for a, b, label in theme.contrast:
        ratio = contrast(values[a], values[b])
        if ratio < 4.5:
            text = _("%(pair)s is %(ratio).1f:1. Aim for 4.5:1 so menus are easy to read.") % {"pair": label, "ratio": ratio}
            warns.setdefault(a, text)
            warns.setdefault(b, text)
    groups = [{"name": _("Store"), "fields": [_field(p, store.get(p.name, ""), "s-", {}) for p in STORE_FIELDS]}]
    for name, params in theme.groups:
        groups.append({"name": name, "fields": [_field(p, values[p.name], "v-", warns) for p in params
                                                if not p.show_if or values.get(p.show_if)],
                       "hidden": [(p.name, ("1" if values[p.name] else "") if p.type == "bool" else values[p.name])
                                  for p in params if p.show_if and not values.get(p.show_if)]})
    cards = []
    for t in themes.themes().values():
        t_values = theme.clean(row(t).draft) if t.name != theme.name else values
        cards.append({"t": t, "on": t.name == theme.name, "live": t.name == live_name, "thumb": _thumb(t, t_values)})
    dirty = is_dirty(theme, values, store)
    switching = live_name != theme.name
    if switching:
        status, kind = _("Previewing %(name)s, not live yet") % {"name": theme.title}, "dirty"
    elif dirty:
        status, kind = _("Unpublished changes"), "dirty"
    else:
        status, kind = _("Everything is live"), ""
    version = hashlib.sha1(json.dumps([values, store], sort_keys=True).encode()).hexdigest()[:10]
    return {"theme": theme, "values": values, "store": store, "groups": groups, "cards": cards, "status": status,
            "status_kind": kind, "can_publish": dirty or switching, "publish_text": _("Publish %(name)s") % {"name": theme.title} if switching else _("Publish"),
            "is_default": values == theme.defaults(), "live_title": themes.get(live_name).title, "version": version,
            "device": "mobile" if device == "mobile" else "desktop", "domain": (store.get("name") or "store").lower().replace(" ", "")}
