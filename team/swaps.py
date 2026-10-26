"""
Passing shifts on, from the staff side of the account (design: StoreAccount's staff views; team.models.ShiftOffer).

- Give away: the shift goes to Up for grabs for everyone with its role. The first who grabs it gets it straight
  away (it's still the giver's until then). Open shifts (nobody on them) are grabbed the same way.
- Hand-off or swap: asked of one coworker (a swap also names one of their shifts the asker takes in return). The
  coworker says yes or no; a yes waits for a manager on Availability requests, who approves or declines.

Only published, upcoming shifts move. Someone can take a shift only with its role and without another shift of
theirs overlapping it. Reassigned shifts stay published (published_as follows), so they don't wait for Publish.
"""
import logging

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Shift, ShiftOffer
from .schedule import clock, snapshot

logger = logging.getLogger(__name__)


class SwapError(Exception):
    pass


def roles_of(user) -> set:
    profile = getattr(user, "staff_profile", None)
    return set(profile.roles.filter(archived=False).values_list("id", flat=True)) if profile else set()


def upcoming(user):
    """Their published shifts from today on."""
    return (Shift.objects.filter(user=user, published=True, deleted=False, date__gte=timezone.localdate())
            .select_related("role").order_by("date", "start"))


def started(shift, now=None) -> bool:
    now = timezone.localtime(now or timezone.now())
    return shift.date < now.date() or (shift.date == now.date() and shift.start <= now.hour * 60 + now.minute)


def overlap(user, shift, ignore=()):
    """Another shift of theirs the same day that overlaps `shift` (not counting `ignore`)."""
    return (Shift.objects.filter(user=user, date=shift.date, deleted=False, start__lt=shift.end, end__gt=shift.start)
            .exclude(pk=shift.pk).exclude(pk__in=[s.pk for s in ignore if s]).first())


def span(shift) -> str:
    return f"{clock(shift.start, False)} – {clock(shift.end, False)}"


def why_not(user, shift, ignore=()) -> str:
    """Why `user` can't take `shift`, or ""."""
    if started(shift):
        return _("It has already started.")
    if shift.role_id and shift.role_id not in roles_of(user):
        return _("Needs the %(role)s role") % {"role": shift.role.name}
    clash = overlap(user, shift, ignore)
    if clash:
        return _("Overlaps your %(span)s shift") % {"span": span(clash)}
    return ""


def active_offer(shift):
    """The open offer on a shift: passing it on, or a swap that would take it in return."""
    return (ShiftOffer.objects.filter(models.Q(shift=shift) | models.Q(theirs=shift), status__in=ShiftOffer.ACTIVE)
            .select_related("by", "to").first())


def _mine(shift, user):
    if shift.user_id != user.id or not shift.published or shift.deleted:
        raise SwapError(_("That isn’t one of your shifts."))
    if started(shift):
        raise SwapError(_("That shift has already started."))
    if active_offer(shift):
        raise SwapError(_("You’re already passing that shift on."))


def _move(shift, user):
    shift.user = user
    shift.save(update_fields=["user", "updated_at"])
    shift.published_as = snapshot(shift)  # staff already know: no need to publish it again
    shift.save(update_fields=["published_as"])


@transaction.atomic
def give(shift, user, note="") -> ShiftOffer:
    _mine(shift, user)
    return ShiftOffer.objects.create(shift=shift, kind=ShiftOffer.GIVE, by=user, note=note.strip()[:200])


@transaction.atomic
def grab(user, shift) -> Shift:
    """Take an open shift or one given away. Raises SwapError when they can't."""
    shift = Shift.objects.select_for_update().select_related("role").get(pk=shift.pk)
    offer = shift.offers.filter(status=ShiftOffer.OPEN, kind=ShiftOffer.GIVE).first()
    if not shift.published or shift.deleted or (shift.user_id and not offer):
        raise SwapError(_("Someone else took that shift."))
    if shift.user_id == user.id:
        raise SwapError(_("That’s already your shift."))
    problem = why_not(user, shift)
    if problem:
        raise SwapError(problem)
    giver = shift.user
    _move(shift, user)
    if offer:
        offer.status, offer.taken_by, offer.decided_at = ShiftOffer.DONE, user, timezone.now()
        offer.save(update_fields=["status", "taken_by", "decided_at"])
        _tell(offer, "shift_taken", giver)
    return shift


@transaction.atomic
def ask(shift, user, coworker, theirs=None, note="") -> ShiftOffer:
    """A hand-off (no `theirs`) or a swap for one of `coworker`'s shifts."""
    _mine(shift, user)
    if coworker is None or coworker.pk == user.pk or not coworker.is_active:
        raise SwapError(_("Pick who to ask."))
    if theirs is not None and (theirs.user_id != coworker.pk or not theirs.published or theirs.deleted or started(theirs)):
        raise SwapError(_("That shift isn’t theirs to swap."))
    problem = why_not(coworker, shift, ignore=[theirs])
    if problem:
        raise SwapError(_("%(name)s can’t take it: %(why)s") % {"name": coworker.first_name or coworker.username, "why": problem.lower()})
    if theirs is not None:
        problem = why_not(user, theirs, ignore=[shift])
        if problem:
            raise SwapError(_("You can’t take theirs: %(why)s") % {"why": problem.lower()})
    offer = ShiftOffer.objects.create(shift=shift, kind=ShiftOffer.SWAP if theirs else ShiftOffer.HANDOFF, by=user, to=coworker,
                                      theirs=theirs, note=note.strip()[:200], status=ShiftOffer.ASKED)
    _tell(offer, "shift_swap", coworker)
    return offer


def answer(offer, user, yes) -> ShiftOffer:
    if offer.to_id != user.id or offer.status != ShiftOffer.ASKED:
        raise SwapError(_("That request isn’t waiting on you."))
    if yes:
        problem = why_not(user, offer.shift, ignore=[offer.theirs])
        if problem:
            raise SwapError(problem)
    offer.status = ShiftOffer.ACCEPTED if yes else ShiftOffer.DECLINED
    offer.answered_at = timezone.now()
    offer.save(update_fields=["status", "answered_at"])
    if not yes:
        _tell(offer, "shift_swap_decided", offer.by)
    return offer


def cancel(offer, user) -> ShiftOffer:
    if offer.by_id != user.id or offer.status not in ShiftOffer.ACTIVE:
        raise SwapError(_("That can’t be cancelled any more."))
    offer.status = ShiftOffer.CANCELLED
    offer.save(update_fields=["status"])
    return offer


@transaction.atomic
def decide(offer, approve, by, message="") -> ShiftOffer:
    """A manager's yes or no to a hand-off or swap the coworker agreed to."""
    offer = ShiftOffer.objects.select_for_update().select_related("shift", "theirs", "by", "to").get(pk=offer.pk)
    if offer.status != ShiftOffer.ACCEPTED:
        raise SwapError(_("That request isn’t waiting for a manager."))
    if approve:
        if offer.shift.user_id != offer.by_id or (offer.theirs and offer.theirs.user_id != offer.to_id):
            raise SwapError(_("The shifts changed since they asked. Decline it and they can ask again."))
        _move(offer.shift, offer.to)
        if offer.theirs:
            _move(offer.theirs, offer.by)
    offer.status = ShiftOffer.DONE if approve else ShiftOffer.DECLINED
    offer.decided_at, offer.decided_by, offer.message = timezone.now(), by, message.strip()[:200]
    offer.save(update_fields=["status", "decided_at", "decided_by", "message"])
    for person in (offer.by, offer.to):
        _tell(offer, "shift_swap_decided", person)
    return offer


def market(user):
    """(open shifts, shifts coworkers gave away) from now on, soonest first."""
    today = timezone.localdate()
    base = Shift.objects.filter(published=True, deleted=False, date__gte=today).select_related("role", "user")
    open_ = [s for s in base.filter(user__isnull=True) if not started(s)]
    given = [o for o in ShiftOffer.objects.filter(kind=ShiftOffer.GIVE, status=ShiftOffer.OPEN, shift__date__gte=today)
             .exclude(by=user).select_related("shift__role", "by") if not started(o.shift)]
    return open_, given


def asks(user):
    return list(ShiftOffer.objects.filter(to=user, status=ShiftOffer.ASKED).select_related("shift__role", "theirs__role", "by"))


def first_name(user) -> str:
    return (user.first_name or user.username) if user else ""


def _tell(offer, kind, person):
    """Email someone about an offer, in their language, after it's saved. A failure is only logged."""
    from misc_tools.email import EmailError, send
    from users.models import language_of
    if not person or not person.email:
        return

    def go():
        from django.utils import translation
        from django.utils.formats import date_format
        lang = language_of(person)
        with translation.override(lang):
            shift = offer.shift
            what = f"{date_format(shift.date, 'D M j')} · {span(shift)}" + (f" · {shift.role.name}" if shift.role else "")
            theirs = f"{date_format(offer.theirs.date, 'D M j')} · {span(offer.theirs)}" if offer.theirs else ""
            data = {"name": person.first_name or person.username, "from": offer.by.first_name or offer.by.username,
                    "who": first_name(offer.taken_by or offer.to),
                    "shift": what, "theirs": theirs, "note": offer.note, "message": offer.message,
                    "decision": str(_("approved") if offer.status == ShiftOffer.DONE else _("declined"))}
        try:
            send(kind, person.email, data, language=lang)
        except EmailError:
            logger.exception("Shift email %s for offer %s wasn't sent", kind, offer.pk)

    transaction.on_commit(go)
