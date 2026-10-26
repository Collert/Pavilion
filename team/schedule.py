"""
Working out the schedule: what a day needs (pars), what's wrong with a shift (time off, unavailable, overlapping,
not their role), hours per week, and publishing changes to staff.
"""
import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Availability, ParSet, Shift, TimeOff

DAY = 1440


def weekly() -> ParSet:
    """The weekly pars (one set; made when first asked for)."""
    pars = ParSet.objects.filter(kind=ParSet.WEEKLY).first()
    return pars or ParSet.objects.create(kind=ParSet.WEEKLY, name="Weekly pars", days=list(range(7)), pars={})


def count_at(windows, minute) -> int:
    """How many people `windows` ([[from, to, people], ...]) ask for at `minute` (overlaps: the higher number)."""
    return max((w[2] for w in windows or [] if w[0] <= minute < w[1]), default=0)


def needs(day, sets=None) -> dict:
    """{role id: [people needed for each of the 24 hours]} on a date: the weekly pars, then any seasonal pars running
    that day (adding to them or replacing the day)."""
    sets = sets if sets is not None else list(ParSet.objects.all())
    weekday = str(day.weekday())
    base = next((s for s in sets if s.kind == ParSet.WEEKLY), None)
    layers = [((base.pars or {}).get(weekday) or {}, ParSet.REPLACE)] if base else []
    for s in sets:
        if s.kind == ParSet.SEASON and s.start and s.end and s.start <= day <= s.end and day.weekday() in (s.days or []):
            layers.append(((s.pars or {}).get(weekday) or {}, s.mode))
    out = {}
    for pars, mode in layers:
        if mode == ParSet.REPLACE:
            out = {}
        for role, windows in pars.items():
            hours = [count_at(windows, h * 60 + 30) for h in range(24)]
            prev = out.get(int(role), [0] * 24)
            out[int(role)] = [a + b for a, b in zip(prev, hours)]
    return {r: hours for r, hours in out.items() if any(hours)}


def availability_on(user_ids, day) -> dict:
    """{user id: [(from, to), ...] they can work on `day`} for people with approved availability in effect."""
    found = {}
    for a in (Availability.objects.filter(user_id__in=user_ids, status=Availability.APPROVED, effective__lte=day)
              .filter(Q(until__isnull=True) | Q(until__gte=day)).order_by("user_id", "-effective", "-id")):
        if a.user_id not in found:
            found[a.user_id] = [tuple(w) for w in (a.windows or {}).get(str(day.weekday()), [])]
    return found


def time_off_on(user_ids, day) -> dict:
    """{user id: TimeOff} approved and covering `day`."""
    return {t.user_id: t for t in TimeOff.objects.filter(user_id__in=user_ids, status=TimeOff.APPROVED, first_day__lte=day, last_day__gte=day)}


def unavailable(windows) -> list:
    """The parts of a day outside someone's available windows."""
    gaps, at = [], 0
    for a, b in sorted(windows):
        if a > at:
            gaps.append((at, a))
        at = max(at, b)
    if at < DAY:
        gaps.append((at, DAY))
    return gaps


def clock(minutes, short=True) -> str:
    if minutes >= DAY:
        return "12a" if short else _("Midnight")
    h, m = divmod(minutes, 60)
    hour = (h + 11) % 12 + 1
    if short:
        return f"{hour}{':%02d' % m if m else ''}{'a' if h < 12 else 'p'}"
    return f"{hour}:{m:02d} {'AM' if h < 12 else 'PM'}"


def issues(shift, context, check_role=True) -> list:
    """What's wrong with a shift: time off, outside their availability, overlapping another of theirs, a role they
    don't have. `context`: {"time_off": {uid: TimeOff}, "avail": {uid: windows}, "shifts": [Shift] that day,
    "roles": {uid: {role ids}}, "names": {uid: first name}, "role_names": {id: name}}."""
    uid = shift.user_id
    if not uid:
        return []
    out = []
    name = context["names"].get(uid, "")
    off = context["time_off"].get(uid)
    if off and (off.all_day or (off.start is not None and shift.start < off.end and shift.end > off.start)):
        out.append(_("%(name)s is off (%(what)s).") % {"name": name, "what": (off.category or _("time off")).lower()})
    windows = context["avail"].get(uid)
    if windows is not None:
        for a, b in unavailable(windows):
            if shift.start < b and shift.end > a:
                out.append(_("%(name)s isn’t available %(from)s–%(to)s.") % {"name": name, "from": clock(a), "to": clock(b)})
                break
    for other in context["shifts"]:
        if other.pk != shift.pk and other.user_id == uid and not other.deleted and shift.start < other.end and shift.end > other.start:
            out.append(_("Overlaps their %(role)s shift %(from)s–%(to)s.") % {
                "role": context["role_names"].get(other.role_id, _("other")).lower(), "from": clock(other.start), "to": clock(other.end)})
    if check_role and shift.role_id and shift.role_id not in context["roles"].get(uid, set()):
        out.append(_("%(name)s doesn’t have the %(role)s role.") % {"name": name, "role": context["role_names"].get(shift.role_id, "")})
    return out


def snapshot(shift) -> dict:
    return {"user": shift.user_id, "role": shift.role_id, "date": shift.date.isoformat(), "start": shift.start, "end": shift.end,
            "break": shift.break_minutes, "note": shift.note}


def changed(shift):
    """Mark a shift edited: it waits for Publish."""
    shift.published = False


def unpublished():
    return Shift.objects.filter(published=False)


@transaction.atomic
def publish(request=None) -> int:
    """Publish every change: deleted shifts go, the rest become what staff see. Each person whose schedule changed
    is emailed (their old and new shifts). Returns how many people were told."""
    told = set()
    for shift in list(unpublished().select_related("user")):
        before = shift.published_as or {}
        for uid in {before.get("user"), shift.user_id} - {None}:
            told.add(uid)
        if shift.deleted:
            shift.delete()
            continue
        shift.published, shift.published_as = True, snapshot(shift)
        shift.save(update_fields=["published", "published_as"])
    if told:
        transaction.on_commit(lambda: _notify(told))
    return len(told)


def _notify(user_ids):
    from django.contrib.auth.models import User

    from django.utils import translation
    from django.utils.formats import date_format

    from misc_tools.email import EmailError, send
    from users.models import language_of
    today = timezone.localdate()
    for user in User.objects.filter(pk__in=user_ids).exclude(email="").select_related("preference"):
        upcoming = Shift.objects.filter(user=user, date__gte=today, date__lt=today + datetime.timedelta(days=14), published=True,
                                        deleted=False).select_related("role")
        lang = language_of(user)
        with translation.override(lang):  # dates in their language
            lines = [f"{date_format(s.date, 'D M j')}: {clock(s.start, False)} – {clock(s.end, False)}" + (f" · {s.role.name}" if s.role else "")
                     + (f" · {s.note}" if s.note else "") for s in upcoming]
        try:
            send("schedule_changed", user.email, {"name": user.first_name or user.username, "shifts": lines}, language=lang)
        except EmailError:
            pass
