"""
Screens (design: AdminScreens): the list of screens with a thumbnail of each layout, "New screen", and the editor: a
live preview of the draft (the real screen, rendered in an iframe), the layout as a tree, and for what's selected the
screen's settings, the widget library (an empty slot), rows and columns, or the widget's settings, which come from its
JSON definition (screens/widgets/<name>.json, see screens/registry.py).

The draft lives in the session while it's edited, with an undo history; Save writes it to the Screen and nudges open
screens to reload ("screens"). Nodes are addressed by path: "r" is the root, "r-0-1" the second child of its first.

    request.session["screen_edit_<id>"] = {"draft": {"name", "orient": "land"|"port", "bg", "public", "layout",
                                                       "popups": [{"widget", "params"}],
                                                       "audio": [{"id", "widget", "params"}], top channel first},
                                             "sel": "screen" or a path, "tab": "layout"|"popups", "open_pop": index,
                                             "pop_preview": a pop-up's name, "hist": [earlier drafts]}

Settings forms post their fields as p-<param> for what's being edited (`editing`: a node's path, or "pop:<index>").
"""
import copy
import hashlib
import json
import uuid

from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _, ngettext

from screens import registry
from screens.layout import check_value, clean_params, layout_errors
from screens.models import Screen

SIZES = {"land": (1920, 1080), "port": (1080, 1920)}
BACKGROUNDS = [("#14261F", _("Pine")), ("#1F4A3A", _("Forest")), ("#101010", _("Black")), ("#2A2620", _("Espresso")),
               ("#1E2A3A", _("Navy")), ("#F4F1EA", _("Paper"))]
HIST = 40
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg"}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------- the layout tree ----------

def node_at(tree, path):
    """The node at `path` ("r", "r-0", ...), None for an empty slot, or False when the path isn't in the tree."""
    if path == "r":
        return tree
    if not path.startswith("r-"):
        return False
    node = tree
    for step in path[2:].split("-"):
        if not isinstance(node, dict) or not str(step).isdigit():
            return False
        children = node.get("children") or []
        widget = registry.get(node.get("widget"))
        if not widget or not widget.slots:
            return False
        count = clean_params(widget, node.get("params"))[widget.slots]
        if int(step) >= count:
            return False
        node = children[int(step)] if int(step) < len(children) else None
    return node


def put(tree, path, value):
    """The tree with `value` at `path` (a copy)."""
    if path == "r":
        return copy.deepcopy(value)
    tree = copy.deepcopy(tree)
    steps = [int(x) for x in path[2:].split("-")]
    node = tree
    for step in steps[:-1]:
        node = node["children"][step]
    children = node.setdefault("children", [])
    while len(children) <= steps[-1]:
        children.append(None)
    children[steps[-1]] = copy.deepcopy(value)
    return tree


def walk(tree, path="r", depth=0):
    """[(path, node, depth)] in order, empty slots included."""
    out = [(path, tree, depth)]
    widget = registry.get(tree.get("widget")) if isinstance(tree, dict) else None
    if widget and widget.slots:
        count = clean_params(widget, tree.get("params"))[widget.slots]
        children = (tree.get("children") or []) + [None] * count
        for i in range(count):
            out += walk(children[i], f"{path}-{i}", depth + 1)
    return out


def first_empty(tree):
    return next((p for p, n, _d in walk(tree) if n is None), None)


def label(node):
    if node is None:
        return _("Empty slot")
    widget = registry.get(node.get("widget"))
    return widget.title if widget else node.get("widget", "?")


# ---------- the draft ----------

def orient_of(screen):
    return "land" if screen.width >= screen.height else "port"


def draft_of(screen) -> dict:
    return {"name": screen.name, "orient": orient_of(screen), "bg": screen.background, "public": screen.public,
            "layout": copy.deepcopy(screen.layout) or None, "popups": copy.deepcopy(screen.popups or []), "sessions": screen.sessions,
            "audio": copy.deepcopy(screen.audio or [])}


def key(screen):
    return f"screen_edit_{screen.id}"


def load(request, screen) -> dict:
    state = request.session.get(key(screen))
    if not state:
        state = {"draft": draft_of(screen), "sel": "screen", "hist": []}
    state.setdefault("tab", "layout")
    state.setdefault("open_pop", None)
    state.setdefault("pop_preview", None)
    state["draft"].setdefault("popups", [])
    state["draft"].setdefault("sessions", screen.sessions)
    state["draft"].setdefault("audio", [])
    return state


def store(request, screen, state):
    request.session[key(screen)] = state
    request.session.modified = True


def forget(request, screen):
    request.session.pop(key(screen), None)


def is_dirty(screen, state) -> bool:
    return state["draft"] != draft_of(screen)


def _param_from_post(param, raw, current):
    """A posted field as the param's value, or the current value when it doesn't check out."""
    kind = param.type
    try:
        if kind in ("int",):
            value = int(float(raw))
        elif kind == "number":
            value = float(raw)
            value = int(value) if value.is_integer() and isinstance(current, int) else value
        elif kind == "bool":
            value = raw == "1"
        elif kind in ("list",):
            value = json.loads(raw)
        elif kind == "size":
            value = float(raw) if raw.replace(".", "", 1).isdigit() else raw.strip()
        else:
            value = raw
    except (TypeError, ValueError):
        return current
    return current if check_value(param, value) else value


def target(draft, editing):
    """What a settings form edits: (widget, params, write(params)) for a layout node's path or "pop:<index>"."""
    if editing.startswith("pop:"):
        i = _int(editing[4:], -1)
        if not 0 <= i < len(draft["popups"]):
            return None
        spec = draft["popups"][i]
        widget = registry.popups().get(spec.get("widget"))

        def write(params):
            draft["popups"][i] = {**spec, "params": _changed(widget, params)}
        return (widget, clean_params(widget, spec.get("params")), write) if widget else None
    node = node_at(draft["layout"], editing) if editing else False
    widget = registry.get(node.get("widget")) if isinstance(node, dict) else None
    if widget is None:
        return None

    def write(params):
        draft["layout"] = put(draft["layout"], editing, {**node, "params": _changed(widget, params)})
    return widget, clean_params(widget, node.get("params")), write


def from_post(state, post, files):
    """The posted fields onto the draft: the screen's name, the audio channels shown, and the edited node's or
    pop-up's params."""
    draft = state["draft"]
    if "name" in post:
        draft["name"] = post["name"].strip()[:100]
    for i, channel in enumerate(draft["audio"]):  # the Audio tab shows every channel's fields: a-<index>-<param>
        widget = registry.audio().get(channel.get("widget"))
        if widget is None or f"a-{i}-channel" not in post:
            continue
        params = clean_params(widget, channel.get("params"))
        for name, param in widget.params.items():
            if f"a-{i}-{name}" in post:
                params[name] = _param_from_post(param, post[f"a-{i}-{name}"], params[name])
        channel["params"] = _changed(widget, params)
    found = target(draft, post.get("editing", ""))
    if found is None:
        return []
    widget, params, write = found
    errors = []
    for name, param in widget.params.items():
        field = f"p-{name}"
        upload = files.get(f"upload-{name}")
        if upload is not None and param.type == "image":
            saved = save_upload(upload)
            if saved:
                params[name] = saved
            else:
                errors.append(_("Use a PNG, JPEG, WebP, GIF or SVG image under 8 MB."))
            continue
        if field in post:
            params[name] = _param_from_post(param, post[field], params[name])
    write(params)
    return errors


def _changed(widget, params):
    """Only the params that differ from the defaults are kept, like a hand-written layout."""
    defaults = clean_params(widget, {})
    return {k: v for k, v in params.items() if v != defaults.get(k)}


def save_upload(upload):
    if upload.content_type not in IMAGE_TYPES or upload.size > 8 * 1024 * 1024:
        return ""
    name = default_storage.save(f"screens/{uuid.uuid4().hex[:12]}.{IMAGE_TYPES[upload.content_type]}", upload)
    return default_storage.url(name)


def _place(state, path, name):
    widget = registry.get(name)
    if widget is None or node_at(state["draft"]["layout"], path) is not None:
        return False
    node = {"widget": name, "params": {}}
    if widget.slots:
        node = {"widget": name, "params": {widget.slots: 2}, "children": [None, None]}
    state["draft"]["layout"] = put(state["draft"]["layout"], path, node)
    state["sel"] = f"{path}-0" if widget.slots else path
    return True


def apply_op(state, op, editing="") -> str:
    """Apply a button's op to the draft; returns a toast. Changes go on the undo history."""
    before = copy.deepcopy(state["draft"])
    if not op.startswith("ppreview:"):
        state["pop_preview"] = None
    toast = _apply(state, op, editing)
    if op != "undo" and state["draft"] != before:
        state["hist"] = (state["hist"] + [before])[-HIST:]
    return toast


def _apply(state, op, editing="") -> str:
    draft, sel = state["draft"], state["sel"]
    name, _sep, arg = op.partition(":")
    tree = draft["layout"]
    node = node_at(tree, sel) if sel != "screen" else False
    widget = registry.get(node.get("widget")) if isinstance(node, dict) else None
    if name == "sel" and (arg == "screen" or node_at(tree, arg) is not False):
        state["sel"] = arg
    elif name == "undo" and state["hist"]:
        state["draft"] = state["hist"].pop()
        if state["sel"] != "screen" and node_at(state["draft"]["layout"], state["sel"]) is False:
            state["sel"] = "screen"
    elif name == "add" and registry.get(arg):
        slot = sel if sel != "screen" and node is None else first_empty(tree)
        if slot is None:
            return _("No empty slot. Remove a widget or add rows or columns first.")
        _place(state, slot, arg)
    elif name == "drop":
        path, _s, widget_name = arg.rpartition(":")
        _place(state, path, widget_name)
    elif name in ("remove", "swap") and isinstance(node, dict):
        draft["layout"] = put(tree, sel, None)
        if name == "remove":
            what = _("Rows") if node.get("widget") == "rows" else _("Columns") if node.get("widget") == "columns" else label(node)
            return _("%(what)s removed. Undo brings it back.") % {"what": what}
    elif name == "flip" and widget and widget.slots:
        draft["layout"] = put(tree, sel, {**node, "widget": "columns" if node["widget"] == "rows" else "rows"})
    elif name in ("more", "less") and widget and widget.slots:
        params = clean_params(widget, node.get("params"))
        count = params[widget.slots]
        new = min(max(count + (1 if name == "more" else -1), 1), 6)
        padded = (node.get("children") or []) + [None] * max(count, new)
        children = padded[:new]
        weights = list(params.get("weights") or [])[:new]
        lost = new < count and padded[count - 1] is not None
        draft["layout"] = put(tree, sel, {**node, "params": _changed(widget, {**params, widget.slots: new, "weights": weights}),
                                          "children": children})
        if lost:
            return (_("Removed the last row and its widget. Undo brings it back.") if node["widget"] == "rows"
                    else _("Removed the last column and its widget. Undo brings it back."))
    elif name in ("wless", "wmore") and widget and widget.slots:
        params = clean_params(widget, node.get("params"))
        i = _int(arg, -1)
        weights = list(params.get("weights") or [])
        weights += [1] * (params[widget.slots] - len(weights))
        if 0 <= i < len(weights):
            w = weights[i]
            if isinstance(w, str) and w.endswith("px"):
                px = max(20, int(float(w[:-2])) + (20 if name == "wmore" else -20))
                weights[i] = f"{px}px"
            else:
                weights[i] = min(max(round(_num(w, 1)) + (1 if name == "wmore" else -1), 1), 8)
            draft["layout"] = put(tree, sel, {**node, "params": _changed(widget, {**params, "weights": weights})})
    elif name == "orient" and arg in SIZES and draft["orient"] != arg:
        draft["orient"] = arg
        return _("Switched to landscape. Check that everything still fits.") if arg == "land" else _("Switched to portrait. Check that everything still fits.")
    elif name == "bg" and arg in dict(BACKGROUNDS):
        draft["bg"] = arg
    elif name == "public":
        draft["public"] = not draft["public"]
    elif name == "sessions":
        draft["sessions"] = not draft["sessions"]
    elif name == "aadd" and arg in ("music", "voice"):
        from audio.models import AudioChannel
        used = {c.get("params", {}).get("channel") for c in draft["audio"]}
        pick = next((c.id for c in AudioChannel.objects.filter(kind=arg, archived=False) if c.id not in used), 0)
        draft["audio"].append({"id": "c" + uuid.uuid4().hex[:6], "widget": f"{arg}_channel", "params": {"channel": pick} if pick else {}})
    elif name == "adel" and 0 <= _int(arg, -1) < len(draft["audio"]):
        gone = draft["audio"].pop(_int(arg))
        return _("%(name)s removed. Undo brings it back.") % {"name": _channel_name(gone)}
    elif name in ("aup", "adown") and 0 <= _int(arg, -1) < len(draft["audio"]):
        i = _int(arg)
        j = i - 1 if name == "aup" else i + 1
        if 0 <= j < len(draft["audio"]):
            draft["audio"][i], draft["audio"][j] = draft["audio"][j], draft["audio"][i]
    elif name == "atype":
        index, _s, kind = arg.partition(":")
        if 0 <= _int(index, -1) < len(draft["audio"]) and kind in ("music", "voice"):
            channel = draft["audio"][_int(index)]
            if channel["widget"] != f"{kind}_channel":
                from audio.models import AudioChannel
                pick = AudioChannel.objects.filter(kind=kind, archived=False).values_list("id", flat=True).first()
                draft["audio"][_int(index)] = {**channel, "widget": f"{kind}_channel", "params": {"channel": pick} if pick else {}}
    elif name == "tab" and arg in ("layout", "popups", "audio"):
        state["tab"] = arg
    elif name == "padd" and arg in registry.popups() and not any(p.get("widget") == arg for p in draft["popups"]):
        draft["popups"].append({"widget": arg, "params": {}})
        state["open_pop"] = len(draft["popups"]) - 1
    elif name == "pdel" and 0 <= _int(arg, -1) < len(draft["popups"]):
        gone = draft["popups"].pop(_int(arg))
        state["open_pop"] = None
        w = registry.popups().get(gone.get("widget"))
        return _("%(what)s removed. Undo brings it back.") % {"what": w.title if w else gone.get("widget")}
    elif name == "popen" and 0 <= _int(arg, -1) < len(draft["popups"]):
        state["open_pop"] = None if state["open_pop"] == _int(arg) else _int(arg)
    elif name == "ppreview" and 0 <= _int(arg, -1) < len(draft["popups"]):
        state["pop_preview"] = draft["popups"][_int(arg)].get("widget")
        state["pop_seq"] = state.get("pop_seq", 0) + 1  # pressing Preview again shows it again
    elif name in ("pset", "ptog", "pless", "pmore"):
        found = target(draft, editing or (sel if sel != "screen" else ""))
        if found is None:
            return ""
        widget, params, write = found
        pname, _s, value = arg.partition(":")
        param = widget.params.get(pname)
        if param is None:
            return ""
        if name == "ptog":
            params[pname] = not params[pname]
        elif name == "pset":
            params[pname] = _param_from_post(param, value, params[pname])
        else:
            step = param.step or 1
            new = params[pname] + (step if name == "pmore" else -step)
            if param.min is not None:
                new = max(param.min, new)
            if param.max is not None:
                new = min(param.max, new)
            params[pname] = int(new) if param.type == "int" else new
        write(params)
    return ""


def unique_slug(name, exclude=None):
    base = slugify(name)[:40] or "screen"
    slug, n = base, 2
    while Screen.objects.filter(slug=slug).exclude(pk=exclude).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create(name, orient) -> Screen:
    width, height = SIZES.get(orient, SIZES["land"])
    return Screen.objects.create(name=name.strip()[:100], slug=unique_slug(name), width=width, height=height,
                                 background="#14261F", layout=None)


def save(screen, state) -> list[str]:
    draft = state["draft"]
    errors = layout_errors(draft["layout"])
    if not draft["name"]:
        errors.insert(0, _("Give the screen a name."))
    if errors:
        return errors
    screen.name, screen.background, screen.public, screen.layout = draft["name"], draft["bg"], draft["public"], draft["layout"]
    screen.sessions = draft["sessions"]
    screen.audio = [{"id": c["id"], "widget": c["widget"], "params": c.get("params", {})} for c in draft["audio"]
                    if c.get("widget") in registry.audio()]
    screen.popups = [{"widget": p["widget"], "params": _changed(registry.popups()[p["widget"]], clean_params(registry.popups()[p["widget"]], p.get("params")))}
                     for p in draft["popups"] if p.get("widget") in registry.popups()]
    if orient_of(screen) != draft["orient"]:
        screen.width, screen.height = SIZES[draft["orient"]]
    screen.save()
    return []


# ---------- what the pages show ----------

def boxes(tree, width, height, x=0.0, y=0.0, scale=1.0):
    """[(x, y, w, h, node)] for the leaves (and empty slots) of a tree laid out in width × height, design px × scale."""
    if not isinstance(tree, dict):
        return [(x, y, width, height, None)]
    widget = registry.get(tree.get("widget"))
    if widget is None or not widget.slots:
        return [(x, y, width, height, tree)]
    params = clean_params(widget, tree.get("params"))
    count = params[widget.slots]
    weights = list(params.get("weights") or []) + [1] * count
    children = (tree.get("children") or []) + [None] * count
    gap = params.get("gap", 0) * scale
    rows = tree["widget"] == "rows"
    span = (height if rows else width) - gap * (count - 1)
    fixed = sum(_num(w[:-2]) * scale for w in weights[:count] if isinstance(w, str) and w.endswith("px"))
    fr = sum(_num(w, 1) for w in weights[:count] if not (isinstance(w, str) and w.endswith("px"))) or 1
    out, off = [], 0.0
    for i in range(count):
        w = weights[i]
        length = _num(w[:-2]) * scale if isinstance(w, str) and w.endswith("px") else max(span - fixed, 0) * _num(w, 1) / fr
        if rows:
            out += boxes(children[i], width, length, x, y + off, scale)
        else:
            out += boxes(children[i], length, height, x + off, y, scale)
        off += length + gap
    return out


def thumb(screen, box_w=300, box_h=170) -> dict:
    """The layout as coloured blocks, for the list's cards."""
    width, height = SIZES.get(orient_of(screen))
    scale = min(box_w / width, box_h / height)
    w, h = width * scale, height * scale
    pad = screen.padding * scale
    rects = []
    for x, y, bw, bh, node in boxes(screen.layout, w - 2 * pad, h - 2 * pad, pad, pad, scale):
        widget = registry.get(node.get("widget")) if node else None
        rects.append({"x": round(x + 2, 1), "y": round(y + 2, 1), "w": round(max(bw - 4, 0), 1), "h": round(max(bh - 4, 0), 1),
                      "color": widget.color if widget else "", "empty": node is None})
    return {"w": round(w), "h": round(h), "bg": screen.background, "rects": rects}


def list_page() -> dict:
    cards = []
    for screen in Screen.objects.all():
        nodes = [n for _p, n, _d in walk(screen.layout)] if screen.layout else []
        widgets = sum(1 for n in nodes if n and (w := registry.get(n.get("widget"))) and not w.slots)
        cards.append({"s": screen, "thumb": thumb(screen), "orient": _("Landscape") if orient_of(screen) == "land" else _("Portrait"),
                      "chips": [ngettext("%(n)d widget", "%(n)d widgets", widgets) % {"n": widgets},
                                ngettext("%(n)d pop-up", "%(n)d pop-ups", len(screen.popups or [])) % {"n": len(screen.popups or [])},
                                (ngettext("%(n)d audio", "%(n)d audio", len(screen.audio or [])) % {"n": len(screen.audio or [])}) if screen.audio else _("Silent")],
                      "where": _("Anyone with the link") if screen.public else _("Staff only")})
    return {"cards": cards}


def _field(name, param, value, source_options):
    """How the editor shows one param."""
    f = {"name": name, "label": param.label or name.replace("_", " ").capitalize(), "help": param.help, "value": value,
         "unit": param.unit}
    if param.source:
        f.update(control="select", options=source_options.get(param.source, []))
    elif param.type == "bool":
        f["control"] = "toggle"
    elif param.type == "choice":
        options = [(o, str(o).replace("_", " ").capitalize()) for o in param.options]
        f.update(control="seg" if len(options) <= 3 else "select", options=options)
    elif param.type in ("int", "number"):
        if param.control == "slider" and param.min is not None and param.max is not None:
            f.update(control="slider", min=param.min, max=param.max, step=param.step or 1)
        else:
            f.update(control="number", less_off=param.min is not None and value <= param.min,
                     more_off=param.max is not None and value >= param.max)
    elif param.type == "color":
        f.update(control="color", presets=list(param.presets))
    elif param.type == "image":
        f["control"] = "image"
    elif param.type == "text":
        f["control"] = "area"
    elif param.type in ("list", "size"):
        f.update(control="text", value=json.dumps(value) if param.type == "list" else value)
    else:
        f["control"] = "text"
    return f


def source_options(draft) -> dict:
    from pos_server.models import Dish, Menu
    out = {"menus": [(0, _("Pick a menu"))] + [(m.id, m.title) for m in Menu.objects.all().order_by("position", "id")],
           "dishes": [(0, _("Pick a dish"))] + [(d.id, d.title) for d in Dish.objects.filter(archived=False).order_by("title")]}
    from audio.models import AudioChannel
    for kind in ("music", "voice"):
        store = list(AudioChannel.objects.filter(kind=kind, archived=False).values_list("id", "name"))
        out[f"channels:{kind}"] = [(0, _("Pick a channel") if store else _("No channels yet: add one in Store › Audio channels"))] + store
        channels = [(c["id"], _channel_name(c)) for c in draft.get("audio", []) if c.get("widget") == f"{kind}_channel"]
        none = _("None") if channels else (_("No music channels on this screen yet") if kind == "music" else _("No voice channels on this screen yet"))
        out[f"audio:{kind}"] = [("", none)] + channels
    return out


def _channel_name(entry):
    """The store channel a screen's audio entry plays, by name."""
    from audio.models import AudioChannel
    channel = AudioChannel.objects.filter(pk=(entry.get("params") or {}).get("channel") or 0).first()
    return channel.name if channel else str(_("No channel picked"))


def _mix(channels):
    """The "How they mix" chart: a minute of each channel as bars; lower channels drop while a voice above speaks."""
    import math
    lanes, speaking = [], [False] * 40
    for i, (c, widget) in enumerate(channels):
        voice = widget.channel == "voice"
        keep = clean_params(widget, c.get("params"))["keep"]
        bars = []
        for k in range(40):
            if voice:
                on = any(a <= (k + i * 7) % 40 < a + 5 for a in (6, 25))
                bars.append({"h": round(55 + 40 * abs(math.sin(k * 1.3))) if on else 8, "kind": "voice" if on else "quiet"})
            else:
                h = round(40 + 50 * abs(math.sin(k * 0.6 + i)))
                ducked = i > 0 and speaking[k]
                bars.append({"h": max(8, round(h * keep / 100)) if ducked else h, "kind": "ducked" if ducked else "music"})
        if voice:
            speaking = [s or b["kind"] == "voice" for s, b in zip(speaking, bars)]
        lanes.append({"name": _channel_name(c), "bars": bars})
    return lanes


def editor_page(screen, state) -> dict:
    draft, sel = state["draft"], state["sel"]
    tree = draft["layout"]
    node = node_at(tree, sel) if sel != "screen" else False
    if node is False and sel != "screen":
        sel = state["sel"] = "screen"
    widget = registry.get(node.get("widget")) if isinstance(node, dict) else None
    dirty = is_dirty(screen, state)
    errors = layout_errors(tree)

    # breadcrumbs and the tree
    crumbs = [{"sel": "screen", "label": draft["name"] or _("Screen"), "on": sel == "screen"}]
    if sel != "screen":
        steps = sel.split("-")
        for k in range(1, len(steps) + 1):
            p = "-".join(steps[:k])
            crumbs.append({"sel": p, "label": label(node_at(tree, p)), "on": p == sel})
    rows = [{"sel": "screen", "label": draft["name"] or _("Screen"), "depth": 0, "icon": "M3 5h18v12H3z M8 21h8 M12 17v4",
             "meta": _("Landscape") if draft["orient"] == "land" else _("Portrait"), "on": sel == "screen", "empty": False}]
    for p, n, depth in walk(tree):
        w = registry.get(n.get("widget")) if n else None
        meta = ""
        if w and w.slots:
            meta = ngettext("%(n)d slot", "%(n)d slots", clean_params(w, n.get("params"))[w.slots]) % {"n": clean_params(w, n.get("params"))[w.slots]}
        rows.append({"sel": p, "label": label(n), "depth": depth + 1, "icon": w.icon if w else "M12 5v14M5 12h14", "color": w.color if w else "",
                     "meta": meta, "on": p == sel, "empty": n is None})

    # the panel
    panel = {}
    if sel == "screen":
        panel = {"kind": "screen", "orients": [("land", _("Landscape")), ("port", _("Portrait"))], "bgs": BACKGROUNDS,
                 "sessions": list(screen.screen_sessions.all())}
    elif node is None:
        lib = [{"name": n, "w": w} for n, w in registry.widgets().items()]
        panel = {"kind": "slot", "groups": [(_("Layout"), [x for x in lib if x["w"].slots]), (_("Widgets"), [x for x in lib if not x["w"].slots])]}
    elif widget and widget.slots:
        params = clean_params(widget, node.get("params"))
        count = params[widget.slots]
        weights = list(params.get("weights") or []) + [1] * count
        children = (node.get("children") or []) + [None] * count
        rows_ = node["widget"] == "rows"
        fr_total = sum(_num(w, 1) for w in weights[:count] if not (isinstance(w, str) and w.endswith("px"))) or 1
        parts = []
        for i in range(count):
            w = weights[i]
            px = isinstance(w, str) and w.endswith("px")
            c = children[i]
            cw = registry.get(c.get("widget")) if c else None
            parts.append({"i": i, "label": (_("Row %(n)d") if rows_ else _("Column %(n)d")) % {"n": i + 1} + " · " + label(c),
                          "sel": f"{sel}-{i}", "size": w if px else round(_num(w, 1), 2), "px": px,
                          "share": 0 if px else round(_num(w, 1) / fr_total * 100),
                          "color": "#C9D3CD" if c is None else "#5B6A62" if cw and cw.slots else (cw.color if cw else "#9AA8A1"),
                          "less_off": (not px and _num(w, 1) <= 1) or (px and _num(w[:-2]) <= 20), "more_off": not px and _num(w, 1) >= 8})
        empty = sum(1 for c in children[:count] if c is None)
        panel = {"kind": "group", "rows": rows_, "count": count, "parts": parts, "gap": params.get("gap", 24),
                 "less_off": count <= 1, "more_off": count >= 6,
                 "warn": ngettext("%(n)d empty slot shows as blank space on the screen.", "%(n)d empty slots show as blank space on the screen.", empty) % {"n": empty} if empty else ""}
    elif widget:
        params = clean_params(widget, node.get("params"))
        options = source_options(draft)
        panel = {"kind": "widget", "w": widget, "fields": [_field(n, p, params[n], options) for n, p in widget.params.items()]}
    else:
        panel = {"kind": "missing"}

    # pop-ups
    options = source_options(draft)
    pops = []
    for i, spec in enumerate(draft["popups"]):
        w = registry.popups().get(spec.get("widget"))
        if w is None:
            continue
        params = clean_params(w, spec.get("params"))
        pops.append({"i": i, "w": w, "open": state["open_pop"] == i,
                     "fields": [_field(n, p, params[n], options) for n, p in w.params.items()] if state["open_pop"] == i else []})
    taken = {p.get("widget") for p in draft["popups"]}
    pop_lib = [{"name": n, "w": w, "added": n in taken} for n, w in registry.popups().items()]
    # audio channels
    channels = [(c, registry.audio()[c["widget"]]) for c in draft["audio"] if c.get("widget") in registry.audio()]
    users = {}
    for _p, n, _d in walk(tree):
        if n and isinstance(n.get("params"), dict) and n["params"].get("channel"):
            users.setdefault(n["params"]["channel"], []).append(label(n))
    for p in draft["popups"]:
        w = registry.popups().get(p.get("widget"))
        if w and (p.get("params") or {}).get("channel"):
            users.setdefault(p["params"]["channel"], []).append(w.title)
    chans = []
    for i, (c, w) in enumerate(channels):
        params = clean_params(w, c.get("params"))
        used = users.get(c["id"], [])
        chans.append({"i": i, "c": c, "w": w, "p": params, "n": i + 1, "top": i == 0, "last": i == len(channels) - 1,
                      "name": _channel_name(c), "options": options[f"channels:{w.channel}"],
                      "keep_example": _("%(name)s at %(n)d%% means it’s still there, just softer, while an order is called.") % {"name": _channel_name(c), "n": params["keep"]}
                      if w.channel == "music" else _("At 100% it keeps playing at full volume."),
                      "used": (_("Used by %(what)s") % {"what": ", ".join(used)}) if used else _("Not used by any widget or pop-up yet.")})

    tab = state["tab"]
    editing = f"pop:{state['open_pop']}" if tab == "popups" and state["open_pop"] is not None else (sel if isinstance(node, dict) else "")

    counted = [n for _p, n, _d in walk(tree)]
    n_widgets = sum(1 for n in counted if n and (w := registry.get(n.get("widget"))) and not w.slots)
    width, height = SIZES[draft["orient"]]
    return {
        "screen": screen, "d": draft, "sel": sel, "crumbs": crumbs, "tree": rows, "panel": panel,
        "dirty": dirty, "errors": errors, "can_undo": bool(state["hist"]), "can_save": dirty and not errors and bool(draft["name"]),
        "status": errors[0] if errors else _("Unsaved changes") if dirty else _("All changes saved"),
        "status_kind": "bad" if errors else "dirty" if dirty else "",
        # the preview frame keeps one address; what it should show is told to it (admin_portal/static/admin_portal/screens.js)
        "preview_base": reverse("admin_screen_preview", args=[screen.id]),
        "preview": reverse("admin_screen_preview", args=[screen.id]) + f"?sel={sel if tab == 'layout' else ''}"
                   + (f"&popup={state['pop_preview']}" if state["pop_preview"] else ""),
        "preview_v": hashlib.sha1(json.dumps(draft, sort_keys=True).encode()).hexdigest()[:10],
        "preview_sel": sel if tab == "layout" and sel != "screen" else "", "preview_popup": f"{state['pop_preview']}-{state.get('pop_seq', 0)}" if state["pop_preview"] else "",
        "ratio": f"{width} / {height}", "orient_label": _("Landscape 16:9") if draft["orient"] == "land" else _("Portrait 9:16"),
        "editing": editing, "n_widgets": n_widgets, "tab": tab, "pops": pops, "pop_lib": pop_lib,
        "tabs": [("layout", _("Layout"), n_widgets), ("popups", _("Pop-ups"), len(pops)), ("audio", _("Audio"), len(chans))],
        "chans": chans, "mix": _mix(channels),
        "address": reverse("screen", args=[screen.slug]),
    }
