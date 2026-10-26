"""
Availability requests (design: AdminRequests; team/requests.py does the deciding): an inbox of time off and
availability changes by status and kind, and the picked request: what's asked, their note, the week before and after
(availability), the shifts it clashes with, things to weigh, and the decision. Shift swaps and hand-offs a coworker
agreed to (team.models.ShiftOffer, team/swaps.py) wait here for a manager too. The view is in the address:
?tab=pending|approved|declined|all&kind=all|timeoff|availability|swap&sel=t<id>|a<id>|s<id>.
"""
import datetime
from urllib.parse import urlencode

from django.db import models
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from team import requests as reqs
from team import swaps
from team.models import Availability, ShiftOffer, TimeOff
from team.schedule import clock

from .team import face, full_name, initials

TABS = [("pending", _("Pending")), ("approved", _("Approved")), ("declined", _("Declined")), ("all", _("All"))]
KINDS = [("all", _("Everything")), ("timeoff", _("Time off")), ("availability", _("Availability changes")), ("swap", _("Swaps"))]
SWAP_STATUS = {ShiftOffer.ACCEPTED: "pending", ShiftOffer.DONE: "approved", ShiftOffer.DECLINED: "declined"}


def state(data) -> dict:
    tab = data.get("tab", "pending")
    kind = data.get("kind", "all")
    return {"tab": tab if tab in dict(TABS) else "pending", "kind": kind if kind in dict(KINDS) else "all", "sel": data.get("sel", "")}


def url(st, **extra) -> str:
    params = {"tab": st["tab"], "kind": st["kind"], **({"sel": st["sel"]} if st["sel"] else {})}
    params.update(extra)
    return "?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})


def find(key):
    """A request from its key: t<id> (time off), a<id> (availability) or s<id> (a swap or hand-off)."""
    model = {"t": TimeOff, "a": Availability, "s": ShiftOffer}.get(key[:1])
    if model is ShiftOffer:
        return swap_requests().filter(pk=key[1:]).first() if key[1:].isdigit() else None
    try:
        return model.objects.select_related("user", "decided_by").get(pk=int(key[1:])) if model else None
    except (ValueError, model.DoesNotExist if model else Exception):
        return None


def key_of(r) -> str:
    return ("t" if isinstance(r, TimeOff) else "s" if isinstance(r, ShiftOffer) else "a") + str(r.pk)


def swap_requests():
    """Swaps and hand-offs a coworker said yes to: waiting for a manager, or decided by one."""
    return (ShiftOffer.objects.filter(kind__in=[ShiftOffer.SWAP, ShiftOffer.HANDOFF])
            .filter(models.Q(status=ShiftOffer.ACCEPTED) | models.Q(decided_by__isnull=False))
            .select_related("by", "to", "shift__role", "theirs__role", "decided_by"))


def status_of(r) -> str:
    return SWAP_STATUS.get(r.status, "declined") if isinstance(r, ShiftOffer) else r.status


def user_of(r):
    return r.by if isinstance(r, ShiftOffer) else r.user


def sent_at(r):
    return (r.answered_at or r.created_at) if isinstance(r, ShiftOffer) else r.submitted_at


def dfmt(d, dow=False, year=False) -> str:
    fmt = ("D " if dow else "") + ("M j, Y" if year or d.year != timezone.localdate().year else "M j")
    return date_format(d, fmt)


def rel(dt) -> str:
    days = (timezone.localdate() - timezone.localtime(dt).date()).days
    if days <= 0:
        return _("today")
    if days == 1:
        return _("yesterday")
    if days < 7:
        return _("%(n)d days ago") % {"n": days}
    return dfmt(timezone.localtime(dt).date())


def range_text(r) -> str:
    if isinstance(r, ShiftOffer):
        return f"{dfmt(r.shift.date, True)} · {swaps.span(r.shift)} → {r.to.first_name or r.to.username}"
    if isinstance(r, Availability):
        return (_("From %(when)s") % {"when": dfmt(r.effective, True)}) + (" " + _("to %(when)s") % {"when": dfmt(r.until)} if r.until else ", " + _("ongoing"))
    if r.first_day == r.last_day:
        return dfmt(r.first_day, True) + (", " + _("all day") if r.all_day else f", {clock(r.start)}–{clock(r.end)}")
    return f"{dfmt(r.first_day, True)} – {dfmt(r.last_day, True)}"


def page(st, me) -> dict:
    today = timezone.localdate()
    everything = list(TimeOff.objects.select_related("user")) + list(Availability.objects.select_related("user")) + list(swap_requests())
    counts = {k: (len(everything) if k == "all" else sum(status_of(r) == k for r in everything)) for k, _l in TABS}

    def kind(r):
        return "timeoff" if isinstance(r, TimeOff) else "swap" if isinstance(r, ShiftOffer) else "availability"

    def starts(r):
        return r.first_day if isinstance(r, TimeOff) else r.shift.date if isinstance(r, ShiftOffer) else r.effective

    shown = [r for r in everything if (st["tab"] == "all" or status_of(r) == st["tab"]) and (st["kind"] == "all" or kind(r) == st["kind"])]
    pending = sorted([r for r in shown if status_of(r) == "pending"], key=starts)
    decided = sorted([r for r in shown if status_of(r) != "pending"], key=lambda r: r.decided_at or sent_at(r), reverse=True)
    shown = pending + decided
    sel = find(st["sel"]) if st["sel"] else None
    if sel is None and shown:
        sel = shown[0]
    rows = []
    for r in shown:
        status = status_of(r)
        n = len(reqs.affected_shifts(r)) if status == "pending" and not isinstance(r, ShiftOffer) else 0
        soon = status == "pending" and (starts(r) - today).days <= 7
        u = user_of(r)
        rows.append({"key": key_of(r), "on": sel is not None and key_of(r) == key_of(sel), "name": full_name(u.first_name, u.last_name, u.username),
                     "ini": initials(u.first_name, u.last_name, u.username), "face": face(u.id), "kind": kind(r),
                     "what": range_text(r), "when": _("Sent %(when)s") % {"when": rel(sent_at(r))},
                     "flag": (ngettext("%(n)d shift affected", "%(n)d shifts affected", n) % {"n": n}) if n else (_("Starts soon") if soon else ""),
                     "flag_tone": "bad" if n else "warn", "status": status, "href": url({**st, "sel": key_of(r)})})
    return {"st": st, "tabs": [(k, label, counts[k], k == st["tab"], url({**st, "tab": k, "sel": ""})) for k, label in TABS],
            "kinds": [(k, label, k == st["kind"], url({**st, "kind": k, "sel": ""})) for k, label in KINDS], "rows": rows,
            "empty": _("Nothing waiting. You’re all caught up.") if st["tab"] == "pending" else _("No requests here."),
            "d": (swap_detail(sel) if isinstance(sel, ShiftOffer) else detail(sel, me)) if sel else None}


def swap_detail(o) -> dict:
    """A swap or hand-off the coworker agreed to: who gives what to whom, and the decision."""
    u, to = o.by, o.to
    status = status_of(o)

    def line(shift, who):
        return {"date": dfmt(shift.date, True), "color": shift.role.color if shift.role else "#5B6A62",
                "what": f"{shift.role.name if shift.role else ''} · {swaps.span(shift)}".lstrip(" ·"), "who": who}

    d = {"r": o, "key": key_of(o), "is_swap": True, "is_t": False, "name": full_name(u.first_name, u.last_name, u.username),
         "first": u.first_name or u.username, "ini": initials(u.first_name, u.last_name, u.username), "face": face(u.id), "kind": "swap",
         "status": status, "status_text": {"pending": _("Waiting for you"), "approved": _("Approved"), "declined": _("Declined")}[status],
         "meta": _("%(to)s said yes %(when)s") % {"to": to.first_name or to.username, "when": rel(sent_at(o))},
         "big": (_("Swap with %(name)s") if o.kind == ShiftOffer.SWAP else _("Hand-off to %(name)s")) % {"name": full_name(to.first_name, to.last_name, to.username)},
         "big_sub": _("Both have the role, and neither ends up with overlapping shifts.") if status == "pending" else "",
         "lines": [line(o.shift, _("Goes to %(name)s") % {"name": to.first_name or to.username})]
                  + ([line(o.theirs, _("Goes to %(name)s") % {"name": u.first_name or u.username})] if o.theirs else []),
         "pending": status == "pending", "msg_ph": _("e.g. All good, thanks for sorting it out."),
         "approve_text": _("Approve the swap") if o.kind == ShiftOffer.SWAP else _("Approve the hand-off")}
    problems = []
    if status == "pending":
        why = swaps.why_not(to, o.shift, ignore=[o.theirs])
        if why:
            problems.append(_("%(name)s: %(why)s") % {"name": to.first_name or to.username, "why": why})
        if o.theirs:
            why = swaps.why_not(u, o.theirs, ignore=[o.shift])
            if why:
                problems.append(_("%(name)s: %(why)s") % {"name": u.first_name or u.username, "why": why})
    d["problems"] = problems
    if status != "pending":
        who = o.decided_by.first_name or o.decided_by.username if o.decided_by else _("someone")
        when = dfmt(timezone.localtime(o.decided_at).date(), year=True) if o.decided_at else ""
        d["dec_text"] = (_("Approved by %(who)s on %(when)s") if status == "approved" else _("Declined by %(who)s on %(when)s")) % {"who": who, "when": when}
    return d


def detail(r, me) -> dict:
    today = timezone.localdate()
    is_t = isinstance(r, TimeOff)
    u = r.user
    others = (TimeOff.objects.filter(user=u).count() + Availability.objects.filter(user=u).count()) - 1
    d = {"r": r, "key": key_of(r), "is_t": is_t, "name": full_name(u.first_name, u.last_name, u.username), "first": u.first_name or u.username,
         "ini": initials(u.first_name, u.last_name, u.username), "face": face(u.id), "kind": "timeoff" if is_t else "availability",
         "meta": " · ".join(x for x in (_("Sent %(when)s") % {"when": rel(r.submitted_at)}, r.category if is_t else "",
                                        (ngettext("%(n)d other request", "%(n)d other requests", others) % {"n": others})) if x)}
    if is_t:
        n = (r.last_day - r.first_day).days + 1
        away = (r.first_day - today).days
        d["big"] = dfmt(r.first_day, True, True) if r.first_day == r.last_day else f"{dfmt(r.first_day, True)} – {dfmt(r.last_day, True, True)}"
        d["big_sub"] = (((ngettext("%(n)d day off", "%(n)d days off", n) % {"n": n}) + (" · " + (ngettext("starts in %(n)d day", "starts in %(n)d days", away) % {"n": away}) if away > 0 else ""))
                        if r.all_day else _("Part day, %(from)s to %(to)s") % {"from": clock(r.start, False), "to": clock(r.end, False)}
                        + (" · " + (ngettext("in %(n)d day", "in %(n)d days", away) % {"n": away}) if away > 0 else ""))
    else:
        d["big"] = _("New availability from %(when)s") % {"when": dfmt(r.effective, True, True)}
        d["big_sub"] = (_("Until %(when)s, then back to the usual") % {"when": dfmt(r.until, True, True)}) if r.until else _("Ongoing, replaces their current availability")
        now = reqs.current_windows(r)
        week = []
        for i in range(7):
            before, after = now.get(str(i), []), (r.windows or {}).get(str(i), [])
            same = [list(w) for w in before] == [list(w) for w in after]
            week.append({"day": reqs.weekday(i), "same": same, "now": [{"left": pct(a), "width": pct(b - a)} for a, b in before],
                         "req": [{"left": pct(a), "width": pct(b - a)} for a, b in after], "none": not after,
                         "ch": _("No change") if same else reqs.times_text(after), "tone": "same" if same else "none" if not after else "new"})
        d["week"] = week
        d["hours"] = [{"label": clock(h * 60).replace(":00", ""), "left": pct(h * 60), "first": h == 0} for h in (0, 4, 8, 12, 16, 20)]
    if r.status == "pending":
        shifts = [reqs.describe(s) for s in reqs.affected_shifts(r)]
    else:
        shifts = r.affected or []
    tag = {"approved": _("Moved to open") if r.handle == "open" else _("Kept"), "declined": _("Kept")}.get(r.status)
    d["shifts"] = [{"date": dfmt(datetime.date.fromisoformat(s["date"]), True), "color": s["color"],
                    "what": f"{s['role']} · {clock(s['start'], False)} – {clock(s['end'], False)}".lstrip(" ·"),
                    "tag": tag or _("Becomes open"), "tone": "open" if not tag else ""} for s in shifts]
    d["impact"] = (ngettext("%(n)d scheduled shift affected", "%(n)d scheduled shifts affected", len(shifts)) % {"n": len(shifts)}) if shifts else _("No scheduled shifts affected")
    d["impact_sub"] = _("Shifts they already have during this time off.") if is_t else _("Upcoming shifts that wouldn’t fit the new availability.")
    d["no_shifts"] = _("No shifts were affected.") if r.status != "pending" else (_("Nothing on the schedule for them yet in this time. Easy yes.") if is_t
                      else _("Their current shifts all fit the new availability."))
    d["notes"] = reqs.notes(r) if r.status == "pending" else []
    d["pending"] = r.status == "pending"
    d["approve"] = ((_("Approve time off") + (", " + (ngettext("open %(n)d shift", "open %(n)d shifts", len(shifts)) % {"n": len(shifts)}) if shifts else ""))
                    if is_t else _("Approve new availability"))
    d["approve_keep"] = _("Approve time off") if is_t else _("Approve new availability")
    d["msg_ph"] = _("e.g. Have a great trip! Or, why it can’t work this time.") if is_t else _("e.g. Thanks for the heads-up. We’ll find weekend cover.")
    if not d["pending"]:
        who = r.decided_by.first_name or r.decided_by.username if r.decided_by else _("someone")
        when = dfmt(timezone.localtime(r.decided_at).date(), year=True) if r.decided_at else ""
        d["dec_text"] = (_("Approved by %(who)s on %(when)s") if r.status == "approved" else _("Declined by %(who)s on %(when)s")) % {"who": who, "when": when}
        d["past"] = (r.last_day < today) if is_t else bool(r.until and r.until < today)
    d["schedule_href"] = shifts_href(r)
    return d


def shifts_href(r) -> str:
    from django.urls import reverse
    day = r.first_day if isinstance(r, TimeOff) else max(r.effective, timezone.localdate())
    monday = day - datetime.timedelta(days=day.weekday())
    return reverse("admin_shifts") + "?" + urlencode({"week": monday.isoformat(), "day": day.weekday()})


def pct(minutes) -> str:
    return f"{minutes / 1440 * 100:.3f}%"
