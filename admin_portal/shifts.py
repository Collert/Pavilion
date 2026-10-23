"""
Shifts (design: AdminShifts): a week of the schedule, one day's timeline at a time, by staff or by role, with the
coverage against the pars (team/schedule.py) above it. Shifts are saved as they're made, moved or deleted, and wait
for Publish (staff are emailed their changes then).

The view lives in the address: ?week=<monday>&day=0-6&group=staff|role&cov=0|1, plus shift=<id> or new=1 (with
u, role, start) for the drawer.
"""
import datetime
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from events.hours import Hours
from team import schedule
from team.models import ParSet, Shift
from users.models import Role

from .team import face, full_name, initials

STEP = 30  # minutes: shifts start and end on the half hour
BREAKS = [0, 15, 30, 45, 60]


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def state(data) -> dict:
    today = timezone.localdate()
    try:
        week = datetime.date.fromisoformat(data.get("week", ""))
    except ValueError:
        week = today
    week -= datetime.timedelta(days=week.weekday())
    default_day = today.weekday() if week <= today < week + datetime.timedelta(days=7) else 0
    day = _int(data.get("day"), default_day)
    return {"week": week, "day": min(max(day, 0), 6), "group": "role" if data.get("group") == "role" else "staff",
            "cov": data.get("cov", "1") != "0"}


def url(st, **extra) -> str:
    params = {"week": st["week"].isoformat(), "day": st["day"]}
    if st["group"] != "staff":
        params["group"] = st["group"]
    if not st["cov"]:
        params["cov"] = "0"
    params.update({k: v for k, v in extra.items() if v not in (None, "")})
    return "?" + urlencode(params)


def pct(minutes) -> str:
    return f"{minutes / 1440 * 100:.3f}%"


def hours_text(minutes) -> str:
    h = round(minutes / 60, 1)
    return f"{h:g}h"


def team_members():
    return list(User.objects.filter(is_staff=True, is_active=True).select_related("staff_profile")
                .prefetch_related("staff_profile__roles").order_by("first_name", "last_name", "id"))


def person_roles(user) -> list:
    profile = getattr(user, "staff_profile", None)
    return list(profile.roles.all()) if profile else []


def context_for(day, shifts, people, roles):
    ids = [u.id for u in people]
    return {"time_off": schedule.time_off_on(ids, day), "avail": schedule.availability_on(ids, day), "shifts": shifts,
            "roles": {u.id: {r.id for r in person_roles(u)} for u in people}, "names": {u.id: u.first_name or u.username for u in people},
            "role_names": {r.id: r.name for r in roles}}


def page(st, open_shift=None, new=None, draft=None) -> dict:
    """Everything the page shows. `draft`: the drawer's posted values (op=preview), shown without saving."""
    today = timezone.localdate()
    now = timezone.localtime()
    week = [st["week"] + datetime.timedelta(days=i) for i in range(7)]
    day = week[st["day"]]
    all_roles = list(Role.objects.all())
    role_by_id = {r.id: r for r in all_roles}  # archived ones too, for the shifts that still have them
    roles = [r for r in all_roles if not r.archived]
    people = team_members()
    by_uid = {u.id: u for u in people}
    week_shifts = list(Shift.objects.filter(date__in=week).select_related("user", "role"))
    live = [s for s in week_shifts if not s.deleted]
    sets = list(ParSet.objects.all())
    week_paid = {}
    for s in live:
        if s.user_id:
            week_paid[s.user_id] = week_paid.get(s.user_id, 0) + s.paid_minutes

    # the week strip
    days = []
    for i, d in enumerate(week):
        ds = [s for s in live if s.date == d]
        ctx = context_for(d, ds, people, all_roles)
        conflicts = sum(bool(schedule.issues(s, ctx)) for s in ds)
        open_n = sum(not s.user_id for s in ds)
        unpub = any(not s.published for s in week_shifts if s.date == d)
        flag, tone = ((ngettext("%(n)d conflict", "%(n)d conflicts", conflicts) % {"n": conflicts}, "bad") if conflicts
                      else (_("%(n)d open") % {"n": open_n}, "warn") if open_n else (_("Unpublished"), "") if unpub else ("", ""))
        staffed = [s for s in ds if s.user_id]
        days.append({"i": i, "dow": date_format(d, "D"), "date": d.day, "on": i == st["day"], "today": d == today,
                     "sub": _("%(n)d shifts · %(h)s") % {"n": len(staffed), "h": hours_text(sum(s.paid_minutes for s in staffed))},
                     "flag": flag, "tone": tone, "href": url({**st, "day": i})})

    # the day
    day_shifts = [s for s in live if s.date == day]
    ctx = context_for(day, day_shifts, people, all_roles)
    open_ranges = Hours().ranges(day)
    lane_open = [(max(0, a), min(1440, b)) for a, b in open_ranges if b > 0 and a < 1440]
    band = None
    if lane_open:
        a, b = lane_open[0][0], lane_open[-1][1]
        band = {"left": pct(a), "width": pct(b - a), "text": _("Open %(from)s – %(to)s") % {"from": schedule.clock(a, False), "to": schedule.clock(b, False)}}
    closed = "rgba(20,38,31,.035)"  # the lanes are shaded where the store is closed
    stops = [f"{closed} 0%"]
    for a, b in lane_open:
        stops += [f"{closed} {pct(a)}", f"transparent {pct(a)}", f"transparent {pct(b)}", f"{closed} {pct(b)}"]
    stops.append(f"{closed} 100%")
    shade = f"linear-gradient(90deg, {', '.join(stops)})"

    def block(s):
        role = role_by_id.get(s.role_id)
        color = role.color if role else "#5B6A62"
        problems = schedule.issues(s, ctx)
        label = (_("Open · %(role)s") % {"role": role.name if role else _("shift")}) if not s.user_id else (role.name if role else _("Shift"))
        brk = None
        if s.break_minutes and s.end > s.start:
            length = s.end - s.start
            brk = {"left": f"{(length / 2 - s.break_minutes / 2) / length * 100:.2f}%", "width": f"{s.break_minutes / length * 100:.2f}%"}
        title = (f"{label} {schedule.clock(s.start, False)} – {schedule.clock(s.end, False)}"
                 + (", " + _("%(n)d min break") % {"n": s.break_minutes} if s.break_minutes else "")
                 + (f"\n{s.note}" if s.note else "") + ("\n⚠ " + " ".join(problems) if problems else "")
                 + ("" if s.published else "\n" + _("Not published yet")))
        return {"s": s, "color": color, "label": label, "time": f"{schedule.clock(s.start)}–{schedule.clock(s.end)}", "narrow": s.end - s.start < 150,
                "left": pct(s.start), "width": pct(s.end - s.start), "open": not s.user_id, "warn": bool(problems), "pub": s.published,
                "brk": brk, "title": title, "href": url(st, shift=s.id)}

    def unavail_blocks(uid):
        off = ctx["time_off"].get(uid)
        if off:
            if off.all_day:
                return [{"full": True, "label": _("Time off · %(what)s") % {"what": off.category or _("approved")}, "left": "0%", "width": "100%"}]
            return [{"full": False, "label": "", "left": pct(off.start), "width": pct(off.end - off.start), "title": off.category}]
        windows = ctx["avail"].get(uid)
        if windows is None:
            return []
        gaps = schedule.unavailable(windows)
        return [{"full": False, "label": _("Not available") if (a, b) == (0, 1440) else "", "left": pct(a), "width": pct(b - a),
                 "title": _("Not available %(from)s–%(to)s") % {"from": schedule.clock(a), "to": schedule.clock(b)}} for a, b in gaps]

    def staff_row(u, shifts, only_role=None):
        paid = week_paid.get(u.id, 0)
        off = ctx["time_off"].get(u.id)
        return {"kind": "row", "uid": u.id, "role": only_role.id if only_role else "", "name": full_name(u.first_name, u.last_name, u.username),
                "ini": initials(u.first_name, u.last_name, u.username), "face": face(u.id), "dim": bool(off and off.all_day),
                "roles": person_roles(u), "hrs": "—" if off and off.all_day else hours_text(paid), "over": paid > 40 * 60,
                "hrs_title": _("This week: %(h)s") % {"h": hours_text(paid)} + (", " + _("over 40 hours") if paid > 40 * 60 else ""),
                "blocks": [block(s) for s in shifts], "unavail": unavail_blocks(u.id),
                "new_role": (only_role.id if only_role else (person_roles(u)[0].id if person_roles(u) else ""))}

    def open_row(shifts, only_role=None):
        return {"kind": "open", "uid": "", "role": only_role.id if only_role else "", "blocks": [block(s) for s in shifts],
                "fill": ngettext("%(n)d to fill", "%(n)d to fill", len(shifts)) % {"n": len(shifts)} if shifts else _("All filled"),
                "new_role": only_role.id if only_role else ""}

    rows = []
    if st["group"] == "staff":
        rows.append(open_row([s for s in day_shifts if not s.user_id]))
        rows += [staff_row(u, [s for s in day_shifts if s.user_id == u.id]) for u in people]
    else:
        for r in roles:
            rs = [s for s in day_shifts if s.role_id == r.id]
            members = [u for u in people if r in person_roles(u) or any(s.user_id == u.id for s in rs)]
            if not rs and not members:
                continue
            staffed = [s for s in rs if s.user_id]
            open_n = len(rs) - len(staffed)
            rows.append({"kind": "head", "name": r.name, "color": r.color,
                         "sub": _("%(n)d shifts · %(h)s") % {"n": len(staffed), "h": hours_text(sum(s.paid_minutes for s in staffed))}
                         + (" · " + _("%(n)d open") % {"n": open_n} if open_n else "")})
            if open_n:
                rows.append(open_row([s for s in rs if not s.user_id], r))
            rows += [staff_row(u, [s for s in rs if s.user_id == u.id], r) for u in members]

    # coverage against the pars
    need = schedule.needs(day, sets)
    cov, short_total = [], 0
    for r in roles:
        if r.id not in need and not any(s.role_id == r.id for s in day_shifts):
            continue
        cells, short = [], 0
        for h in range(24):
            m = h * 60 + 30
            want = need.get(r.id, [0] * 24)[h]
            have = sum(1 for s in day_shifts if s.user_id and s.role_id == r.id and s.start <= m < s.end
                       and not (ctx["time_off"].get(s.user_id) and ctx["time_off"][s.user_id].all_day))
            tone = "none" if not want and not have else "short" if have < want else "over" if have > want else "ok"
            short += tone == "short"
            cells.append({"t": f"{have}/{want}" if want or have else "", "tone": tone,
                          "title": _("%(role)s %(from)s–%(to)s: %(have)d scheduled, %(want)d needed") % {
                              "role": r.name, "from": schedule.clock(h * 60), "to": schedule.clock(h * 60 + 60), "have": have, "want": want},
                          "href": url(st, new=1, role=r.id, start=h * 60)})
        short_total += short
        cov.append({"r": r, "cells": cells, "short": short})

    staffed = [s for s in day_shifts if s.user_id]
    conflicts = sum(bool(schedule.issues(s, ctx)) for s in day_shifts)
    open_n = len(day_shifts) - len(staffed)
    foot = [(_("%(n)d shifts · %(h)s scheduled") % {"n": len(staffed), "h": hours_text(sum(s.paid_minutes for s in staffed))}, "ink"),
            (_("%(n)d open") % {"n": open_n}, "warn" if open_n else ""),
            (ngettext("%(n)d conflict", "%(n)d conflicts", conflicts) % {"n": conflicts}, "bad" if conflicts else ""),
            ((ngettext("%(n)d role-hour short", "%(n)d role-hours short", short_total) % {"n": short_total}) if short_total else _("Fully covered"),
             "bad" if short_total else "good")]

    n_unpub = schedule.unpublished().count()
    hours = [{"label": schedule.clock(h * 60).replace(":00", ""), "left": pct(h * 60), "first": h == 0,
              "open": any(a <= h * 60 < b for a, b in lane_open)} for h in range(24)]
    now_line = pct(now.hour * 60 + now.minute) if day == today else None

    drawer = None
    if open_shift is not None or new is not None:
        drawer = drawer_context(st, day, open_shift, new, draft, people, roles, ctx, week_paid)

    prev_week, next_week = st["week"] - datetime.timedelta(days=7), st["week"] + datetime.timedelta(days=7)
    return {"st": st, "days": days, "day_title": date_format(day, "l, M j"), "hours": hours, "band": band, "shade": shade,
            "rows": rows, "cov": cov, "foot": foot, "now_line": now_line, "drawer": drawer,
            "unpub": n_unpub, "pub_text": (ngettext("Publish %(n)d change", "Publish %(n)d changes", n_unpub) % {"n": n_unpub}) if n_unpub else _("Published"),
            "prev": url({**st, "week": prev_week}), "next": url({**st, "week": next_week}),
            "groups": [("staff", _("By staff"), url({**st, "group": "staff"})), ("role", _("By role"), url({**st, "group": "role"}))],
            "cov_href": url({**st, "cov": not st["cov"]}), "close": url(st), "back": url(st), "date": day.isoformat()}


def drawer_context(st, day, shift, new, draft, people, roles, ctx, week_paid):
    by_uid = {u.id: u for u in people}
    if draft is not None:
        d = draft
    elif shift is not None:
        d = {"id": shift.id, "user": shift.user_id, "role": shift.role_id, "start": shift.start, "end": shift.end,
             "brk": shift.break_minutes, "note": shift.note, "also": []}
    else:
        start = (_int(new.get("start"), 600) // STEP) * STEP
        start = min(max(start, 0), 1440 - 60)
        uid = _int(new.get("u"))
        role = _int(new.get("role"))
        if role is None and uid in by_uid and person_roles(by_uid[uid]):
            role = person_roles(by_uid[uid])[0].id
        d = {"id": None, "user": uid if uid in by_uid else None, "role": role or (roles[0].id if roles else None),
             "start": start, "end": min(1440, start + 240), "brk": 0, "note": "", "also": []}
    probe = Shift(pk=d["id"] or -1, user_id=d["user"], role_id=d["role"], date=day, start=d["start"], end=d["end"], break_minutes=d["brk"])
    others = [s for s in ctx["shifts"] if s.pk != d["id"]]
    problems = schedule.issues(probe, {**ctx, "shifts": others}, check_role=False)  # the role has its own warning
    if d["end"] - d["start"] >= 300 and not d["brk"]:
        problems.append(_("Shifts over 5 hours usually need a 30 minute meal break."))
    if d["end"] <= d["start"]:
        problems.insert(0, _("The shift ends before it starts."))
    mine = {r.id for r in person_roles(by_uid[d["user"]])} if d["user"] in by_uid else None
    role = next((r for r in roles if r.id == d["role"]), None)
    who = by_uid.get(d["user"])
    paid = max(0, d["end"] - d["start"] - d["brk"])
    week_total = week_paid.get(d["user"], 0) - (shift.paid_minutes if shift is not None and shift.user_id == d["user"] else 0) + paid
    length = (_("%(h)s paid") % {"h": hours_text(paid)} + (" · " + _("%(h)s on site") % {"h": hours_text(d["end"] - d["start"])} if d["brk"] else "")
              + (" · " + _("%(h)s for %(name)s this week") % {"h": hours_text(week_total), "name": who.first_name or who.username} if who else ""))
    times = list(range(0, 1441, STEP))
    return {"d": d, "title": (_("%(name)s’s shift") % {"name": who.first_name or who.username} if who else _("Open shift")) if d["id"] else _("New shift"),
            "color": role.color if role else "#5B6A62",
            "people": [{"u": u, "on": u.id == d["user"], "label": f"{full_name(u.first_name, u.last_name, u.username)} — "
                        + (", ".join(r.name for r in person_roles(u)) or _("no roles"))
                        + (" " + _("(time off)") if ctx["time_off"].get(u.id) else "")} for u in people],
            "roles": [{"r": r, "on": r.id == d["role"], "theirs": mine is None or r.id in mine} for r in roles],
            "role_warn": (_("%(name)s doesn’t have this role. You can still schedule it, or add the role on their profile.")
                          % {"name": who.first_name or who.username}) if who and mine is not None and d["role"] not in mine else "",
            "starts": [(m, schedule.clock(m, False), m == d["start"]) for m in times if m < 1440],
            "ends": [(m, schedule.clock(m, False), m == d["end"]) for m in times if m > 0],
            "breaks": [(b, _("%(n)d min") % {"n": b} if b else _("None"), b == d["brk"]) for b in BREAKS],
            "len": length, "issues": problems,
            "also": [{"i": i, "label": date_format(st["week"] + datetime.timedelta(days=i), "D"), "on": i in d["also"], "self": i == st["day"]} for i in range(7)],
            "save": (_("Save shift") if d["id"] else (_("Add shifts") if d["also"] else _("Add shift"))), "save_off": d["end"] <= d["start"]}


def from_post(post) -> dict:
    start = _int(post.get("start"), 600)
    end = _int(post.get("end"), start + 240)
    return {"id": _int(post.get("id")), "user": _int(post.get("user")), "role": _int(post.get("role")),
            "start": min(max(start, 0), 1439), "end": min(max(end, 1), 1440),
            "brk": _int(post.get("brk"), 0) if _int(post.get("brk"), 0) in BREAKS else 0,
            "note": post.get("note", "").strip()[:80], "also": sorted({i for i in (_int(x) for x in post.getlist("also")) if i is not None and 0 <= i <= 6})}


def save(st, d) -> str:
    """Make or update a shift (and copies on other days); unpublished until Publish."""
    day = st["week"] + datetime.timedelta(days=st["day"])
    user = User.objects.filter(pk=d["user"], is_staff=True).first() if d["user"] else None
    role = Role.objects.filter(pk=d["role"]).first()
    fields = {"user": user, "role": role, "start": d["start"], "end": d["end"], "break_minutes": d["brk"], "note": d["note"], "published": False}
    if d["id"]:
        Shift.objects.filter(pk=d["id"]).update(**fields)
        msg = _("Shift updated")
    else:
        Shift.objects.create(date=day, **fields)
        msg = _("Shift added")
    for i in d["also"]:
        if i != st["day"]:
            Shift.objects.create(date=st["week"] + datetime.timedelta(days=i), **fields)
    if d["also"]:
        msg += " " + _("on %(n)d days") % {"n": len([i for i in d["also"] if i != st["day"]]) + 1}
    return msg + ". " + _("Publish when you’re ready.")


def delete(shift) -> str:
    name = shift.user.first_name if shift.user else ""
    if shift.published_as:
        shift.deleted, shift.published = True, False
        shift.save(update_fields=["deleted", "published"])
    else:
        shift.delete()
    return _("Shift deleted. Publish to let %(who)s know.") % {"who": name or _("staff")}


def move(shift, user_id, start, role_id=None) -> str:
    length = shift.end - shift.start
    start = min(max((start // STEP) * STEP, 0), 1440 - length)
    user = User.objects.filter(pk=user_id, is_staff=True).first() if user_id else None
    shift.user, shift.start, shift.end, shift.published = user, start, start + length, False
    if role_id:
        shift.role_id = role_id
    elif user:
        theirs = [r.id for r in person_roles(user)]
        if theirs and shift.role_id not in theirs:
            shift.role_id = theirs[0]
    shift.save()
    return _("%(who)s · %(from)s–%(to)s. Publish to let them know.") % {
        "who": user.first_name if user else _("Open shifts"), "from": schedule.clock(start), "to": schedule.clock(start + length)}
