"""
Menus (design: AdminMenus, list + items and schedule tabs; the display tab is left to the screens app).
Schedules are worked out in pos_server.menu_schedule.
"""

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from pos_server.menu_schedule import Schedule, days_text, fmt_minutes, minute_of_week
from pos_server.models import Combo, Dish, Menu, MenuRule, Station

STATUS = {"draft": ("Draft", "draft"), "live": ("Live now", "live"), "scheduled": ("Scheduled", "scheduled"), "off": ("Off", "off")}
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"]


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
                     "count": menu.dishes.count() + menu.combos.count(), "first": i == 0, "last": i == len(menus) - 1})
    banner = None
    if live:
        extra = len(live) - 1
        banner = {"name": live[0].title, "more": ngettext("%(n)d more live underneath", "%(n)d more live underneath", extra) % {"n": extra} if extra else "",
                  "next": schedule.next_change(live[0], now) if live[0].auto else ""}
    return {"rows": rows, "banner": banner}


def catalog(menu, group="all", query=""):
    """Every dish and combo, with whether it's on `menu`, filtered by station (or combos) and a search."""
    stations = list(Station.objects.all())
    groups = [{"key": "all", "label": _("All"), "color": None}] + \
             [{"key": str(s.id), "label": s.name, "color": s.color} for s in stations] + \
             [{"key": "combos", "label": _("Combos"), "color": "#14261F"}]
    if group not in {g["key"] for g in groups}:
        group = "all"
    on_dishes = set(menu.dishes.values_list("id", flat=True))
    on_combos = set(menu.combos.values_list("id", flat=True))
    q = (query or "").strip()
    rows = []
    if group != "combos":
        dishes = Dish.objects.select_related("station").order_by("station__position", "title")
        if group != "all":
            dishes = dishes.filter(station_id=int(group))
        if q:
            dishes = dishes.filter(title__icontains=q)
        rows += [{"kind": "dish", "obj": d, "on": d.id in on_dishes, "color": d.station.color, "group": d.station.name,
                  "price": d.price, "out": not d.available} for d in dishes]
    if group in ("all", "combos"):
        combos = Combo.objects.order_by("title")
        if q:
            combos = combos.filter(title__icontains=q)
        rows += [{"kind": "combo", "obj": c, "on": c.id in on_combos, "color": "#14261F", "group": _("Combos"),
                  "price": c.price, "out": False} for c in combos]
    picked = []
    for s in stations:
        items = list(menu.dishes.filter(station=s).order_by("title"))
        if items:
            picked.append({"label": s.name, "color": s.color, "items": [{"kind": "dish", "obj": d, "out": not d.available} for d in items]})
    combos = list(menu.combos.order_by("title"))
    if combos:
        picked.append({"label": _("Combos"), "color": "#14261F", "items": [{"kind": "combo", "obj": c, "out": False} for c in combos]})
    count = len(on_dishes) + len(on_combos)
    return {"groups": groups, "group": group, "query": q, "rows": rows, "picked": picked, "count": count,
            "all_shown_on": bool(rows) and all(r["on"] for r in rows)}


def schedule_tab(menu, now=None):
    now = timezone.localtime(now or timezone.now())
    menus = list(Menu.objects.all())
    schedule = Schedule(menus)
    menu = schedule.by_id[menu.id]
    rules = schedule.rules.get(menu.id, [])

    def rule_row(rule):
        row = {"rule": rule, "text": rule_text(rule, schedule.by_id)}
        if rule.type == MenuRule.WEEKLY:
            row["label"] = _("Every week · %(text)s") % {"text": row["text"]}
            row["days"] = [{"n": i, "letter": DAY_LETTERS[i], "name": DAY_SHORT[i], "on": i in rule.day_list} for i in range(7)]
        elif rule.type == MenuRule.DATES:
            row["label"] = _("Between dates") if rule.kind == MenuRule.ON else _("Closed between dates")
        else:
            row["label"] = _("When another menu starts")
            row["others"] = [m for m in menus if m.id != menu.id]
        return row

    sets = [
        {"kind": MenuRule.ON, "title": _("Turn on when"), "sub": _("Any one of these switches the menu on"),
         "empty": _("No rules yet, so it never turns on by itself."),
         "rules": [rule_row(r) for r in rules if r.kind == MenuRule.ON],
         "adds": [(MenuRule.WEEKLY, _("Weekly time")), (MenuRule.DATES, _("Date range"))]},
        {"kind": MenuRule.OFF, "title": _("Turn off when"), "sub": _("Any one of these switches it off again"),
         "empty": _("No rules yet, so it stays on until someone turns it off."),
         "rules": [rule_row(r) for r in rules if r.kind == MenuRule.OFF],
         "adds": [(MenuRule.WEEKLY, _("Weekly time")), (MenuRule.DATES, _("Date range")), (MenuRule.MENU, _("Another menu starts"))]},
    ]

    # The week, one row a day: this menu's on times, other scheduled menus underneath, and now
    mine = schedule.segments(menu)
    others = [(m, schedule.segments(m)) for m in menus if m.id != menu.id and m.auto and not m.draft]
    minute = minute_of_week(now)

    def slices(segs, day, name):
        out = []
        for a, b in segs:
            lo, hi = max(a, day * 1440), min(b, (day + 1) * 1440)
            if hi > lo:
                out.append({"left": round((lo - day * 1440) / 14.4, 2), "width": round((hi - lo) / 14.4, 2),
                            "title": f"{name} · {fmt_minutes(lo % 1440)}–{fmt_minutes(hi % 1440 if hi % 1440 else 1440)}"})
        return out

    week = [{"name": _(DAY_SHORT[d]), "today": d == minute // 1440, "mine": slices(mine, d, menu.title),
             "others": [s for m, segs in others for s in slices(segs, d, m.title)]} for d in range(7)]
    live_now = schedule.is_live(menu, now) if not menu.draft else schedule.is_live(_published(menu), now)
    next_change = schedule.next_change(menu, now)
    week_note = (_("No weekly times set") if not mine else
                 (_("On now") if live_now else _("Off now")) + (" · " + next_change[0].lower() + next_change[1:] if next_change else ""))

    notes = [_("Also on every day from %(a)s to %(b)s (not shown on the week above)") % {"a": date_format(r.start_date, "M j"), "b": date_format(r.end_date, "M j")}
             for r in schedule.date_rules(menu, "on")]
    notes += [_("Kept off from %(a)s to %(b)s") % {"a": date_format(r.start_date, "M j"), "b": date_format(r.end_date, "M j")}
              for r in schedule.date_rules(menu, "off")]

    order = {m.id: i for i, m in enumerate(menus)}
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
            first = other.title if order[other.id] < order[menu.id] else menu.title
            overlaps.append(_("Overlaps with %(other)s on %(days)s, %(span)s. Both are live then; %(first)s comes first because it's higher in the list.")
                            % {"other": other.title, "days": ", ".join(days), "span": span, "first": first})
    return {"sets": sets, "week": week, "now_left": round((minute % 1440) / 14.4, 2), "week_note": week_note if menu.auto else "",
            "notes": notes, "overlaps": overlaps[:3], "auto": menu.auto, "manual_on": menu.manual_on}


def _published(menu):
    """A copy of a draft as if it were published, to preview its schedule."""
    copy = Menu(pk=menu.pk, title=menu.title, auto=menu.auto, manual_on=menu.manual_on, position=menu.position)
    return copy
