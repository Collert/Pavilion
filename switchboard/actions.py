"""
The actions Switchboard can ask for (protocol §3 and §5; listed in manifest.py). Each is `fn(call, params) -> dict`
whose keys are the manifest's `output`. `params` arrive checked against the manifest (required ones present, values
turned into str / bool / int / Decimal / aware datetime). An ActionError becomes the protocol's error answer; its
message is shown to a non-technical admin in Switchboard's run log, in English like the manifest.

People are matched by email. A staff member's id here is their account's id (auth User), as a string.
"""
import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import IssuedCard, SwitchboardConnection

EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DEFAULT_LABEL = "Switchboard"
MAX_AMOUNT = Decimal("9999.99")


class ActionError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def invalid(message) -> ActionError:
    return ActionError(422, "invalid_params", message)


def conflict(message) -> ActionError:
    return ActionError(409, "conflict", message)


@dataclass
class Call:
    connection: SwitchboardConnection
    request: object  # the HttpRequest (links in emails are built from its host)


# ---------- params ----------

def _convert(spec, value):
    kind, label = spec["type"], spec["label"]
    if kind == "string":
        if isinstance(value, (dict, list, bool)) or value is None:
            raise invalid(f"{label} must be text.")
        return str(value).strip()
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "1", "0", ""):
            return value.strip().lower() in ("true", "yes", "1")
        raise invalid(f"{label} must be true or false.")
    if kind in ("money", "number", "int"):
        if isinstance(value, bool):
            raise invalid(f"{label} must be a number.")
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise invalid(f"{label} must be a number.")
        if not number.is_finite():
            raise invalid(f"{label} must be a number.")
        if kind == "int":
            if number != number.to_integral_value():
                raise invalid(f"{label} must be a whole number.")
            return int(number)
        return number
    if kind == "datetime":
        try:
            moment = datetime.datetime.fromisoformat(str(value).strip())
        except ValueError:
            raise invalid(f"{label} must be a date and time like 2026-11-19T16:00:00-08:00.")
        # without an offset it's taken as the store's own time
        return timezone.make_aware(moment) if timezone.is_naive(moment) else moment
    return value


def clean(action, body) -> dict:
    """The body's params as the action wants them. Missing optional params are left out (protocol §1)."""
    out = {}
    for spec in action["params"]:
        value = body.get(spec["key"])
        if value is None or (isinstance(value, str) and not value.strip()):
            if spec.get("required"):
                raise invalid(f"{spec['label']} is missing.")
            continue
        out[spec["key"]] = _convert(spec, value)
    return out


# ---------- people ----------

def full_name(user) -> str:
    return user.get_full_name() or user.username


def _roles(user) -> list:
    from users.models import Staff
    profile = Staff.objects.filter(user=user).first()
    return list(profile.roles.filter(archived=False)) if profile else []


def _person(user) -> dict:
    return {"staff_id": str(user.pk), "name": full_name(user), "email": user.email}


def staff_by_email(email):
    return User.objects.filter(is_staff=True, email__iexact=email).order_by("-is_active", "id").first()


def staff_by_id(value):
    """The staff member a `staff_id` names, or a 422."""
    user = User.objects.filter(pk=int(value), is_staff=True).first() if str(value).isdigit() else None
    if user is None:
        raise invalid(f"There is no staff member with the ID {value}. Use the Staff ID from Find staff member.")
    return user


def _role_named(name):
    from users.models import Role
    role = Role.objects.filter(name__iexact=name, archived=False).first()
    if role is None:
        names = ", ".join(Role.objects.filter(archived=False).values_list("name", flat=True)) or "none yet"
        raise invalid(f"Pavilion has no role called “{name}”. Its roles: {names}.")
    return role


def staff_find(call, p):
    user = staff_by_email(p["email"])
    if user is None:
        return {"found": False}
    return {"found": True, **_person(user), "roles": ", ".join(r.name for r in _roles(user)), "active": user.is_active}


def staff_ensure(call, p):
    """What the admin's Staff page does when someone is added (admin_portal/staff.py): an existing account becomes
    staff, else a new one is made without a password. Owners are never changed."""
    from admin_portal import staff as staff_page
    email = p["email"]
    if not EMAIL.match(email) or len(email) > 150:
        raise invalid("Email doesn’t look like an email address.")
    role = _role_named(p["role"]) if p.get("role") else None
    user = staff_by_email(email)
    created = False
    if user is None:
        created = True
        account = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).order_by("id").first()
        if account is not None:  # a customer's account: it becomes staff, as "Existing account" does on the Staff page
            if account.is_superuser:
                raise conflict("That email belongs to an owner’s account, which Switchboard can’t change.")
            user = staff_page.promote(account)
        else:
            first, _sep, last = p["name"].partition(" ")
            user, problem = staff_page.create({"first": first, "last": last.strip(), "email": email})
            if user is None:
                raise invalid(str(problem))
    if role is not None and not user.is_superuser:
        profile = staff_page.profile(user)
        if not profile.roles.filter(pk=role.pk).exists():
            profile.roles.add(role)
            owners = staff_page.owners_group()  # a role never makes an owner here
            user.groups.add(*[g for g in role.groups.all() if owners is None or g.pk != owners.pk])
    if created and p.get("invite"):
        request = call.request
        transaction.on_commit(lambda: _invite(request, user))
    return {**_person(user), "created": created}


def _invite(request, user):
    import logging

    from admin_portal import staff as staff_page
    try:
        staff_page.invite(request, user)
    except Exception:  # the account is made either way; a manager can send the link again from the Staff page
        logging.getLogger(__name__).warning("Couldn't email the staff invitation to user %s", user.pk, exc_info=True)


# ---------- staff cards ----------

def _card_type(label, amount):
    """The balance card type called `label`: the store's own, or a new one-time balance type."""
    from gift_cards.models import StaffCardType
    t = StaffCardType.objects.filter(name__iexact=label, archived=False).first()
    if t is None:
        last = StaffCardType.objects.order_by("-position").values_list("position", flat=True).first()
        return StaffCardType.objects.create(name=label, kind=StaffCardType.BALANCE, amount=amount, refill="never", leftover="reset",
                                            cap=max(amount, Decimal("50.00")), channels=[c for c, _label in StaffCardType.CHANNELS],
                                            position=(last or 0) + 1)
    if t.kind != StaffCardType.BALANCE:
        raise conflict(f"“{t.name}” is a discount card on Pavilion’s Staff cards page, so it can’t hold a balance. Use another label.")
    return t


def staff_card_issue(call, p):
    """A balance card worth `amount` of the type called `label` (gift_cards/staff_cards.py has the rules). The type's
    own schedule refills it afterwards; a type made here never refills. One live card per person and type, as on the
    Staff cards page."""
    from gift_cards.models import StaffCard
    from gift_cards.staff_cards import money
    user = staff_by_id(p["staff_id"])
    if not user.is_active:
        raise conflict(f"{full_name(user)} can’t sign in to Pavilion any more, so a card wouldn’t work.")
    amount = money(p["amount"])
    if amount <= 0 or amount > MAX_AMOUNT:
        raise invalid(f"Amount must be more than 0 and at most {MAX_AMOUNT}.")
    label = p.get("label") or DEFAULT_LABEL
    if len(label) > 30:
        raise invalid("Label can be up to 30 characters.")
    replaced = None
    now = timezone.now()
    connection = call.connection
    if p.get("replace_previous"):
        mine = Q(connection=connection) | Q(connection__isnull=True, host=connection.host)
        earlier = StaffCard.objects.filter(user=user, revoked_at__isnull=True,
                                           pk__in=IssuedCard.objects.filter(mine).values("card_id")).order_by("-issued_at", "-id")
        ids = list(earlier.values_list("pk", flat=True))
        if ids:
            StaffCard.objects.filter(pk__in=ids).update(revoked_at=now)
            replaced = str(ids[0])
    t = _card_type(label, amount)
    if t.cards.filter(user=user, revoked_at__isnull=True).exists():
        raise conflict(f"{full_name(user)} already has a {t.name} card. Turn on “Replace the previous card”, use another label, "
                       "or revoke the card on Pavilion’s Staff cards page.")
    card = StaffCard.objects.create(type=t, user=user, number=StaffCard.new_number(), balance=amount, refilled_at=now, issued_at=now)
    IssuedCard.objects.create(connection=connection, host=connection.host[:255], card=card)
    return {"card_id": str(card.pk), "amount": float(amount), "label": t.name, "replaced": replaced}


# ---------- shifts ----------

def shift_add(call, p):
    """A published team.Shift (dates and minutes since midnight in the store's time zone), which is what the time
    clock looks for. Publishing tells the person by email, as the Shifts page's Publish does."""
    from team import schedule
    from team.models import Shift
    user = staff_by_id(p["staff_id"])
    start, end = timezone.localtime(p["starts_at"]), timezone.localtime(p["ends_at"])
    if end <= start:
        raise invalid("Ends must be after Starts.")
    day = start.date()
    first = start.hour * 60 + start.minute
    last = end.hour * 60 + end.minute
    if end.date() != day:
        if end.date() != day + datetime.timedelta(days=1) or last != 0:
            raise invalid("A shift can’t run past midnight in Pavilion. Add one shift up to midnight and another for the next day.")
        last = 1440
    if last <= first:
        raise invalid("A shift must be at least a minute long.")
    if p.get("role"):
        role = _role_named(p["role"])
    else:
        role = next(iter(_roles(user)), None)
    note = p.get("note", "")
    if len(note) > 80:
        raise invalid("Note can be up to 80 characters.")
    theirs = list(Shift.objects.filter(user=user, date=day, deleted=False))
    same = next((s for s in theirs if s.start == first and s.end == last and (not p.get("role") or s.role_id == role.pk)), None)
    if same is not None:
        return {"shift_id": str(same.pk), "status": "published" if same.published else "unpublished", "created": False}
    clash = next((s for s in theirs if first < s.end and last > s.start), None)
    if clash is not None:
        raise conflict(f"{full_name(user)} already has a shift on {day:%b} {day.day} from {schedule.clock(clash.start, False)} to "
                       f"{schedule.clock(clash.end, False)}, which overlaps this one.")
    shift = Shift(user=user, role=role, date=day, start=first, end=last, note=note or _("Added through Switchboard")[:80], published=True)
    shift.published_as = schedule.snapshot(shift)
    shift.save()
    from pavilion.realtime import publish
    publish("schedule")
    transaction.on_commit(lambda: _tell(user.pk))
    return {"shift_id": str(shift.pk), "status": "published", "created": True}


def _tell(user_id):
    import logging

    from team import schedule
    try:
        schedule._notify({user_id})
    except Exception:
        logging.getLogger(__name__).warning("Couldn't email user %s their new shift", user_id, exc_info=True)


RUN = {"staff.find": staff_find, "staff.ensure": staff_ensure, "staff_card.issue": staff_card_issue, "shift.add": shift_add}
