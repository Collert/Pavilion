"""
Stations (design: AdminStations): the list in ticket order and the selected station's settings: name, ticket code,
icon, colour, target ticket time and whether it's taking tickets, the dishes made there, a ticket preview, the last
30 days and archiving (its dishes move to another station first). One form with Save: every change (archiving
included) is a draft until it's saved.

    draft = {"rows": [{"id": 3 or "", "name", "tag", "icon", "color", "target", "active"}], "sel": index,
             "gone": [{"id": 4, "to": 3 or "new:<index>"}]}
"""
import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _, ngettext

from pos_server import station_icons
from pos_server.models import Dish, OrderStation, Station

TARGET = (1, 45)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tag(value):
    return "".join(c for c in str(value).upper() if c.isalnum())[:4]


def draft_of() -> dict:
    rows = [{"id": s.id, "name": s.name, "tag": s.ticket_code, "icon": s.icon, "color": s.color, "target": s.target_minutes,
             "active": s.taking_tickets} for s in Station.objects.filter(archived=False)]
    return {"rows": rows, "sel": 0, "gone": []}


def from_post(post) -> dict:
    rows = []
    for k in range(min(_int(post.get("count")), 60)):
        sid, name, tag, icon, color, target, active = (post.get(f"st-{k}-{f}", "") for f in ("id", "name", "tag", "icon", "color", "target", "active"))
        rows.append({"id": _int(sid) or "", "name": name.strip()[:24], "tag": _tag(tag),
                     "icon": icon if icon in station_icons.PATHS else station_icons.DEFAULT,
                     "color": color if len(color) == 7 and color.startswith("#") else station_icons.PALETTE[0][0],
                     "target": min(max(_int(target, 10), TARGET[0]), TARGET[1]), "active": active == "1"})
    gone = []
    for x in post.getlist("gone"):
        sid, _s, to = x.partition(">")
        if _int(sid):
            gone.append({"id": _int(sid), "to": to})
    sel = min(max(_int(post.get("sel")), 0), max(len(rows) - 1, 0))
    return {"rows": rows, "sel": sel, "gone": gone}


def _key(row, k):
    """How a row is pointed at from `gone`: its id, or new:<index> before it's saved."""
    return str(row["id"]) if row["id"] else f"new:{k}"


def apply_op(draft, op, post) -> tuple[dict, str]:
    """Returns the draft and a toast."""
    rows, k = draft["rows"], draft["sel"]
    name, _sep, arg = op.partition(":")
    row = rows[k] if rows else None
    toast = ""
    if name == "pick" and 0 <= _int(arg, -1) < len(rows):
        draft["sel"] = _int(arg)
    elif name in ("up", "down") and 0 <= _int(arg, -1) < len(rows):
        i = _int(arg)
        j = i - 1 if name == "up" else i + 1
        if 0 <= j < len(rows):
            rows[i], rows[j] = rows[j], rows[i]
            _fix_new_refs(draft, {i: j, j: i})
            if draft["sel"] in (i, j):
                draft["sel"] = j if draft["sel"] == i else i
    elif name == "new":
        used = {r["color"] for r in rows}
        color = next((c for c, _l in station_icons.PALETTE if c not in used), station_icons.PALETTE[0][0])
        rows.append({"id": "", "name": _("New station"), "tag": "NEW", "icon": "knife", "color": color, "target": 8, "active": True})
        draft["sel"] = len(rows) - 1
    elif row and name == "icon" and arg in station_icons.PATHS:
        row["icon"] = arg
    elif row and name == "color" and len(arg) == 7:
        row["color"] = arg
    elif row and name in ("less", "more"):
        row["target"] = min(max(row["target"] + (1 if name == "more" else -1), TARGET[0]), TARGET[1])
    elif row and name == "active":
        row["active"] = not row["active"]
    elif row and name == "archive" and len(rows) > 1:
        dishes = dish_ids(draft).get(_key(row, k), [])
        target = post.get("move_to", "")
        others = [(_key(r, i), r) for i, r in enumerate(rows) if i != k]
        to = next((r for key, r in others if key == target), None) or others[0][1]
        to_key = next(key for key, r in others if r is to)
        if row["id"]:
            draft["gone"].append({"id": row["id"], "to": to_key})
        # stations archived earlier that moved their dishes here now send them on
        for g in draft["gone"]:
            if g["to"] == _key(row, k):
                g["to"] = to_key
        rows.pop(k)
        _fix_new_refs(draft, {i: i - 1 for i in range(k + 1, len(rows) + 1)})
        draft["sel"] = 0
        toast = (ngettext("%(name)s archived. %(n)d dish moved to %(to)s. Save to keep it.",
                          "%(name)s archived. %(n)d dishes moved to %(to)s. Save to keep it.", len(dishes))
                 % {"name": row["name"], "n": len(dishes), "to": to["name"]}) if dishes else _("%(name)s archived. Save to keep it.") % {"name": row["name"]}
    return draft, toast


def _fix_new_refs(draft, moved):
    """Rows that aren't saved yet are pointed at by position: follow them when rows move."""
    for g in draft["gone"]:
        if g["to"].startswith("new:") and _int(g["to"][4:], -1) in moved:
            g["to"] = f"new:{moved[_int(g['to'][4:])]}"


def dish_ids(draft) -> dict:
    """{row key: [dish id]} as the draft would leave them (archived stations' dishes moved)."""
    by_station = {}
    for dish_id, station_id in Dish.objects.filter(archived=False).values_list("id", "station_id"):
        by_station.setdefault(str(station_id), []).append(dish_id)
    out = {_key(r, i): list(by_station.get(str(r["id"]), [])) if r["id"] else [] for i, r in enumerate(draft["rows"])}
    for g in draft["gone"]:
        out.setdefault(g["to"], []).extend(by_station.get(str(g["id"]), []))
    return out


def problems(draft) -> list[str]:
    out = []
    names = [r["name"].strip().lower() for r in draft["rows"]]
    tags = [r["tag"] for r in draft["rows"]]
    if any(not n or names.count(n) > 1 for n in names) or any(not t or tags.count(t) > 1 for t in tags):
        out.append(_("Each station needs its own name and code"))
    if not draft["rows"]:
        out.append(_("Keep at least one station."))
    return out


def row_problem(draft, k) -> str:
    r = draft["rows"][k]
    others = [x for i, x in enumerate(draft["rows"]) if i != k]
    if not r["name"].strip():
        return _("Give the station a name.")
    if any(x["name"].strip().lower() == r["name"].strip().lower() for x in others):
        return _("Another station already has this name.")
    if not r["tag"]:
        return _("Give it a short ticket code, like GRL.")
    if any(x["tag"] == r["tag"] for x in others):
        return _("Another station already uses %(code)s.") % {"code": r["tag"]}
    return ""


def is_dirty(draft) -> bool:
    return [dict(r) for r in draft["rows"]] != draft_of()["rows"] or bool(draft["gone"])


def _code_for(name):
    base = slugify(name)[:16] or "station"
    code, n = base, 2
    while Station.objects.filter(code=code).exists():
        code, n = f"{base}-{n}", n + 1
    return code


@transaction.atomic
def save(draft):
    saved = {}
    for position, r in enumerate(draft["rows"]):
        values = {"name": r["name"].strip(), "ticket_code": r["tag"], "icon": r["icon"], "color": r["color"],
                  "target_minutes": r["target"], "taking_tickets": r["active"], "position": position}
        station = Station.objects.filter(pk=r["id"], archived=False).first() if r["id"] else None
        if station is None:
            station = Station.objects.create(code=_code_for(r["name"]), **values)
        else:
            Station.objects.filter(pk=station.pk).update(**values)
        saved[_key(r, position)] = station
    for g in draft["gone"]:
        to = saved.get(g["to"])
        if to is None:
            continue
        Dish.objects.filter(station_id=g["id"]).update(station=to)
        Station.objects.filter(pk=g["id"]).update(archived=True, taking_tickets=False)


def _stats(station_id, target):
    since = timezone.now() - datetime.timedelta(days=30)
    rows = OrderStation.objects.filter(station_id=station_id, order__timestamp__gte=since)
    count = rows.count()
    times = [(b - a).total_seconds() / 60 for a, b in rows.filter(confirmed_at__isnull=False, ready_at__isnull=False)
             .values_list("confirmed_at", "ready_at") if b >= a]
    avg = sum(times) / len(times) if times else None
    red = sum(1 for t in times if t > target * 2) / len(times) if times else None
    return [
        {"value": f"{count:,}", "label": _("Tickets"), "tone": ""},
        {"value": _("%(n).1f min") % {"n": avg} if avg is not None else "—", "label": _("Average ticket time"),
         "tone": "bad" if avg is not None and avg > target else ""},
        {"value": (_("On target") if avg <= target else _("+%(n).1f min") % {"n": avg - target}) if avg is not None else "—",
         "label": _("Against target"), "tone": "bad" if avg is not None and avg > target else "good" if avg is not None else ""},
        {"value": f"{round(red * 100)}%" if red is not None else "—", "label": _("Tickets that went red"), "tone": ""},
    ]


def page(draft, move_to="") -> dict:
    rows = draft["rows"]
    k = draft["sel"]
    dishes = dish_ids(draft)
    titles = dict(Dish.objects.filter(archived=False).values_list("id", "title"))
    counts = {key: len(ids) for key, ids in dishes.items()}
    errs = problems(draft)
    dirty = is_dirty(draft)
    if errs:
        status, kind = errs[0], "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    items = []
    for i, r in enumerate(rows):
        n = counts.get(_key(r, i), 0)
        items.append({**r, "k": i, "on": i == k, "path": station_icons.path(r["icon"]), "first": i == 0, "last": i == len(rows) - 1,
                      "sub": ngettext("%(n)d dish", "%(n)d dishes", n) % {"n": n} + " · " + _("%(t)d min target") % {"t": r["target"]}})
    e = None
    if rows:
        r = rows[k]
        key = _key(r, k)
        names = sorted(titles.get(d, "") for d in dishes.get(key, []))
        clash = next((x for i, x in enumerate(rows) if i != k and x["color"] == r["color"]), None)
        used = {x["color"]: x["name"] for i, x in enumerate(rows) if i != k}
        others = [(_key(x, i), x["name"]) for i, x in enumerate(rows) if i != k]
        n = len(names)
        e = {
            "r": r, "k": k, "key": key, "path": station_icons.path(r["icon"]), "problem": row_problem(draft, k),
            "icons": [{"key": key_, "label": label, "path": path, "on": key_ == r["icon"]} for key_, label, path in station_icons.ICONS],
            "colors": [{"hex": hex_, "label": label + (" · " + _("used by %(name)s") % {"name": used[hex_]} if hex_ in used else ""),
                        "on": hex_ == r["color"], "taken": hex_ in used and hex_ != r["color"]} for hex_, label in station_icons.PALETTE],
            "color_hint": _("Heads up: %(name)s uses this color too, so the two will be hard to tell apart on screens and reports.") % {"name": clash["name"]}
            if clash else _("Used on kitchen screens, tickets, the order marking screen and in reports. Colors with a ring are taken."),
            "active_hint": _("On. Turn off to mark its dishes sold out, e.g. when the fryer is down.") if r["active"]
            else ngettext("Off. Its %(n)d dish shows as sold out until you turn it back on.", "Off. Its %(n)d dishes show as sold out until you turn it back on.", n) % {"n": n},
            "dishes": names, "dish_head": ngettext("%(n)d dish is made here", "%(n)d dishes are made here", n) % {"n": n},
            "dish_sub": _("Each dish belongs to one station. Change it on the dish.") if n else _("No dishes yet. Pick this station when you edit a dish."),
            "ticket": {"tag": f"{r['tag'] or '—'} · {r['name'] or _('Station')}",
                       "lines": [("2×" if j == 0 else "1×", d) for j, d in enumerate(names[:2])] or [("1×", _("Dish name"))],
                       "timer": f"{max(1, r['target'] - 2)}:40",
                       "note": _("Turns amber at %(a)d min and red at %(b)d min.") % {"a": r["target"], "b": r["target"] * 2}},
            "stats": _stats(r["id"], r["target"]) if r["id"] else _stats(None, r["target"]),
            "moves": [{"key": key_, "name": name, "on": key_ == move_to} for key_, name in others],
            "move_label": ngettext("Move its %(n)d dish to", "Move its %(n)d dishes to", n) % {"n": n}, "n": n,
            "archive_off": len(rows) <= 1,
            "archive_text": _("Move dishes and archive %(name)s") % {"name": r["name"] or _("station")} if n else _("Archive %(name)s") % {"name": r["name"] or _("station")},
        }
    return {"d": draft, "items": items, "count": len(rows), "e": e, "status": status, "status_kind": kind, "can_save": dirty and not errs,
            "gone": [f"{g['id']}>{g['to']}" for g in draft["gone"]]}
