"""
Availability requests (design: AdminRequests): time off (TimeOff) and availability changes (Availability) staff ask
for, and the manager's decision. Approving can move the shifts it clashes with to open shifts; either way the person
is emailed (`request_decided`) with the manager's message.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from .models import Availability, ParSet, Shift, TimeOff
from .schedule import clock, unavailable

FULL_DAY = [[0, 1440]]


def current_windows(req: Availability) -> dict:
    """Their availability before this request: the approved one in effect when it would start (else any time)."""
    before = (Availability.objects.filter(user_id=req.user_id, status=Availability.APPROVED, effective__lte=req.effective)
              .filter(Q(until__isnull=True) | Q(until__gte=req.effective)).exclude(pk=req.pk).order_by("-effective", "-id").first())
    return {str(d): (before.windows or {}).get(str(d), []) for d in range(7)} if before else {str(d): FULL_DAY for d in range(7)}


def affected_shifts(req) -> list:
    """The person's upcoming shifts the request clashes with."""
    shifts = Shift.objects.filter(user_id=req.user_id, deleted=False).select_related("role")
    if isinstance(req, TimeOff):
        shifts = shifts.filter(date__gte=req.first_day, date__lte=req.last_day)
        if not req.all_day and req.start is not None:
            shifts = shifts.filter(start__lt=req.end, end__gt=req.start)
        return list(shifts)
    shifts = shifts.filter(date__gte=max(req.effective, timezone.localdate()))
    if req.until:
        shifts = shifts.filter(date__lte=req.until)
    out = []
    for s in shifts:
        gaps = unavailable([tuple(w) for w in (req.windows or {}).get(str(s.date.weekday()), [])])
        if any(s.start < b and s.end > a for a, b in gaps):
            out.append(s)
    return out


def describe(s) -> dict:
    return {"date": s.date.isoformat(), "role": s.role.name if s.role else "", "color": s.role.color if s.role else "#5B6A62",
            "start": s.start, "end": s.end}


def notes(req) -> list:
    """Things to weigh: others off at the same time, a busy seasonal par, short notice, a role nobody else covers."""
    out = []
    today = timezone.localdate()
    if isinstance(req, TimeOff):
        others = (TimeOff.objects.filter(first_day__lte=req.last_day, last_day__gte=req.first_day).exclude(pk=req.pk)
                  .exclude(status=TimeOff.DECLINED).select_related("user"))
        if others:
            out.append(("warn", _("Also off then: %(who)s.") % {"who": ", ".join(
                f"{o.user.first_name or o.user.username} (" + (_("pending") + ", " if o.status == TimeOff.PENDING else "")
                + span_text(o.first_day, o.last_day) + ")" for o in others)}))
        for season in ParSet.objects.filter(kind=ParSet.SEASON, start__lte=req.last_day, end__gte=req.first_day):
            out.append(("warn", _("Overlaps the %(name)s seasonal par, when you need extra people.") % {"name": season.name}))
        if req.status == TimeOff.PENDING and 0 <= (req.first_day - today).days <= 7:
            n = (req.first_day - today).days
            out.append(("warn", ngettext("Short notice: this starts in %(n)d day.", "Short notice: this starts in %(n)d days.", n) % {"n": n}))
    else:
        now = current_windows(req)
        lost_days = [d for d in range(7) if now.get(str(d)) and not (req.windows or {}).get(str(d))]
        profile = getattr(req.user, "staff_profile", None)
        for role in (profile.roles.all() if profile else []):
            others = User.objects.filter(is_staff=True, is_active=True, staff_profile__roles=role).exclude(pk=req.user_id).exists()
            if lost_days and not others:
                days = ", ".join(weekday(d, plural=True) for d in lost_days)
                out.append(("bad", _("%(days)s would have no %(role)s. Nobody else has the %(role)s role.") % {"days": days, "role": role.name}))
    return out


def weekday(d, plural=False):
    names = [_("Mondays"), _("Tuesdays"), _("Wednesdays"), _("Thursdays"), _("Fridays"), _("Saturdays"), _("Sundays")] if plural else \
        [_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")]
    return names[d]


def span_text(a, b) -> str:
    from django.utils.formats import date_format
    return date_format(a, "M j") if a == b else f"{date_format(a, 'M j')}–{date_format(b, 'M j')}"


@transaction.atomic
def decide(req, approve, by, message="", handle="open") -> int:
    """Approve or decline. On approval with handle "open", the clashing shifts become open shifts (unpublished).
    Returns how many shifts were opened. The person is emailed after the decision is saved."""
    shifts = affected_shifts(req)
    req.status = Availability.APPROVED if approve else Availability.DECLINED
    req.decided_at, req.decided_by, req.message = timezone.now(), by, message.strip()[:300]
    req.handle = (handle if handle in ("open", "keep") else "open") if approve and shifts else ""
    req.affected = [describe(s) for s in shifts]
    req.save()
    opened = 0
    if approve and req.handle == "open":
        for s in shifts:
            s.user, s.published = None, False
            s.save(update_fields=["user", "published"])
            opened += 1
    transaction.on_commit(lambda: _tell(req))
    return opened


def reopen(req):
    req.status, req.decided_at, req.decided_by, req.message, req.handle = Availability.PENDING, None, None, "", ""
    req.save(update_fields=["status", "decided_at", "decided_by", "message", "handle"])


def _tell(req):
    from misc_tools.email import EmailError, send
    if not req.user.email:
        return
    from django.utils import translation

    from users.models import language_of
    lang = language_of(req.user)
    with translation.override(lang):  # what it says is in their language, not the manager's
        what = (_("Time off: %(when)s") % {"when": span_text(req.first_day, req.last_day)} if isinstance(req, TimeOff)
                else _("Availability from %(when)s") % {"when": span_text(req.effective, req.effective)})
        decision = str(_("approved") if req.status == Availability.APPROVED else _("declined"))
    try:
        send("request_decided", req.user.email, {"name": req.user.first_name or req.user.username, "what": str(what),
                                                  "decision": decision, "message": req.message}, language=lang)
    except EmailError:
        pass


def times_text(windows) -> str:
    return ", ".join(f"{clock(a)}–{clock(b)}" for a, b in windows) if windows else _("Unavailable")
