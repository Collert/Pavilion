"""
Menus (design: AdminMenus, list + items, schedule and display tabs). Schedules are worked out in
pos_server.menu_schedule; menu boards (the display tab) in screens.menu_boards.

The editor is one form with Save, like the dish editor. Its whole state is a draft carried in a hidden `state` field
(JSON, see `draft_from_menu`); the fields of the tab on screen are merged into it (`merge_post`), the button pressed
(`op`) changes it (`apply_op`), and nothing is written until Save (`save_draft`).
"""
import datetime
import json

from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from pos_server.menu_schedule import Schedule, days_text, fmt_minutes, minute_of_week
from pos_server.models import Combo, Dish, Menu, MenuRule, Station
from screens import menu_boards
from screens.layout import check_value

STATUS = {"draft": ("Draft", "draft"), "live": ("Live now", "live"), "scheduled": ("Scheduled", "scheduled"), "off": ("Off", "off")}
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"]
TABS = ("items", "schedule", "display")
FX = [("cut", "Cut"), ("fade", "Fade"), ("slide", "Slide"), ("rise", "Rise")]


def status(menu, schedule, now=None):
    if menu.draft:
        key = "draft"
    elif schedule.is_live(menu, now):
        key = "live"
    elif menu.auto:
        key = "scheduled"
    else:
        key = "off"
    return {"key": key, "label": _(STATUS[key][0])}


def rule_text(rule, menus_by_id):
    if rule.type == MenuRule.WEEKLY:
        return f"{days_text(rule.day_list)} {fmt_minutes(rule.time.hour * 60 + rule.time.minute) if rule.time else '…'}"
    if rule.type == MenuRule.DATES:
        fmt = lambda d: date_format(d, "M j") if d else "…"  # noqa: E731
        return f"{fmt(rule.start_date)} – {fmt(rule.end_date)}"
    other = menus_by_id.get(rule.other_menu_id)
    return _("when %(menu)s starts") % {"menu": other.title if other else _("another menu")}


def rule_chips(menu, schedule):
    if not menu.auto:
        return [{"text": _("Switched by hand"), "kind": "hand"}]
    chips = []
    for rule in schedule.rules.get(menu.id, []):
        prefix = _("On") if rule.kind == MenuRule.ON else _("Off")
        chips.append({"text": f"{prefix} {rule_text(rule, schedule.by_id)}", "kind": rule.kind, "dates": rule.type == MenuRule.DATES})
    return chips


def groups_of(menu, stations):
    station_ids = set(menu.dishes.values_list("station", flat=True))
    names = [s.name for s in stations if s.id in station_ids]
    if menu.combos.exists():
        names.append(_("Combos"))
    return " · ".join(names)


def display_of(menu):
    """The menu's board template, for the list's Display column."""
    template = menu_boards.template_of(menu.display or {})
    if not template:
        return None
    return {"name": template.title, "orientation": template.layout["orientation"], "look": menu_boards.clean(menu.display)["look"]}


def menu_list(now=None):
    now = now or timezone.now()
    menus = list(Menu.objects.all())
    schedule = Schedule(menus)
    stations = list(Station.objects.all())
    live = schedule.live(now)
    rows = []
    for i, menu in enumerate(menus):
        st = status(menu, schedule, now)
        if menu.draft:
            sub = _("Not published")
        elif menu.auto:
            sub = schedule.next_change(menu, now)
        else:
            sub = _("Turned on by hand") if menu.manual_on else _("Turn on by hand")
        rows.append({"menu": menu, "status": st, "sub": sub, "rules": rule_chips(menu, schedule), "groups": groups_of(menu, stations),
                     "count": menu.dishes.count() + menu.combos.count(), "first": i == 0, "last": i == len(menus) - 1,
                     "display": display_of(menu)})
    banner = None
    if live:
        extra = len(live) - 1
        banner = {"name": live[0].title, "more": ngettext("%(n)d more live underneath", "%(n)d more live underneath", extra) % {"n": extra} if extra else "",
                  "next": schedule.next_change(live[0], now) if live[0].auto else ""}
    return {"rows": rows, "banner": banner}


# ---------- the editor's draft ----------

def _rule_dict(rule):
    return {"kind": rule.kind, "type": rule.type, "days": rule.days, "time": rule.time.strftime("%H:%M") if rule.time else "",
            "start": rule.start_date.isoformat() if rule.start_date else "", "end": rule.end_date.isoformat() if rule.end_date else "",
            "other": rule.other_menu_id or ""}


def draft_from_menu(menu):
    return {
        "title": menu.title, "auto": menu.auto, "manual_on": menu.manual_on,
        "dishes": sorted(menu.dishes.values_list("id", flat=True)), "combos": sorted(menu.combos.values_list("id", flat=True)),
        "rules": [_rule_dict(r) for r in menu.rules.all()],
        "display": menu_boards.clean(menu.display, menu),
        "ui": {"tab": "items", "group": "all", "q": "", "page": 0},
    }


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_state(post, menu):
    """The draft carried in the form's `state` field (the saved menu's when it's missing or broken)."""
    try:
        state = json.loads(post.get("state", ""))
    except ValueError:
        state = None
    base = draft_from_menu(menu)
    if not isinstance(state, dict):
        return base
    draft = {**base, **{k: state[k] for k in base if k in state}}
    draft["dishes"] = sorted({i for i in (_int(x) for x in draft["dishes"] or []) if i})
    draft["combos"] = sorted({i for i in (_int(x) for x in draft["combos"] or []) if i})
    draft["rules"] = [r for r in draft["rules"] if isinstance(r, dict)][:40]
    draft["auto"], draft["manual_on"] = bool(draft["auto"]), bool(draft["manual_on"])
    draft["display"] = menu_boards.clean(draft["display"], _DraftMenu(draft, menu))
    ui = draft["ui"] if isinstance(draft["ui"], dict) else {}
    draft["ui"] = {"tab": ui.get("tab") if ui.get("tab") in TABS else "items", "group": str(ui.get("group") or "all"),
                   "q": str(ui.get("q") or "")[:80], "page": _int(ui.get("page"), 0)}
    return draft


def _look_value(param, raw):
    """A posted look field as the param's type (None when it doesn't fit)."""
    if param.type == "bool":
        return raw is not None
    if raw is None:
        return None
    if param.type == "int":
        value = _int(raw)
    elif param.type == "number":
        try:
            value = float(raw)
        except ValueError:
            return None
    else:
        value = raw
    return None if check_value(param, value) else value


def merge_post(draft, post, menu):
    """Take the fields of the tab that was on screen into the draft."""
    if "title" in post:
        draft["title"] = post.get("title", "")[:140]
    tab = draft["ui"]["tab"]
    if tab == "items" and "q" in post:
        draft["ui"]["q"] = post.get("q", "")[:80]
    elif tab == "schedule":
        for i, rule in enumerate(draft["rules"]):
            for field in ("time", "start", "end", "other"):
                if f"rule-{i}-{field}" in post:
                    rule[field] = post.get(f"rule-{i}-{field}", "")
    elif tab == "display":
        d = draft["display"]
        if post.get("template") in menu_boards.templates():
            d["template"] = post["template"]
        page = d["pages"][min(draft["ui"]["page"], len(d["pages"]) - 1)]
        if "featured" in post:
            page["featured"] = post.get("featured", "")
        for k in range(len(page["slots"])):
            if f"slot-{k}" in post:
                page["slots"][k] = post.get(f"slot-{k}", "")
        for key in ("sold", "page_fx", "group_fx"):
            if key in post:
                d[key] = post[key]
        for key in ("page_sec", "group_sec"):
            if key in post:
                d[key] = _int(post[key], d[key])
        template = menu_boards.template_of(d)
        if template and "look-present" in post:
            for name, param in template.params.items():
                value = _look_value(param, post.get(f"look-{name}"))
                if value is not None:
                    d["look"][name] = value
        draft["display"] = menu_boards.clean(d, _DraftMenu(draft, menu))
    return draft


def apply_op(draft, op, post, menu):
    """Tabs, toggles, rules, pages and steppers."""
    name, _sep, arg = (op or "").partition(":")
    args = arg.split(":") if arg else []
    ui, d = draft["ui"], draft["display"]
    if name == "tab" and args and args[0] in TABS:
        ui["tab"] = args[0]
    elif name == "group" and args:
        ui["group"] = args[0]
    elif name == "toggle" and len(args) == 2:
        key = "combos" if args[0] == "c" else "dishes"
        item = _int(args[1])
        if item:
            draft[key] = sorted(set(draft[key]) ^ {item})
    elif name in ("add_shown", "remove_shown"):
        c = catalog(draft, ui["group"], ui["q"])
        for row in c["rows"]:
            key = "combos" if row["kind"] == "combo" else "dishes"
            ids = set(draft[key])
            (ids.add if name == "add_shown" else ids.discard)(row["obj"].id)
            draft[key] = sorted(ids)
    elif name == "auto":
        draft["auto"] = not draft["auto"]
        if draft["auto"] and not draft["rules"]:  # a starting point, like the design
            draft["rules"] = [{"kind": "on", "type": "weekly", "days": "01234", "time": "11:00", "start": "", "end": "", "other": ""},
                              {"kind": "off", "type": "weekly", "days": "01234", "time": "15:00", "start": "", "end": "", "other": ""}]
    elif name == "manual":
        draft["manual_on"] = not draft["manual_on"]
    elif name == "add_rule" and len(args) == 2:
        kind = "off" if args[0] == "off" else "on"
        today = timezone.localdate()
        rule = {"kind": kind, "type": "weekly", "days": "01234", "time": "11:00" if kind == "on" else "15:00", "start": "", "end": "", "other": ""}
        if args[1] == "dates":
            rule.update(type="dates", days="", time="", start=(today + datetime.timedelta(days=7)).isoformat(),
                        end=(today + datetime.timedelta(days=9)).isoformat())
        elif args[1] == "menu" and kind == "off":
            other = Menu.objects.exclude(pk=menu.pk).first()
            rule.update(type="menu", days="", time="", other=other.id if other else "")
        draft["rules"].append(rule)
    elif name == "remove_rule" and args and _int(args[0]) is not None and 0 <= _int(args[0]) < len(draft["rules"]):
        draft["rules"].pop(_int(args[0]))
    elif name == "day" and len(args) == 2:
        i, day = _int(args[0]), _int(args[1])
        if i is not None and 0 <= i < len(draft["rules"]) and day is not None and 0 <= day < 7:
            days = {int(c) for c in draft["rules"][i]["days"] if c.isdigit()} ^ {day}
            draft["rules"][i]["days"] = "".join(str(x) for x in sorted(days))
    elif name == "page" and args:
        ui["page"] = max(0, min(_int(args[0], 0), len(d["pages"]) - 1))
    elif name == "add_page":
        template = menu_boards.template_of(d)
        d["pages"].append({"featured": "", "slots": [""] * (template.layout["groups"] if template else 3)})
        ui["page"] = len(d["pages"]) - 1
    elif name == "remove_page" and len(d["pages"]) > 1:
        d["pages"].pop(min(ui["page"], len(d["pages"]) - 1))
        ui["page"] = max(0, ui["page"] - 1)
    elif name == "sec" and len(args) == 2 and args[0] in ("page", "group"):
        d[f"{args[0]}_sec"] = d[f"{args[0]}_sec"] + (_int(args[1]) or 0)
    draft["display"] = menu_boards.clean(d, _DraftMenu(draft, menu))
    ui["page"] = max(0, min(ui["page"], len(draft["display"]["pages"]) - 1))
    return draft


def _rules(draft, menu):
    """The draft's rules as unsaved MenuRules (bad times and dates left empty)."""
    out = []
    for r in draft["rules"]:
        rule = MenuRule(menu_id=menu.id, kind="off" if r.get("kind") == "off" else "on",
                        type=r.get("type") if r.get("type") in ("weekly", "dates", "menu") else "weekly",
                        days="".join(c for c in str(r.get("days", "")) if c in "0123456"))
        try:
            rule.time = datetime.time.fromisoformat(r.get("time") or "") if r.get("time") else None
        except ValueError:
            rule.time = None
        for field, key in (("start_date", "start"), ("end_date", "end")):
            try:
                setattr(rule, field, datetime.date.fromisoformat(r.get(key) or "") if r.get(key) else None)
            except ValueError:
                setattr(rule, field, None)
        if rule.start_date and rule.end_date and rule.start_date > rule.end_date:
            rule.start_date, rule.end_date = rule.end_date, rule.start_date
        rule.other_menu_id = _int(r.get("other"))
        out.append(rule)
    return out


def draft_schedule(draft, menu):
    """A Schedule of every menu where this one is the draft: (schedule, the draft as an unsaved Menu)."""
    copy = Menu(pk=menu.pk, title=draft["title"] or menu.title, auto=draft["auto"], manual_on=draft["manual_on"],
                position=menu.position, draft=menu.draft)
    menus = [copy if m.pk == menu.pk else m for m in Menu.objects.all()]
    schedule = Schedule(menus)
    schedule.rules[menu.pk] = _rules(draft, menu)
    return schedule, copy


def _canonical(draft):
    return json.dumps({k: v for k, v in draft.items() if k != "ui"} | {"title": draft["title"].strip()}, sort_keys=True, default=str)


def is_dirty(draft, menu):
    return _canonical(draft) != _canonical(draft_from_menu(menu))


def problems(draft, menu):
    out = []
    if not draft["title"].strip():
        out.append(_("Give the menu a name"))
    if menu_boards.problems(draft["display"], _DraftMenu(draft, menu)):
        out.append(_("Fix the timing before saving"))
    return out


@transaction.atomic
def save_draft(draft, menu):
    menu.title = draft["title"].strip()[:140]
    menu.auto, menu.manual_on, menu.draft = draft["auto"], draft["manual_on"], False
    menu.display = {k: v for k, v in draft["display"].items()}
    menu.save()
    menu.rules.all().delete()
    for rule in _rules(draft, menu):
        rule.pk = None
        if rule.type == MenuRule.MENU and not Menu.objects.filter(pk=rule.other_menu_id).exclude(pk=menu.pk).exists():
            continue
        rule.save()
    menu.dishes.set(Dish.objects.filter(pk__in=draft["dishes"], archived=False))
    menu.combos.set(Combo.objects.filter(pk__in=draft["combos"], archived=False))
    return menu


# ---------- tabs ----------

def catalog(draft, group="all", query=""):
    """Every dish and combo, with whether it's in the draft, filtered by station (or combos) and a search."""
    stations = list(Station.objects.all())
    groups = [{"key": "all", "label": _("All"), "color": None}] + \
             [{"key": str(s.id), "label": s.name, "color": s.color} for s in stations] + \
             [{"key": "combos", "label": _("Combos"), "color": "#14261F"}]
    if group not in {g["key"] for g in groups}:
        group = "all"
    on_dishes, on_combos = set(draft["dishes"]), set(draft["combos"])
    q = (query or "").strip()
    rows = []
    if group != "combos":
        dishes = Dish.objects.filter(archived=False).select_related("station").order_by("station__position", "title")
        if group != "all":
            dishes = dishes.filter(station_id=int(group))
        if q:
            dishes = dishes.filter(title__icontains=q)
        rows += [{"kind": "dish", "obj": d, "on": d.id in on_dishes, "color": d.station.color, "group": d.station.name,
                  "price": d.price, "out": not d.available} for d in dishes]
    if group in ("all", "combos"):
        combos = Combo.objects.filter(archived=False).order_by("title")
        if q:
            combos = combos.filter(title__icontains=q)
        rows += [{"kind": "combo", "obj": c, "on": c.id in on_combos, "color": "#14261F", "group": _("Combos"),
                  "price": c.standard_price(), "out": not c.available} for c in combos]
    picked = []
    for s in stations:
        items = list(Dish.objects.filter(pk__in=on_dishes, station=s).order_by("title"))
        if items:
            picked.append({"label": s.name, "color": s.color, "items": [{"kind": "dish", "obj": d, "out": not d.available} for d in items]})
    combos = list(Combo.objects.filter(pk__in=on_combos).order_by("title"))
    if combos:
        picked.append({"label": _("Combos"), "color": "#14261F", "items": [{"kind": "combo", "obj": c, "out": not c.available} for c in combos]})
    return {"groups": groups, "group": group, "query": q, "rows": rows, "picked": picked, "count": len(on_dishes) + len(on_combos),
            "all_shown_on": bool(rows) and all(r["on"] for r in rows)}


def schedule_tab(draft, menu, now=None):
    now = timezone.localtime(now or timezone.now())
    schedule, copy = draft_schedule(draft, menu)
    rules = schedule.rules.get(menu.id, [])
    others_list = [m for m in schedule.menus if m.id != menu.id]

    def rule_row(i, rule):
        row = {"i": i, "rule": rule, "text": rule_text(rule, schedule.by_id)}
        if rule.type == MenuRule.WEEKLY:
            row["label"] = _("Every week · %(text)s") % {"text": row["text"]}
            row["days"] = [{"n": d, "letter": DAY_LETTERS[d], "name": DAY_SHORT[d], "on": d in rule.day_list} for d in range(7)]
        elif rule.type == MenuRule.DATES:
            row["label"] = _("Between dates") if rule.kind == MenuRule.ON else _("Closed between dates")
        else:
            row["label"] = _("When another menu starts")
            row["others"] = others_list
        return row

    rows = [rule_row(i, r) for i, r in enumerate(rules)]
    sets = [
        {"kind": MenuRule.ON, "title": _("Turn on when"), "sub": _("Any one of these switches the menu on"),
         "empty": _("No rules yet, so it never turns on by itself."), "rules": [r for r in rows if r["rule"].kind == MenuRule.ON],
         "adds": [(MenuRule.WEEKLY, _("Weekly time")), (MenuRule.DATES, _("Date range"))]},
        {"kind": MenuRule.OFF, "title": _("Turn off when"), "sub": _("Any one of these switches it off again"),
         "empty": _("No rules yet, so it stays on until someone turns it off."), "rules": [r for r in rows if r["rule"].kind == MenuRule.OFF],
         "adds": [(MenuRule.WEEKLY, _("Weekly time")), (MenuRule.DATES, _("Date range")), (MenuRule.MENU, _("Another menu starts"))]},
    ]

    mine = schedule.segments(copy)
    others = [(m, schedule.segments(m)) for m in others_list if m.auto and not m.draft]
    minute = minute_of_week(now)

    def slices(segs, day, name):
        out = []
        for a, b in segs:
            lo, hi = max(a, day * 1440), min(b, (day + 1) * 1440)
            if hi > lo:
                out.append({"left": round((lo - day * 1440) / 14.4, 2), "width": round((hi - lo) / 14.4, 2),
                            "title": f"{name} · {fmt_minutes(lo % 1440)}–{fmt_minutes(hi % 1440 if hi % 1440 else 1440)}"})
        return out

    week = [{"name": _(DAY_SHORT[d]), "today": d == minute // 1440, "mine": slices(mine, d, copy.title),
             "others": [s for m, segs in others for s in slices(segs, d, m.title)]} for d in range(7)]
    published = Menu(pk=copy.pk, title=copy.title, auto=copy.auto, manual_on=copy.manual_on, position=copy.position)
    live_now = schedule.is_live(published, now)
    next_change = schedule.next_change(copy, now)
    week_note = (_("No weekly times set") if not mine else
                 (_("On now") if live_now else _("Off now")) + (" · " + next_change[0].lower() + next_change[1:] if next_change else ""))
    notes = [_("Also on every day from %(a)s to %(b)s (not shown on the week above)") % {"a": date_format(r.start_date, "M j"), "b": date_format(r.end_date, "M j")}
             for r in schedule.date_rules(copy, "on")]
    notes += [_("Kept off from %(a)s to %(b)s") % {"a": date_format(r.start_date, "M j"), "b": date_format(r.end_date, "M j")}
              for r in schedule.date_rules(copy, "off")]
    order = {m.id: i for i, m in enumerate(schedule.menus)}
    overlaps = []
    for other, segs in others:
        hits = {}
        for a0, a1 in mine:
            for b0, b1 in segs:
                lo, hi = max(a0, b0), min(a1, b1)
                if hi > lo:
                    key = f"{fmt_minutes(lo % 1440)}–{fmt_minutes(hi % 1440 if hi % 1440 else 1440)}"
                    hits.setdefault(key, []).append(_(DAY_SHORT[lo // 1440]))
        for span, days in hits.items():
            first = other.title if order[other.id] < order[menu.id] else copy.title
            overlaps.append(_("Overlaps with %(other)s on %(days)s, %(span)s. Both are live then; %(first)s comes first because it's higher in the list.")
                            % {"other": other.title, "days": ", ".join(days), "span": span, "first": first})
    return {"sets": sets, "week": week, "now_left": round((minute % 1440) / 14.4, 2), "week_note": week_note if copy.auto else "",
            "notes": notes, "overlaps": overlaps[:3], "auto": copy.auto, "manual_on": copy.manual_on, "title": copy.title}


def display_tab(draft, menu, store_name=""):
    d = draft["display"]
    template = menu_boards.template_of(d)
    per = template.layout["per_group"] if template else 6
    page_index = min(draft["ui"]["page"], len(d["pages"]) - 1)
    page = d["pages"][page_index]
    preview_menu = _DraftMenu(draft, menu)
    groups = menu_boards.groups(preview_menu)
    by_group, by_key = menu_boards.items(preview_menu)
    shown = {key: [i for i in by_group.get(key, []) if d["sold"] == "show" or not i["out"]] for key, _n, _c in groups}
    placed = {}
    for n, p in enumerate(d["pages"]):
        for key in p["slots"]:
            if key:
                placed.setdefault(key, []).append(n + 1)

    slots = []
    for k, key in enumerate(page["slots"]):
        count = len(shown.get(key, []))
        screens = menu_boards.screens_of(count, per)
        elsewhere = lambda g: [n for n in placed.get(g, []) if not (n == page_index + 1 and key == g)]  # noqa: E731
        options = [{"key": "", "label": _("— Empty —"), "on": not key}] + [
            {"key": g, "on": g == key, "label": f"{name} · " + ngettext("%(n)d item", "%(n)d items", len(shown.get(g, []))) % {"n": len(shown.get(g, []))}
             + ((" " + _("(also on page %(pages)s)") % {"pages": ", ".join(map(str, elsewhere(g)))}) if elsewhere(g) else "")}
            for g, name, _c in groups]
        note = ""
        if key:
            note = (_("%(n)d items, the slot fits %(per)d. It will rotate through %(s)d screens.") % {"n": count, "per": per, "s": screens}
                    if screens > 1 else ngettext("%(n)d item · fits on one screen", "%(n)d items · fits on one screen", count) % {"n": count})
        slots.append({"k": k, "label": _("Group slot %(n)d") % {"n": k + 1}, "options": options, "note": note, "rotates": screens > 1})
    unplaced = [_("%(group)s (%(n)d items) is not on any page yet. Put it in an empty slot or add a page.") % {"group": name, "n": len(by_group.get(g, []))}
                for g, name, _c in groups if g not in placed]
    featured_options = [{"key": "", "label": _("None — the groups fill the space"), "on": not page["featured"]}] + [
        {"key": key, "label": item["title"] + (" " + _("(sold out)") if item["out"] else ""), "on": key == page["featured"]}
        for key, item in by_key.items()]
    out_count = sum(1 for item in by_key.values() if item["out"])
    if not out_count:
        sold_hint = _("Nothing in this menu is sold out right now.")
    elif d["sold"] == "show":
        sold_hint = ngettext("%(n)d sold-out item stays in place with a Sold out label.", "%(n)d sold-out items stay in place with a Sold out label.", out_count) % {"n": out_count}
    else:
        sold_hint = ngettext("%(n)d sold-out item is taken out and the rest close the gap.", "%(n)d sold-out items are taken out and the rest close the gap.", out_count) % {"n": out_count}

    multi_page = len(d["pages"]) > 1
    overflow = any(menu_boards.screens_of(len(shown.get(key, [])), per) > 1 for p in d["pages"] for key in p["slots"] if key)
    bad_timing = multi_page and overflow and d["group_sec"] > d["page_sec"]

    def timer(kind):
        page_kind = kind == "page"
        sec = d[f"{kind}_sec"]
        active = multi_page if page_kind else overflow
        msgs = []
        if active:
            if not page_kind and bad_timing:
                msgs.append(("err", _("Must be %(n)d s or less. A group can't take longer to turn over than the page it sits on.") % {"n": d["page_sec"]}))
            if page_kind and bad_timing:
                msgs.append(("err", _("Shorter than the group time (%(n)d s). Make this longer or the group time shorter.") % {"n": d["group_sec"]}))
            if sec < 10:
                msgs.append(("warn", _("Under 10 seconds is hard to read. Guests may not finish reading before it changes.")))
            if page_kind and overflow and not bad_timing:
                for p in d["pages"]:
                    for key in p["slots"]:
                        screens = menu_boards.screens_of(len(shown.get(key, [])), per) if key else 1
                        if screens > 1 and screens * d["group_sec"] > d["page_sec"]:
                            name = next((n for g, n, _c in groups if g == key), key)
                            msgs.append(("warn", _("%(group)s needs %(s)d × %(g)d s = %(t)d s to show every item, but the page changes after %(p)d s.")
                                         % {"group": name, "s": screens, "g": d["group_sec"], "t": screens * d["group_sec"], "p": d["page_sec"]}))
        return {"kind": kind, "title": _("Between pages") if page_kind else _("Inside a group"),
                "sub": (ngettext("%(n)d page takes turns on the screen", "%(n)d pages take turns on the screen", len(d["pages"])) % {"n": len(d["pages"])}
                        if multi_page else _("Only one page, so nothing to switch. Add a page to use this.")) if page_kind
                else (_("For groups with more items than their slot fits") if overflow else _("Every group fits its slot, so nothing rotates.")),
                "active": active, "fx": d[f"{kind}_fx"], "sec": sec, "msgs": msgs, "bad": bad_timing}

    look = []
    if template:
        for name, param in template.params.items():
            look.append({"name": name, "param": param, "value": d["look"].get(name)})
    board = menu_boards.board(preview_menu, d, store_name=store_name)
    return {
        "templates": [{"name": n, "t": t, "on": template and n == template.name,
                       "sub": ngettext("%(n)d group", "%(n)d groups", t.layout["groups"]) % {"n": t.layout["groups"]}
                       + ("" if t.layout["featured"] == "none" else " + " + _("featured"))}
                      for n, t in menu_boards.templates().items()],
        "template": template, "d": d, "page": page_index, "pages": list(range(len(d["pages"]))),
        "exposes": (_("%(name)s takes %(featured)s and %(groups)d groups per page, up to %(per)d items each.")
                    % {"name": template.title, "groups": template.layout["groups"], "per": per,
                       "featured": _("no featured item") if template.layout["featured"] == "none" else _("an optional featured item")}) if template else "",
        "has_featured": bool(template) and template.layout["featured"] != "none", "featured_options": featured_options,
        "slots": slots, "unplaced": unplaced, "sold_hint": sold_hint, "timers": [timer("page"), timer("group")], "fx": FX,
        "look": look, "board": board, "bad_timing": bad_timing,
        "portrait": not template or template.layout["orientation"] == "portrait",
        "canvas": (template.layout["width"], template.layout["height"]) if template else (1080, 1920),
    }


class _DraftMenu:
    """A stand-in for the menu with the draft's dishes and combos, for previews before saving."""

    def __init__(self, draft, menu):
        self.pk = self.id = menu.pk
        self.title = draft["title"] or menu.title
        self.display = draft["display"]
        self._dishes, self._combos = draft["dishes"], draft["combos"]

    @property
    def dishes(self):
        return Dish.objects.filter(pk__in=self._dishes)

    @property
    def combos(self):
        return Combo.objects.filter(pk__in=self._combos)


def editor(menu, draft, leave_ask=False, now=None):
    schedule, copy = draft_schedule(draft, menu)
    issues = problems(draft, menu)
    dirty = is_dirty(draft, menu)
    context = {
        "menu": menu, "d": draft, "tab": draft["ui"]["tab"], "status": status(copy, schedule, now),
        "title": draft["title"].strip() or _("Untitled menu"),
        "items_count": len(draft["dishes"]) + len(draft["combos"]),
        "state": json.dumps(draft, default=str),
        "save_status": issues[0] if issues else (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issues else "dirty" if dirty else "",
        "can_save": (dirty or menu.draft) and not issues, "leave_ask": leave_ask,
        "template_name": (menu_boards.template_of(draft["display"]).title if menu_boards.template_of(draft["display"]) else ""),
        "bad_timing": bool(menu_boards.problems(draft["display"], _DraftMenu(draft, menu))),
    }
    return context

