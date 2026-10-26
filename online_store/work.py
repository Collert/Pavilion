"""
The staff side of the account (design: StoreAccount's staff views): Shifts (what's coming up, giving a shift away
or asking a coworker to swap or take it, and the time clock's history), Up for grabs (open shifts, shifts coworkers
gave away and what coworkers asked of them) and Availability and time off (their week, their requests and new
ones). The shift moves are in team/swaps.py, the requests in team/requests.py. Only staff (is_staff) see them.
"""
import datetime

from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from admin_portal.team import face, full_name, initials
from team import requests as reqs, swaps
from team.models import Availability, Shift, ShiftOffer, TimeEntry, TimeOff
from team.schedule import clock

CATEGORIES = [("Vacation", _("Vacation")), ("Sick", _("Sick")), ("Appointment", _("Appointment")), ("Personal", _("Personal"))]
DAYS = [_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")]


def is_worker(user) -> bool:
    return bool(user.is_authenticated and user.is_staff)


def staff_only(view):
    """Account pages for staff: others go to their orders."""
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(reverse("store_signin") + f"?next={request.path}")
        if not is_worker(request.user):
            return redirect("account")
        return view(request, *args, **kwargs)
    wrapped.__name__, wrapped.__doc__ = view.__name__, view.__doc__
    return wrapped


# ---------- words ----------

def day_label(d) -> str:
    return date_format(d, "D, M j")


def hours(minutes) -> str:
    h, m = divmod(max(0, int(minutes)), 60)
    return (_("%(h)d h %(m)d m") % {"h": h, "m": m}) if m else (_("%(h)d h") % {"h": h})


def name_of(user) -> str:
    return full_name(user.first_name, user.last_name, user.username)


def person(user) -> dict:
    return {"name": name_of(user), "first": user.first_name or user.username, "ini": initials(user.first_name, user.last_name, user.username),
            "face": face(user.id)}


def role_of(shift) -> dict:
    return {"name": shift.role.name if shift.role else "", "color": shift.role.color if shift.role else "#5B6A62"}


def date_box(d) -> dict:
    return {"dow": date_format(d, "D").upper(), "day": d.day, "mon": date_format(d, "M").upper()}


def paid_text(shift) -> str:
    return _("%(h)s paid") % {"h": hours(shift.paid_minutes)}


def to_minutes(text):
    try:
        h, m = text.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def hhmm(minutes) -> str:
    minutes = min(minutes, 1439)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_day(text):
    try:
        return datetime.date.fromisoformat(text or "")
    except ValueError:
        return None


# ---------- the side panel: the Work group's badge ----------

def grabbable(user, mine=None) -> int:
    """How many shifts on Up for grabs they could take, plus what coworkers asked of them."""
    mine = list(swaps.upcoming(user)) if mine is None else mine
    roles = swaps.roles_of(user)
    open_, given = swaps.market(user)
    shifts = open_ + [o.shift for o in given]
    n = sum(1 for s in shifts if (not s.role_id or s.role_id in roles) and not _clash(mine, s))
    return n + ShiftOffer.objects.filter(to=user, status=ShiftOffer.ASKED).count()


def _clash(mine, shift, ignore=()):
    skip = {s.pk for s in ignore if s}
    return next((m for m in mine if m.pk != shift.pk and m.pk not in skip and m.date == shift.date
                 and m.start < shift.end and shift.start < m.end), None)


def side(user) -> dict:
    """What the side panel shows for staff: their roles under their name and the Up for grabs badge."""
    profile = getattr(user, "staff_profile", None)
    roles = [r.name for r in profile.roles.filter(archived=False)] if profile else []
    return {"sub": ", ".join(roles), "face": face(user.id), "badge": grabbable(user)}


# ---------- Shifts ----------

@staff_only
def shifts(request):
    from .account import _page
    user = request.user
    if request.method == "POST":
        _shift_post(request)
        return redirect("account_shifts")
    today = timezone.localdate()
    mine = list(swaps.upcoming(user).filter(user=user))
    offers = {o.shift_id: o for o in ShiftOffer.objects.filter(shift__in=mine, status__in=ShiftOffer.ACTIVE).select_related("to", "theirs")}
    wanted = {o.theirs_id: o for o in ShiftOffer.objects.filter(theirs__in=mine, status__in=ShiftOffer.ACTIVE).select_related("by")}
    picked = set(ShiftOffer.objects.filter(shift__in=mine, taken_by=user, status=ShiftOffer.DONE).values_list("shift_id", flat=True))
    rows = []
    for s in mine:
        o = offers.get(s.pk)
        row = {"s": s, "box": date_box(s.date), "role": role_of(s), "time": f"{date_format(s.date, 'D')} · {swaps.span(s)}",
               "can_act": not o and s.pk not in wanted and not swaps.started(s), "offer": o, "tag": "", "tone": ""}
        sub = [row["role"]["name"], paid_text(s)]
        if o and o.status == ShiftOffer.OPEN:
            row.update(tag=_("Up for grabs"), tone="amber", cancel=_("Take it back"))
            sub.append(_("still yours until someone grabs it"))
        elif o and o.status == ShiftOffer.ASKED:
            row.update(tag=_("Swap with %(name)s pending") % {"name": swaps.first_name(o.to)}, tone="blue", cancel=_("Cancel swap"))
            sub.append(_("you’d take their %(when)s, %(span)s") % {"when": day_label(o.theirs.date), "span": swaps.span(o.theirs)} if o.theirs
                       else _("handing it to %(name)s") % {"name": swaps.first_name(o.to)})
        elif o and o.status == ShiftOffer.ACCEPTED:
            row.update(tag=_("Waiting for a manager"), tone="blue", cancel=_("Cancel swap"))
            sub.append(_("%(name)s said yes") % {"name": swaps.first_name(o.to)})
        elif s.pk in wanted:
            w = wanted[s.pk]
            row.update(tag=_("Swap pending"), tone="blue")
            sub.append(_("%(name)s asked to swap for it") % {"name": swaps.first_name(w.by)} if w.status == ShiftOffer.ASKED
                       else _("swapping with %(name)s once a manager approves") % {"name": swaps.first_name(w.by)})
        else:
            if s.pk in picked:
                row.update(tag=_("Picked up"), tone="green")
            if s.note:
                sub.append(s.note)
        row["sub"] = " · ".join(x for x in sub if x)
        rows.append(row)
    # Hand-offs and swaps they said yes to, waiting for a manager
    for o in ShiftOffer.objects.filter(to=user, status=ShiftOffer.ACCEPTED, shift__date__gte=today).select_related("shift__role", "by"):
        rows.append({"s": o.shift, "box": date_box(o.shift.date), "role": role_of(o.shift), "time": f"{date_format(o.shift.date, 'D')} · {swaps.span(o.shift)}",
                     "tag": _("Waiting for a manager"), "tone": "blue", "can_act": False,
                     "sub": " · ".join(x for x in [role_of(o.shift)["name"], paid_text(o.shift),
                                                   _("from %(name)s · a manager has to OK it") % {"name": swaps.first_name(o.by)}] if x)})
    rows.sort(key=lambda r: (r["s"].date, r["s"].start))
    for t in TimeOff.objects.filter(user=user, status=TimeOff.PENDING, last_day__gte=today).order_by("first_day"):
        rows.append({"timeoff": True, "box": date_box(t.first_day), "time": _("%(when)s · time off") % {"when": off_when(t, with_hours=False)},
                     "sub": _("%(why)s · waiting for a manager. Shifts in these dates won’t be scheduled.") % {"why": category_label(t.category) or _("Time off")},
                     "tag": _("Requested"), "tone": "amber"})
    nxt = mine[0] if mine else None
    week_start = today - datetime.timedelta(days=today.weekday())
    week = Shift.objects.filter(user=user, published=True, deleted=False, date__gte=week_start, date__lt=week_start + datetime.timedelta(days=7))
    context = {"rows": rows, "next": _next_card(nxt, today) if nxt else None,
               "week_hours": hours(sum(s.paid_minutes for s in week)), **history(user, today), "modal": _modal(request, user, mine, offers)}
    return _page(request, "shifts.html", "shifts", context)


def _next_card(s, today) -> dict:
    when = _("today") if s.date == today else _("tomorrow") if s.date == today + datetime.timedelta(days=1) else day_label(s.date)
    sub = [role_of(s)["name"], paid_text(s)] + ([_("%(n)d min break") % {"n": s.break_minutes}] if s.break_minutes else [])
    return {"box": date_box(s.date), "when": when, "time": swaps.span(s), "sub": " · ".join(x for x in sub if x)}


def history(user, today) -> dict:
    """The last two weeks at the time clock: each stretch with when they clocked in and out, and whether they were late."""
    since = timezone.make_aware(datetime.datetime.combine(today - datetime.timedelta(days=13), datetime.time()))
    entries = list(TimeEntry.objects.filter(user=user, clock_in__gte=since, clock_out__isnull=False)
                   .select_related("shift__role").prefetch_related("breaks"))
    rows, total = [], 0
    for e in entries:
        start, end = timezone.localtime(e.clock_in), timezone.localtime(e.clock_out)
        worked = int(e.worked_seconds() // 60)
        total += worked
        late = 0
        if e.shift and e.shift.date == start.date():
            late = start.hour * 60 + start.minute - e.shift.start
        rows.append({"date": day_label(start.date()), "role": role_of(e.shift)["name"] if e.shift else "",
                     "color": role_of(e.shift)["color"] if e.shift else "#C9D3CD",
                     "clock": _("In %(a)s · out %(b)s") % {"a": clock(start.hour * 60 + start.minute, False), "b": clock(end.hour * 60 + end.minute, False)},
                     "hours": hours(worked), "late": late > 2, "flag": (_("Late %(n)d min") % {"n": late}) if late > 2 else _("On time")})
    first = today - datetime.timedelta(days=13)
    return {"hist": rows, "hist_stats": [{"v": hours(total), "l": _("%(a)s – %(b)s") % {"a": date_format(first, "M j"), "b": date_format(today, "M j")}},
                                         {"v": str(len(rows)), "l": ngettext("Shift worked", "Shifts worked", len(rows))}]}


def _modal(request, user, mine, offers):
    """The give-away or swap dialog over the Shifts page (?give=<shift> or ?swap=<shift>&who=<user>)."""
    key = "give" if request.GET.get("give") else "swap" if request.GET.get("swap") else ""
    if not key:
        return None
    shift = next((s for s in mine if str(s.pk) == request.GET.get(key) and not swaps.active_offer(s) and not swaps.started(s)), None)
    if shift is None:
        return None
    md = {"kind": key, "s": shift, "role": role_of(shift), "shift": f"{day_label(shift.date)} · {swaps.span(shift)}",
          "sub": f"{role_of(shift)['name']} · {paid_text(shift)}".lstrip(" ·")}
    if key == "give":
        md["eligible"] = (_("everyone with the %(role)s role") % {"role": shift.role.name}) if shift.role else _("everyone")
        return md
    people = []
    for u in (User.objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk)
              .select_related("staff_profile").prefetch_related("staff_profile__roles").order_by("first_name", "username")):
        why = swaps.why_not(u, shift)
        profile = getattr(u, "staff_profile", None)
        roles = ", ".join(r.name for r in profile.roles.all() if not r.archived) if profile else ""
        people.append({**person(u), "id": u.pk, "off": bool(why), "sub": why or roles, "on": str(u.pk) == request.GET.get("who")})
    people.sort(key=lambda p: p["off"])
    md["people"] = people
    md["any"] = any(not p["off"] for p in people)
    chosen = next((p for p in people if p["on"] and not p["off"]), None)
    md["who"] = chosen
    if chosen:
        coworker = User.objects.get(pk=chosen["id"])
        theirs = []
        for t in swaps.upcoming(coworker)[:20]:
            if swaps.started(t) or swaps.active_offer(t):
                continue
            why = swaps.why_not(user, t, ignore=[shift])
            theirs.append({"id": t.pk, "label": f"{day_label(t.date)} · {swaps.span(t)}", "sub": why or role_of(t)["name"], "off": bool(why)})
        md["theirs"] = theirs[:8]
    return md


def _shift_post(request):
    user, post = request.user, request.POST
    op = post.get("op", "")
    try:
        if op in ("give", "swap"):
            shift = Shift.objects.filter(pk=post.get("shift") or 0, user=user).first()
            if shift is None:
                raise swaps.SwapError(_("That isn’t one of your shifts."))
            if op == "give":
                swaps.give(shift, user, post.get("note", ""))
                messages.success(request, _("It’s up for grabs. You’re still on it until someone takes it."))
            else:
                coworker = User.objects.filter(pk=post.get("who") or 0, is_staff=True).first()
                theirs = None
                if post.get("theirs") not in (None, "", "none"):
                    theirs = Shift.objects.filter(pk=post.get("theirs")).first()
                    if theirs is None:
                        raise swaps.SwapError(_("That shift isn’t theirs to swap."))
                elif post.get("theirs") != "none":
                    raise swaps.SwapError(_("Pick one of their shifts, or hand it over."))
                swaps.ask(shift, user, coworker, theirs, post.get("note", ""))
                messages.success(request, _("Sent to %(name)s. A manager approves once they say yes.") % {"name": swaps.first_name(coworker)})
        elif op == "cancel":
            offer = ShiftOffer.objects.filter(pk=post.get("offer") or 0, by=user).first()
            if offer is None:
                raise swaps.SwapError(_("That can’t be cancelled any more."))
            was_open = offer.status == ShiftOffer.OPEN
            swaps.cancel(offer, user)
            messages.success(request, _("Taken back. It’s off Up for grabs.") if was_open else _("Swap request cancelled."))
    except swaps.SwapError as e:
        messages.error(request, str(e))


# ---------- Up for grabs ----------

@staff_only
def market(request):
    from .account import _page
    user = request.user
    if request.method == "POST":
        _market_post(request)
        return redirect(reverse("account_market") + ("?all=1" if request.POST.get("all") == "1" else ""))
    only_mine = request.GET.get("all") != "1"
    mine = list(swaps.upcoming(user))
    roles = swaps.roles_of(user)
    windows = {}
    open_, given = swaps.market(user)

    def card(shift, by=None, note=""):
        if shift.date not in windows:
            windows[shift.date] = reqs.windows_now(user, shift.date).get(str(shift.date.weekday()), [])
        avail = windows[shift.date]
        has_role = not shift.role_id or shift.role_id in roles
        clash = _clash(mine, shift)
        outside = not any(a <= shift.start and shift.end <= b for a, b in avail)
        b2b = None if clash else next((m for m in mine if m.date == shift.date and m.pk != shift.pk
                                       and (abs(m.end - shift.start) <= 60 or abs(shift.end - m.start) <= 60)), None)
        warn = (_("Needs the %(role)s role") % {"role": shift.role.name} if not has_role
                else _("Overlaps your %(span)s shift") % {"span": swaps.span(clash)} if clash
                else (_("Outside your availability") + ("" if avail else " " + _("(no %(day)s)") % {"day": reqs.weekday(shift.date.weekday(), plural=True)})) if outside
                else _("Right after your %(span)s shift") % {"span": swaps.span(b2b)} if b2b else "")
        return {"s": shift, "box": date_box(shift.date), "time": swaps.span(shift), "role": role_of(shift), "hours": paid_text(shift),
                "by": person(by) if by else None, "from": (_("Given away by %(name)s") % {"name": swaps.first_name(by)}) if by else (shift.note or _("Open shift")),
                "note": note, "warn": warn, "cant": not has_role or bool(clash), "has_role": has_role}

    sections = []
    cards = [card(s) for s in open_]
    cards = [c for c in cards if c["has_role"] or not only_mine]
    if cards:
        sections.append({"title": _("Open shifts"), "meta": _("Nobody’s scheduled yet"), "items": sorted(cards, key=lambda c: (c["s"].date, c["s"].start))})
    cards = [card(o.shift, o.by, o.note) for o in given]
    cards = [c for c in cards if c["has_role"] or not only_mine]
    if cards:
        sections.append({"title": _("Given away by coworkers"), "meta": _("They’re still on it until someone grabs it"),
                         "items": sorted(cards, key=lambda c: (c["s"].date, c["s"].start))})
    asks = []
    for o in swaps.asks(user):
        s = o.shift
        detail = [day_label(s.date), swaps.span(s), role_of(s)["name"]]
        if o.theirs:
            detail.append(_("you give your %(when)s, %(span)s") % {"when": day_label(o.theirs.date), "span": swaps.span(o.theirs)})
        else:
            detail.append(_("nothing to give back"))
        asks.append({"o": o, "by": person(o.by), "detail": " · ".join(x for x in detail if x),
                     "head": (_("%(name)s asked to swap shifts with you") if o.theirs else _("%(name)s asked you to take their shift")) % {"name": name_of(o.by)},
                     "accept": _("Swap") if o.theirs else _("Take it")})
    return _page(request, "market.html", "market", {"sections": sections, "asks": asks, "only_mine": only_mine})


def _market_post(request):
    user, post = request.user, request.POST
    op = post.get("op", "")
    try:
        if op == "grab":
            shift = Shift.objects.filter(pk=post.get("shift") or 0).first()
            if shift is None:
                raise swaps.SwapError(_("Someone else took that shift."))
            swaps.grab(user, shift)
            messages.success(request, _("%(when)s is yours. It’s on your schedule.") % {"when": day_label(shift.date)})
        elif op in ("accept", "decline"):
            offer = ShiftOffer.objects.filter(pk=post.get("offer") or 0, to=user).select_related("by").first()
            if offer is None:
                raise swaps.SwapError(_("That request isn’t waiting on you."))
            swaps.answer(offer, user, op == "accept")
            messages.success(request, _("Accepted. It’s yours once a manager approves.") if op == "accept"
                             else _("Declined. %(name)s gets a notification.") % {"name": swaps.first_name(offer.by)})
    except swaps.SwapError as e:
        messages.error(request, str(e))


# ---------- Availability and time off ----------

def category_label(key) -> str:
    return str(dict(CATEGORIES).get(key, key))


def off_when(t, with_hours=True) -> str:
    when = day_label(t.first_day) if t.first_day == t.last_day else f"{day_label(t.first_day)} – {day_label(t.last_day)}"
    if not with_hours:
        return when
    return f"{when} · " + (_("all day") if t.all_day or t.start is None else f"{clock(t.start, False)} – {clock(t.end, False)}")


def request_rows(user) -> list:
    rows = []
    for r in sorted(list(TimeOff.objects.filter(user=user).select_related("decided_by"))
                    + list(Availability.objects.filter(user=user).select_related("decided_by")),
                    key=lambda r: r.submitted_at, reverse=True)[:12]:
        sent = _("sent %(when)s") % {"when": date_format(timezone.localtime(r.submitted_at).date(), "M j")}
        if isinstance(r, TimeOff):
            sub = [category_label(r.category) or _("Time off"), sent]
            if r.status == TimeOff.PENDING:
                n = len(reqs.affected_shifts(r))
                if n:
                    sub.append(ngettext("%(n)d shift would open up", "%(n)d shifts would open up", n) % {"n": n})
            row = {"key": f"t{r.pk}", "kind": "timeoff", "kind_text": _("Time off"), "what": off_when(r)}
        else:
            before = reqs.current_windows(r)
            changed = sum(1 for d in range(7) if (r.windows or {}).get(str(d), []) != before.get(str(d), []))
            sub = [ngettext("%(n)d day changed", "%(n)d days changed", changed) % {"n": changed},
                   (_("until %(when)s") % {"when": day_label(r.until)}) if r.until else _("ongoing"), sent]
            row = {"key": f"a{r.pk}", "kind": "availability", "kind_text": _("Availability"),
                   "what": _("New availability from %(when)s") % {"when": day_label(r.effective)}}
        reply = ""
        if r.message:
            reply = f"“{r.message}”" + (f" · {swaps.first_name(r.decided_by)}" if r.decided_by else "")
        row.update(sub=" · ".join(sub), reply=reply, status=r.status,
                   status_text={"pending": _("Waiting"), "approved": _("Approved"), "declined": _("Declined")}[r.status],
                   withdraw=r.status == Availability.PENDING)
        rows.append(row)
    return rows


@staff_only
def time_page(request):
    from .account import _page
    user = request.user
    tab = "avail" if (request.POST.get("rq") or request.GET.get("rq")) == "avail" else "off"
    if request.method == "POST":
        if _time_post(request):
            return redirect(reverse("account_time") + ("?rq=avail" if tab == "avail" else ""))
    today = timezone.localdate()
    week = reqs.windows_now(user)
    current = reqs.availability_now(user)
    if current is None:
        note = _("No availability set: you can be scheduled any time")
    else:
        note = (_("Since %(a)s · until %(b)s") % {"a": date_format(current.effective, "M j"), "b": date_format(current.until, "M j")}) if current.until \
            else _("Since %(a)s") % {"a": date_format(current.effective, "M j")}
    chart = []
    for d in range(7):
        w = week.get(str(d), [])
        chart.append({"day": DAYS[d], "bars": [{"left": a / 14.4, "width": (b - a) / 14.4} for a, b in w],
                      "txt": ", ".join(f"{clock(a, False)} – {clock(b, False)}" for a, b in w) if w else _("Not available")})
    upcoming = list(swaps.upcoming(user).filter(user=user))
    post = request.POST if request.method == "POST" else {}
    default_from = today + datetime.timedelta(days=7)
    av_from = parse_day(post.get("from")) or (default_from if tab == "avail" else None) or default_from
    days = []
    for d in range(7):
        w = week.get(str(d), [])
        if post and tab == "avail":
            on = post.get(f"d{d}") == "1"
            a, b = to_minutes(post.get(f"d{d}_start")) or 540, to_minutes(post.get(f"d{d}_end")) or 1020
        else:
            on, a, b = bool(w), (w[0][0] if w else 540), (w[0][1] if w else 1020)
        days.append({"i": d, "day": DAYS[d], "on": on, "start": hhmm(a), "end": hhmm(b), "was": [hhmm(w[0][0]), hhmm(w[0][1])] if w else None})
    fo = {"from": post.get("first", "") if tab == "off" else "", "to": post.get("last", "") if tab == "off" else "",
          "all_day": post.get("all_day", "1") == "1" if post and tab == "off" else True,
          "start": post.get("start", "09:00") if tab == "off" else "09:00", "end": post.get("end", "13:00") if tab == "off" else "13:00",
          "cat": post.get("category", "Vacation") if tab == "off" else "Vacation", "note": post.get("reason", "") if tab == "off" else ""}
    fa = {"from": av_from.isoformat(), "until": post.get("until", "") if tab == "avail" else "", "note": post.get("reason", "") if tab == "avail" else "", "days": days}
    return _page(request, "time.html", "time", {
        "tab": tab, "avail_note": note, "chart": chart, "hours_marks": [{"left": h / 24 * 100, "label": clock(h * 60)} for h in (0, 4, 8, 12, 16, 20)],
        "requests": request_rows(user), "fo": fo, "fa": fa, "cats": CATEGORIES, "today": today.isoformat(),
        "shift_days": [{"date": s.date.isoformat(), "label": day_label(s.date)} for s in upcoming]})


def _time_post(request) -> bool:
    """Handle a form; True to redirect (done, or a withdraw), False to show the form again with what they typed."""
    user, post = request.user, request.POST
    op = post.get("op", "")
    try:
        if op == "timeoff":
            all_day = post.get("all_day") == "1"
            cat = post.get("category", "")
            reqs.ask_time_off(user, parse_day(post.get("first")), parse_day(post.get("last")), all_day,
                              to_minutes(post.get("start")), to_minutes(post.get("end")),
                              cat if cat in dict(CATEGORIES) else "", post.get("reason", ""))
            messages.success(request, _("Sent. You’ll get a notification when a manager answers."))
        elif op == "availability":
            windows = {}
            for d in range(7):
                a, b = to_minutes(post.get(f"d{d}_start")), to_minutes(post.get(f"d{d}_end"))
                if post.get(f"d{d}") == "1":
                    if a is None or b is None or b <= a:
                        raise reqs.RequestError(_("Check the times on %(day)s.") % {"day": DAYS[d]})
                    windows[str(d)] = [[a, b]]
                else:
                    windows[str(d)] = []
            reqs.ask_availability(user, windows, parse_day(post.get("from")), parse_day(post.get("until")), post.get("reason", ""))
            messages.success(request, _("Sent to your manager."))
        elif op == "withdraw":
            key = post.get("key", "")
            model = {"t": TimeOff, "a": Availability}.get(key[:1])
            req = model.objects.filter(pk=key[1:], user=user).first() if model and key[1:].isdigit() else None
            if req is None:
                raise reqs.RequestError(_("Only requests still waiting can be withdrawn."))
            reqs.withdraw(req, user)
            messages.success(request, _("Request withdrawn."))
        return True
    except reqs.RequestError as e:
        messages.error(request, str(e))
        return op == "withdraw"
