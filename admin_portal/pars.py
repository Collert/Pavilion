"""
Shift pars (design: AdminShiftPars): how many people each role needs, hour by hour (team.ParSet). The weekly pars
and seasonal ones (between two dates, on some weekdays, adding to the weekly numbers or replacing them). One form
with Save: the draft lives in the session; every button posts the selected set's fields back.

    request.session["admin_pars"] = {"sets": [{"id": 3 or "n1", "kind", "name", "start", "end", "mode", "days", "pars"}],
                                     "sel": id, "day": 0-6, "gone": [ids]}
    pars = {"<weekday>": {"<role id>": [[from, to, people], ...]}}
"""
import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from team import schedule
from team.models import ParSet
from users.models import Role

KEY = "admin_pars"
STEP = 30


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_row(s) -> dict:
    return {"id": s.id, "kind": s.kind, "name": s.name, "start": s.start.isoformat() if s.start else "", "end": s.end.isoformat() if s.end else "",
            "mode": s.mode, "days": sorted(s.days or []), "pars": s.pars or {}}


def saved() -> list:
    schedule.weekly()
    sets = list(ParSet.objects.all())
    return [as_row(s) for s in sorted(sets, key=lambda s: (s.kind != ParSet.WEEKLY, s.start or datetime.date.min, s.id))]


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        sets = saved()
        st = {"sets": sets, "sel": sets[0]["id"], "day": timezone.localdate().weekday(), "gone": []}
    return st


def selected(st) -> dict:
    return next((x for x in st["sets"] if str(x["id"]) == str(st["sel"])), st["sets"][0])


def take_post(st, post):
    """The selected set's fields: a season's name, dates and mode, and the open day's windows."""
    e = selected(st)
    if e["kind"] == ParSet.SEASON:
        if "name" in post:
            e["name"] = post.get("name", "").strip()[:40]
        for f in ("start", "end"):
            if f in post:
                e[f] = post.get(f, "")[:10]
        if post.get("mode") in (ParSet.ADD, ParSet.REPLACE):
            e["mode"] = post["mode"]
    day = str(st["day"])
    pars = e["pars"].setdefault(day, {})
    for role in list(pars):
        wins = pars[role]
        for k, w in enumerate(wins):
            a = _int(post.get(f"w-{role}-{k}-a"), w[0])
            b = _int(post.get(f"w-{role}-{k}-b"), w[1])
            a = min(max(a, 0), 1440 - STEP)
            wins[k] = [a, min(max(b, a + STEP), 1440), w[2]]


def apply_op(st, op, post) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    day = str(st["day"])
    pars = e["pars"].setdefault(day, {})
    if name == "pick":
        found = next((x for x in st["sets"] if str(x["id"]) == arg), None)
        if found:
            st["sel"] = found["id"]
    elif name == "day" and _int(arg) is not None and 0 <= _int(arg) <= 6:
        st["day"] = _int(arg)
    elif name == "season":
        today = timezone.localdate()
        first = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        last = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
        new_id = f"n{len(st['sets']) + 1}{timezone.now().strftime('%H%M%S')}"
        st["sets"].append({"id": new_id, "kind": ParSet.SEASON, "name": _("New seasonal par"), "start": first.isoformat(), "end": last.isoformat(),
                           "mode": ParSet.ADD, "days": list(range(7)), "pars": {}})
        st["sel"] = new_id
    elif name == "add":
        wins = pars.setdefault(arg, [])
        a = min(1380, wins[-1][1]) if wins else 660
        wins.append([a, min(1440, a + 180), 1])
    elif name in ("less", "more", "rm"):
        role, _s, k = arg.partition(":")
        wins = pars.get(role, [])
        k = _int(k)
        if k is not None and 0 <= k < len(wins):
            if name == "rm":
                wins.pop(k)
            else:
                wins[k][2] = min(9, max(1, wins[k][2] + (1 if name == "more" else -1)))
            if not wins:
                pars.pop(role, None)
    elif name == "dayon" and e["kind"] == ParSet.SEASON:
        d = st["day"]
        e["days"] = sorted(set(e["days"]) - {d}) if d in e["days"] else sorted(set(e["days"]) | {d})
    elif name == "copy":
        targets = sorted({i for i in (_int(x) for x in post.getlist("copy")) if i is not None and 0 <= i <= 6 and i != st["day"]})
        if arg == "all":
            targets = [i for i in range(7) if i != st["day"]]
        for i in targets:
            e["pars"][str(i)] = {r: [list(w) for w in wins] for r, wins in pars.items()}
        if e["kind"] == ParSet.SEASON:
            e["days"] = sorted(set(e["days"]) | set(targets))
        if targets:
            return _("%(day)s’s pars copied to %(days)s.") % {"day": weekday_name(st["day"]), "days": ", ".join(weekday_short(i) for i in targets)}
    elif name == "delete" and e["kind"] == ParSet.SEASON:
        st["sets"] = [x for x in st["sets"] if x is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = st["sets"][0]["id"]
        return _("%(name)s deleted. Save to keep it.") % {"name": e["name"] or _("Seasonal par")}
    return ""


def problem(x) -> str:
    if x["kind"] != ParSet.SEASON:
        return ""
    if not x["name"]:
        return _("Give it a name.")
    if not x["start"] or not x["end"]:
        return _("Pick both dates.")
    if x["end"] < x["start"]:
        return _("The end date is before the start date.")
    return ""


def is_dirty(st) -> bool:
    return bool(st["gone"]) or _clean(st["sets"]) != _clean(saved())


def _clean(sets):
    def tidy(pars):
        return {d: {r: w for r, w in roles.items() if w} for d, roles in (pars or {}).items() if any(roles.values())}
    return [{**x, "pars": tidy(x["pars"])} for x in sets]


@transaction.atomic
def save(st):
    ParSet.objects.filter(pk__in=st["gone"]).delete()
    for x in _clean(st["sets"]):
        fields = {"kind": x["kind"], "name": x["name"], "mode": x["mode"], "days": x["days"], "pars": x["pars"],
                  "start": datetime.date.fromisoformat(x["start"]) if x["start"] else None,
                  "end": datetime.date.fromisoformat(x["end"]) if x["end"] else None}
        if isinstance(x["id"], int):
            ParSet.objects.filter(pk=x["id"]).update(**fields)
        else:
            ParSet.objects.create(**fields)


def weekday_name(i):
    return date_format(datetime.date(2024, 1, 1) + datetime.timedelta(days=i), "l")


def weekday_short(i):
    return date_format(datetime.date(2024, 1, 1) + datetime.timedelta(days=i), "D")


def staff_minutes(day_pars) -> int:
    return sum(schedule.count_at(wins, m + 15) * STEP for wins in day_pars.values() for m in range(0, 1440, STEP))


def hours_text(minutes):
    return f"{round(minutes / 60, 1):g}h"


def page(st) -> dict:
    today = timezone.localdate().isoformat()
    roles = list(Role.objects.all())
    sets = st["sets"]
    weekly = sets[0]
    e = selected(st)
    is_season = e["kind"] == ParSet.SEASON
    errs = {str(x["id"]): problem(x) for x in sets if problem(x)}
    dirty = is_dirty(st)
    if errs:
        bad = next(x for x in sets if str(x["id"]) in errs)
        status, kind = _("Fix %(name)s to save") % {"name": bad["name"] or _("the new seasonal par")}, "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""

    def status_of(x):
        if x["kind"] != ParSet.SEASON or not x["start"] or not x["end"]:
            return None
        return ("ended", _("Ended")) if x["end"] < today else ("upcoming", _("Upcoming")) if x["start"] > today else ("active", _("Active"))

    def dshort(iso):
        if not iso:
            return "—"
        d = datetime.date.fromisoformat(iso)
        return date_format(d, "M j" if d.year == timezone.localdate().year else "M j, Y")

    def item(x):
        days = range(7) if x["kind"] == ParSet.WEEKLY else x["days"]
        total = sum(staff_minutes((x["pars"] or {}).get(str(d), {})) for d in days)
        st_ = status_of(x)
        sub = (_("%(h)s a week across all roles") % {"h": hours_text(total)} if x["kind"] == ParSet.WEEKLY
               else f"{dshort(x['start'])} – {dshort(x['end'])} · " + (_("adds %(h)s/wk") if x["mode"] == ParSet.ADD else _("replaces, %(h)s/wk")) % {"h": hours_text(total)})
        return {"x": x, "on": x is e, "name": x["name"] or _("Untitled"), "st": st_, "sub": sub, "weekly": x["kind"] == ParSet.WEEKLY}

    seasons = sorted(sets[1:], key=lambda x: ((x["end"] or "") < today, x["start"] or ""))
    d = st["day"]
    day_on = not is_season or d in e["days"]
    dp = (e["pars"] or {}).get(str(d), {})
    base_dp = (weekly["pars"] or {}).get(str(d), {})
    add = is_season and e["mode"] == ParSet.ADD
    times = list(range(0, 1441, STEP))
    role_rows = []
    for r in roles:
        wins = dp.get(str(r.id), [])
        cells = []
        for h in range(24):
            n = max(schedule.count_at(wins, h * 60 + 15), schedule.count_at(wins, h * 60 + 45))
            base = max(schedule.count_at(base_dp.get(str(r.id)), h * 60 + 15), schedule.count_at(base_dp.get(str(r.id)), h * 60 + 45)) if add else 0
            cells.append({"t": ("+" if add else "") + str(n) if n else "", "n": n, "base": base, "a": f"{min(85, 22 + 25 * n)}%",
                          "title": f"{r.name} {schedule.clock(h * 60)}–{schedule.clock(h * 60 + 60)}: "
                          + (_("%(base)d weekly + %(n)d extra") % {"base": base, "n": n} if add else _("%(n)d needed") % {"n": n})})
        mins = sum(schedule.count_at(wins, m + 15) * STEP for m in range(0, 1440, STEP))
        role_rows.append({"r": r, "cells": cells, "sub": (hours_text(mins) + (" " + _("extra") if add else "")) if wins else _("Not needed"),
                          "add": _("+ Add time") if wins else _("+ Add %(role)s needed") % {"role": r.name.lower()},
                          "wins": [{"k": k, "a": w[0], "b": w[1], "n": ("+" if add else "") + str(w[2]), "less_off": w[2] <= 1,
                                    "a_opts": [(m, schedule.clock(m, False)) for m in times if m < 1440],
                                    "b_opts": [(m, schedule.clock(m, False)) for m in times if m > w[0]]} for k, w in enumerate(wins)]})
    sh = staff_minutes(dp)
    if not day_on:
        day_sub = _("This seasonal par doesn’t apply on %(day)ss.") % {"day": weekday_name(d)}
    elif is_season:
        day_sub = (_("%(h)s extra on top of the weekly %(day)s pars.") if add else _("%(h)s of staff, used instead of the weekly %(day)s pars.")) % {"h": hours_text(sh), "day": weekday_name(d)}
    else:
        day_sub = _("%(h)s of staff across all roles. Set how many people each role needs and when.") % {"h": hours_text(sh)}
    season_text = ""
    if is_season:
        st_ = status_of(e)
        season_text = (st_[1] + ". " if st_ else "") + _("Runs %(from)s to %(to)s on %(days)s.") % {
            "from": dshort(e["start"]), "to": dshort(e["end"]),
            "days": _("every day") if len(e["days"]) == 7 else ", ".join(weekday_short(i) for i in e["days"]) or _("no days")}
    active_season = any(x["start"] and x["end"] and x["start"] <= today <= x["end"] for x in seasons)
    return {"st": st, "status": status, "status_kind": kind, "can_save": dirty and not errs,
            "weekly": item(weekly), "seasons": [item(x) for x in seasons], "e": e, "is_season": is_season, "err": errs.get(str(e["id"]), ""),
            "season_text": season_text,
            "days": [{"i": i, "label": weekday_short(i), "on": i == d, "off": is_season and i not in e["days"],
                      "sub": hours_text(staff_minutes((e["pars"] or {}).get(str(i), {}))) if not is_season or i in e["days"] else _("Off")} for i in range(7)],
            "day_title": weekday_name(d), "day_sub": day_sub, "day_on": day_on, "roles": role_rows,
            "copy_days": [{"i": i, "label": weekday_short(i)[:2], "self": i == d} for i in range(7)],
            "hours": [{"label": schedule.clock(h * 60).replace(":00", ""), "left": f"{h / 24 * 100:.3f}%", "first": h == 0} for h in range(0, 24, 2)],
            "foot": _("Where two times overlap, the higher number counts. Shifts compare each hour against these pars")
            + (" " + _("plus any active seasonal par.") if active_season else " " + _("plus any seasonal par running that day."))}
