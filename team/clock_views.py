"""
The time clock screen (/team/clock; designed here, after the register's lock screen). It runs on a registered device
with nobody signed in: a person says who they are with their PIN or their email and password, which lasts for this
one visit (session["clock"], a couple of minutes, never a sign-in), then clocks in, starts or ends an unpaid break, or
clocks out (team/clock.py). Clocking in early or off the schedule asks for a manager's PIN. Every punch nudges the
"clock" topic, so the board of who's in stays current on every clock.

    request.session["clock"] = {"user": id, "until": epoch, "view": "me"|"approve"|"done", "done": {title, text, kind},
                                "tab": "pin"|"email"}
"""
import datetime
import time

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import time_format
from django.utils.translation import gettext as _, ngettext

from pavilion.realtime import publish
from pos_server.devices import DEVICE_COOKIE, device_required
from users.access import can
from users.models import Staff

from . import clock
from .models import Shift, TimeEntry

KEY = "clock"
VISIT_SECONDS = 120
PIN_ATTEMPTS, PIN_LOCKOUT_SECONDS = 5, 60


def _when(dt) -> str:
    return time_format(timezone.localtime(dt).time(), "g:i A")


def _span(seconds) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else ngettext("%(n)d min", "%(n)d min", m) % {"n": m}


def _people():
    """Staff with a PIN: who's in first, then who's on today, then everyone else."""
    today = timezone.localdate()
    on = {e.user_id for e, _b in clock.on_now()}
    scheduled = set(Shift.objects.filter(date=today, deleted=False, published=True).values_list("user_id", flat=True))
    staff = [s for s in Staff.objects.filter(user__is_active=True, user__is_staff=True).exclude(pin="").select_related("user")]
    staff.sort(key=lambda s: (s.user_id not in on, s.user_id not in scheduled, (s.user.first_name or s.user.username).lower()))
    return [{"id": s.id, "name": s.display_name, "in": s.user_id in on, "today": s.user_id in scheduled} for s in staff]


def _board(now):
    """The dark side: who's in now, and who's due in the next three hours."""
    on = []
    for entry, on_break in clock.on_now():
        u = entry.user
        role = entry.shift.role.name if entry.shift and entry.shift.role else ""
        on.append({"name": u.get_full_name() or u.username, "ini": (u.first_name[:1] + u.last_name[:1]).upper() or u.username[:1].upper(),
                   "sub": (_("On break") if on_break else _("In since %(time)s") % {"time": _when(entry.clock_in)}) + (f" · {role}" if role else ""),
                   "break": on_break})
    at = clock.minutes(now)
    clocked = {e.user_id for e, _b in clock.on_now()} | set(TimeEntry.objects.filter(clock_in__date=timezone.localdate(now)).values_list("user_id", flat=True))
    due = []
    for s in Shift.objects.filter(date=timezone.localdate(now), deleted=False, published=True, user__isnull=False,
                                  start__gte=at - 30, start__lte=at + 180).select_related("user", "role").order_by("start"):
        if s.user_id not in clocked:
            due.append({"name": s.user.get_full_name() or s.user.username, "time": clock.clock_text(s.start),
                        "role": s.role.name if s.role else "", "late": s.start < at})
    return {"on": on, "due": due}


def _visit(request):
    """The person at the clock right now (their visit hasn't timed out), or None."""
    v = request.session.get(KEY) or {}
    if not v.get("user") or v.get("until", 0) < time.time():
        return None, v
    user = User.objects.filter(pk=v["user"], is_active=True, is_staff=True).first()
    return user, v


def _start(request, user):
    request.session[KEY] = {"user": user.id, "until": time.time() + VISIT_SECONDS, "view": "me", "tab": "pin"}


def _done(request, v, title, text, kind="in"):
    v.update(view="done", done={"title": title, "text": text, "kind": kind}, until=time.time() + 8)
    request.session[KEY] = v
    publish("clock")


def _me(user, now):
    """The signed-in person's panel."""
    st = clock.state(user)
    entry = clock.open_entry(user)
    shifts = clock.shifts_today(user, now)
    plan = clock.check_in(user, now) if st == "out" else None
    if st == "in":
        status = _("Clocked in since %(time)s · %(span)s") % {"time": _when(entry.clock_in), "span": _span(entry.worked_seconds(now))}
    elif st == "break":
        b = next(b for b in entry.breaks.all() if b.end is None)
        status = _("On break since %(time)s · %(span)s") % {"time": _when(b.start), "span": _span((now - b.start).total_seconds())}
    else:
        status = _("Not clocked in")
    punches = clock.today(user, now)
    rows = []
    for p in punches:
        if p["kind"] == "break":
            rows.append({"what": p["what"], "time": _when(p["at"]) + " – " + (_when(p["until"]) if p.get("until") else _("now")), "kind": "break"})
        else:
            rows.append({"what": p["what"], "time": _when(p["at"]), "kind": p["kind"]})
    return {
        "user": user, "first": user.first_name or user.username, "ini": (user.first_name[:1] + user.last_name[:1]).upper() or user.username[:1].upper(),
        "state": st, "status": status, "plan": plan,
        "shifts": [{"role": s.role.name if s.role else _("Shift"), "color": s.role.color if s.role else "#5B6A62",
                    "time": (f"{clock.clock_text(s.start)} – " + (_("when you clock out") if entry and entry.added_shift and entry.shift_id == s.id
                                                                   else clock.clock_text(s.end)))} for s in shifts],
        "rows": rows, "worked": _span(clock.worked_today(user, now)) if punches else "",
    }


@device_required
def time_clock(request):
    now = timezone.now()
    user, v = _visit(request)
    if request.method == "POST":
        op = request.POST.get("op", "")
        device_key = request.get_signed_cookie(DEVICE_COOKIE, default="any")
        if op in ("tab:pin", "tab:email"):
            v["tab"] = op[4:]
            request.session[KEY] = v
        elif op == "pin":
            staff_id = request.POST.get("staff", "")
            key = f"clock-pin:{device_key}:{staff_id}"
            staff = Staff.objects.filter(pk=staff_id if staff_id.isdigit() else None, user__is_active=True, user__is_staff=True).select_related("user").first()
            if cache.get(key, 0) >= PIN_ATTEMPTS:
                v["error"] = _("Too many tries. Wait a minute, then try again.")
            elif staff is None or not staff.pin or not staff.check_pin(request.POST.get("pin", "")):
                cache.set(key, cache.get(key, 0) + 1, timeout=PIN_LOCKOUT_SECONDS)
                v.update(error=_("That PIN didn’t match. Try again."), pick=staff.id if staff else None)
            else:
                cache.delete(key)
                _start(request, staff.user)
                v = None
            if v is not None:
                request.session[KEY] = v
        elif op == "password":
            ident, password = request.POST.get("email", "").strip(), request.POST.get("password", "")
            username = ident
            if "@" in ident:
                matches = list(User.objects.filter(email__iexact=ident).values_list("username", flat=True)[:2])
                username = matches[0] if len(matches) == 1 else ident
            person = authenticate(request, username=username, password=password) if ident and password else None
            if person is None or not person.is_staff:
                v.update(error=_("That email and password didn’t match a staff account."), tab="email", email=ident)
                request.session[KEY] = v
            else:
                _start(request, person)
        elif op in ("done", "cancel"):
            request.session.pop(KEY, None)
        elif user is None:
            request.session.pop(KEY, None)  # their visit timed out
        else:
            v["until"] = time.time() + VISIT_SECONDS
            v.pop("error", None)
            first = user.first_name or user.username
            try:
                if op == "in":
                    plan = clock.check_in(user, now)
                    if not plan["ok"]:
                        v["view"] = "approve"
                        request.session[KEY] = v
                        return redirect("time_clock")
                    clock.clock_in(user, now)
                    _done(request, v, _("You’re clocked in, %(name)s") % {"name": first}, _("At %(time)s. Have a good shift.") % {"time": _when(now)})
                elif op == "approve":
                    approver = next((s for s in clock.approvers(exclude=user) if str(s.id) == request.POST.get("staff")), None)
                    key = f"clock-approve:{device_key}:{request.POST.get('staff')}"
                    if cache.get(key, 0) >= PIN_ATTEMPTS:
                        v["error"] = _("Too many tries. Wait a minute, then try again.")
                    elif approver is None or not approver.check_pin(request.POST.get("pin", "")):
                        cache.set(key, cache.get(key, 0) + 1, timeout=PIN_LOCKOUT_SECONDS)
                        v["error"] = _("That PIN didn’t match. Try again.")
                    else:
                        cache.delete(key)
                        entry = clock.clock_in(user, now, approver=approver.user)
                        text = _("At %(time)s, approved by %(name)s.") % {"time": _when(now), "name": approver.display_name}
                        if entry.added_shift:
                            text += " " + _("A shift was added for you.")
                        _done(request, v, _("You’re clocked in, %(name)s") % {"name": first}, text)
                elif op == "back":
                    v["view"] = "me"
                elif op == "break":
                    clock.start_break(user, now)
                    _done(request, v, _("Enjoy your break, %(name)s") % {"name": first}, _("Unpaid break from %(time)s. Come back and end it here.") % {"time": _when(now)}, "break")
                elif op == "unbreak":
                    clock.end_break(user, now)
                    _done(request, v, _("Welcome back, %(name)s") % {"name": first}, _("Break ended at %(time)s.") % {"time": _when(now)}, "in")
                elif op == "out":
                    entry = clock.clock_out(user, now)
                    _done(request, v, _("You’re clocked out, %(name)s") % {"name": first},
                          _("At %(time)s · %(span)s worked. See you next time.") % {"time": _when(now), "span": _span(entry.worked_seconds(now))}, "out")
            except clock.ClockError as e:
                v["error"] = str(e)
            if v.get("view") != "done":
                request.session[KEY] = v
        return redirect("time_clock")
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    view = "who"
    if user is not None:
        view = v.get("view", "me")
    elif v.get("view") == "done" and v.get("until", 0) >= time.time():
        view = "done"
    error = v.pop("error", "") if v else ""
    if v:
        request.session[KEY] = v
    context = {"view": view, "error": error, "board": _board(now), "tab": (v or {}).get("tab", "pin"), "email": (v or {}).get("email", ""),
               "people": _people() if view == "who" else [], "pick": (v or {}).get("pick"), "done": (v or {}).get("done"),
               "until_ms": int(((v or {}).get("until", 0)) * 1000)}
    if user is not None and view in ("me", "approve"):
        context["me"] = _me(user, now)
        if view == "approve":
            context["approvers"] = [{"id": s.id, "name": s.display_name} for s in clock.approvers(exclude=user)]
    return render(request, "team/clock.html", context)
