"""
A screen's layout is a tree of widget nodes, stored as JSON on `Screen.layout`:

    {"widget": "columns", "params": {"count": 2, "weights": [1, 2]},
     "children": [{"widget": "clock"}, null]}

- `widget` names a definition in `screens/widgets/` (see registry.py).
- `params` are checked against the definition's `params`; anything left out gets its default.
- `children` only for widgets with `slots` (rows, columns): up to `params[slots]` nodes, `null` for an empty slot.

`validate_layout()` is strict (the admin refuses a bad layout). `render_screen()` is forgiving, because widget
definitions can change after a layout was saved: a bad param falls back to its default and a missing widget or a
failing data provider shows a placeholder instead of taking the whole screen down.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from . import registry

logger = logging.getLogger(__name__)

MAX_DEPTH = 12
MAX_NODES = 300
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SIZE_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:px|fr|%)|auto)$")
TEXT_LIMITS = {"string": 300, "text": 3000, "url": 2000, "image": 2000}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_value(param, value):
    """The problem with `value` for `param` as a short message, or None if it's fine."""
    kind = param.type
    if kind in TEXT_LIMITS:
        if not isinstance(value, str):
            return "should be text"
        if len(value) > TEXT_LIMITS[kind]:
            return f"is longer than {TEXT_LIMITS[kind]} characters"
        if kind in ("url", "image") and value and not (value.startswith("https://") or (value.startswith("/") and not value.startswith("//"))):
            return "should start with https:// or /"
        return None
    if kind == "bool":
        return None if isinstance(value, bool) else "should be true or false"
    if kind in ("int", "number"):
        if not _is_number(value) or (kind == "int" and not isinstance(value, int)):
            return "should be a whole number" if kind == "int" else "should be a number"
        if param.min is not None and value < param.min:
            return f"should be at least {param.min}"
        if param.max is not None and value > param.max:
            return f"should be at most {param.max}"
        return None
    if kind == "choice":
        return None if value in param.options else f"should be one of {', '.join(map(str, param.options))}"
    if kind == "color":
        return None if isinstance(value, str) and COLOR_RE.match(value) else "should be a colour like #1C2321"
    if kind == "size":
        if _is_number(value):
            return None if value > 0 else "should be more than 0"
        return None if isinstance(value, str) and SIZE_RE.match(value) else 'should be a number (fr) or like "132px", "40%", "auto"'
    if kind == "list":
        if not isinstance(value, list):
            return "should be a list"
        if param.min is not None and len(value) < param.min:
            return f"should have at least {param.min} items"
        if param.max is not None and len(value) > param.max:
            return f"should have at most {param.max} items"
        for index, item in enumerate(value):
            if problem := check_value(param.item, item):
                return f"item {index + 1} {problem}"
        return None
    return f"has an unknown type {kind}"


def _empty(param):
    return {"bool": False, "list": [], "int": 0, "number": 0}.get(param.type, "")


def clean_params(widget, raw, errors=None, where=""):
    """`raw` with defaults filled in. Bad values are reported to `errors` (if given) and replaced by the default."""
    if not isinstance(raw, dict):
        if raw is not None and errors is not None:
            errors.append(f"{where}: params should be an object")
        raw = {}
    params = {}
    for name, param in widget.params.items():
        default = param.default if param.default is not None else _empty(param)
        if name not in raw:
            params[name] = default
            continue
        problem = check_value(param, raw[name])
        if problem:
            if errors is not None:
                errors.append(f"{where}: {name} {problem}")
            params[name] = default
        else:
            params[name] = raw[name]
    if errors is not None:
        for name in raw.keys() - widget.params.keys():
            errors.append(f"{where}: {widget.name} has no param {name!r}")
    return params


def _describe(path):
    return "screen" if not path else "screen › " + " › ".join(f"slot {int(step) + 1}" for step in path)


def layout_errors(tree):
    """Every problem with a layout tree, as readable messages ([] if it's fine)."""
    errors, count = [], 0

    def visit(node, path):
        nonlocal count
        where = _describe(path)
        if node is None:
            return
        count += 1
        if count > MAX_NODES:
            if count == MAX_NODES + 1:
                errors.append(f"A screen can hold at most {MAX_NODES} widgets")
            return
        if len(path) > MAX_DEPTH:
            errors.append(f"{where}: nested deeper than {MAX_DEPTH} levels")
            return
        if not isinstance(node, dict):
            errors.append(f"{where}: should be an object like {{\"widget\": \"clock\"}} or null")
            return
        widget = registry.get(node.get("widget"))
        if widget is None:
            errors.append(f"{where}: unknown widget {node.get('widget')!r}")
            return
        where = f"{where} ({widget.name})"
        for key in node.keys() - {"widget", "params", "children"}:
            errors.append(f"{where}: unexpected key {key!r}")
        params = clean_params(widget, node.get("params"), errors, where)
        children = node.get("children")
        if children is None:
            return
        if not widget.slots:
            errors.append(f"{where}: this widget doesn't hold other widgets")
            return
        if not isinstance(children, list):
            errors.append(f"{where}: children should be a list")
            return
        if len(children) > params[widget.slots]:
            errors.append(f"{where}: {len(children)} children for {params[widget.slots]} slots (raise {widget.slots})")
        for index, child in enumerate(children):
            visit(child, path + (index,))

    if tree in (None, {}):
        return errors
    visit(tree, ())
    return errors


def validate_layout(tree):
    if errors := layout_errors(tree):
        raise ValidationError(errors)


# ---------- rendering ----------

@dataclass
class RenderedScreen:
    html: str
    css: list = field(default_factory=list)
    js: list = field(default_factory=list)
    topics: list = field(default_factory=list)


def _add(target, items):
    target.extend(item for item in items if item not in target)


class _Renderer:
    def __init__(self, request, prefix, editing=None):
        self.request = request
        self.prefix = prefix
        self.result = RenderedScreen(html="")
        self.count = 0
        self.editing = editing  # the admin editor's preview: {"sel": path}; nodes carry their path, empty slots invite a widget

    def _edit_attrs(self, path):
        return format_html(' data-path="{}"', path) if self.editing is not None else ""

    def _selected(self, path):
        return " is-selected" if self.editing is not None and self.editing.get("sel") == path else ""

    def node(self, spec, path, depth=0):
        node_id = f"{self.prefix}-{path}"
        if not isinstance(spec, dict) or depth > MAX_DEPTH or self.count >= MAX_NODES:
            if self.editing is not None:
                return format_html('<div class="widget slot-empty slot-edit{}" id="{}" data-path="{}"><span>+ Add widget</span></div>',
                                   " is-selected" if self.editing.get("sel") == path else "", node_id, path)
            return format_html('<div class="widget slot-empty" id="{}"></div>', node_id)
        self.count += 1
        widget = registry.get(spec.get("widget"))
        if widget is None:
            return self.placeholder(node_id, f"Unknown widget {spec.get('widget')!r}")
        params = clean_params(widget, spec.get("params"))
        children = []
        if widget.slots:
            raw = spec.get("children") if isinstance(spec.get("children"), list) else []
            raw = (raw + [None] * params[widget.slots])[:params[widget.slots]]
            children = [self.node(child, f"{path}-{index}", depth + 1) for index, child in enumerate(raw)]
        try:
            provider = widget.provider()
            data = provider(self.request, params) if provider else {}
            html = render_to_string(widget.template, {
                "params": params, "children": children, "data": data, "node_id": node_id, "widget": widget,
                "request": self.request,
            })
        except Exception:
            logger.exception("Screen widget %s failed to render", widget.name)
            return self.placeholder(node_id, f"{widget.title} couldn't load")
        _add(self.result.css, widget.css)
        _add(self.result.js, widget.js)
        _add(self.result.topics, widget.live)
        # Leaves are live regions (static/js/live-page.js swaps the ones whose HTML changed). Containers aren't, so
        # an update to one order list doesn't replace (and restart the animations of) everything around it.
        return format_html('<div class="widget widget--{}{}" id="{}"{}{}>{}</div>', widget.name, self._selected(path), node_id,
                           mark_safe("" if widget.slots else " data-region"), self._edit_attrs(path), html)

    def placeholder(self, node_id, message):
        return format_html('<div class="widget widget-missing" id="{}" data-region><p>{}</p></div>', node_id, message)


def layout_key(screen):
    """Changes whenever the layout or canvas does, so node ids change and live pages reload the whole screen."""
    blob = json.dumps([screen.layout, screen.width, screen.height, screen.background, screen.padding], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


def render_screen(screen, request, editing=None):
    """The screen's HTML, its pop-ups after the layout. `editing` ({"sel": path, "popup": name}) is the admin editor's
    preview: every node carries its path, and the named pop-up shows with sample data."""
    renderer = _Renderer(request, "w" + layout_key(screen), editing)
    html = renderer.node(screen.layout or None, "r")
    renderer.result.html = html + render_popups(screen, request, renderer.result, force=(editing or {}).get("popup"))
    return renderer.result


def render_popups(screen, request, result, force=None):
    """Each of the screen's pop-ups as its own live region: its card while it's showing, and a hidden data-ends for
    when the page should look again (to hide it, or for a scheduled one to appear). See popups.py."""
    from django.utils import timezone

    from .popups import SAMPLES
    now = timezone.now()
    parts = []
    for index, spec in enumerate(screen.popups or []):
        widget = registry.popups().get(spec.get("widget")) if isinstance(spec, dict) else None
        if widget is None:
            continue
        params = clean_params(widget, spec.get("params"))
        _add(result.css, widget.css)
        _add(result.topics, widget.live)
        try:
            if force == widget.name:
                state = {"show": True, **SAMPLES[widget.trigger]()}
            else:
                provider = widget.provider()
                state = provider(request, params, now) if provider else {"show": False}
            html = render_to_string(widget.template, {"params": params, "data": state, "widget": widget, "request": request})                 if state.get("show") else ""
        except Exception:
            logger.exception("Screen pop-up %s failed", widget.name)
            state, html = {"show": False}, ""
        ends = state.get("until") if state.get("show") else state.get("next")
        timer = format_html('<span hidden data-ends="{}"></span>', int(ends.timestamp() * 1000)) if ends and not force else ""
        shown_for = format_html(' data-preview-seconds="{}"', params.get("seconds", 6)) if force == widget.name else ""
        parts.append(format_html('<div class="popup-slot" id="pop-{}-{}" data-region{}>{}{}</div>', index, widget.name, shown_for,
                                 mark_safe(html), timer))
    return mark_safe("".join(parts))
