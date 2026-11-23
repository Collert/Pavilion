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
        out[spec["key"]] = _within(spec, _convert(spec, value))
    return out


def _within(spec, value):
    """The limits the manifest states for a param (protocol §1): Switchboard checks them too, before it sends."""
    label = spec["label"]
    if "max_length" in spec and isinstance(value, str) and len(value) > spec["max_length"]:
        raise invalid(f"{label} can be up to {spec['max_length']} characters.")
    if "choices" in spec and value not in [choice["value"] for choice in spec["choices"]]:
        raise invalid(f"{label} must be one of: {', '.join(str(choice['value']) for choice in spec['choices'])}.")
    if not isinstance(value, (bool, str)) and ("min" in spec or "max" in spec):
        low, high = spec.get("min"), spec.get("max")
        if (low is not None and value < Decimal(str(low))) or (high is not None and value > Decimal(str(high))):
            raise invalid(f"{label} must be " + (f"from {low} to {high}." if low is not None and high is not None
                                                 else f"at least {low}." if low is not None else f"at most {high}."))
    return value


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


def staff_invite(call, p):
    """The Staff page's Invite: the link where they set their own password (admin_portal/staff.py: invite)."""
    from admin_portal import staff as staff_page
    from misc_tools.email import EmailError
    user = staff_by_id(p["staff_id"])
    if user.is_superuser:
        raise conflict("That is an owner’s account, which Switchboard can’t change.")
    if not user.is_active:
        raise conflict(f"{full_name(user)} can’t sign in to Pavilion any more. Turn their account back on on the Staff page first.")
    if not user.email:
        raise conflict(f"{full_name(user)} has no email address in Pavilion.")
    try:
        sent = bool(staff_page.invite(call.request, user))
    except EmailError as e:
        raise conflict(f"The invitation wasn’t sent: {e}")
    if not sent:
        raise conflict(NO_EMAIL)
    return {**_person(user), "sent": True}


def team_on_now(call, p):
    from team import clock
    on = clock.on_now()
    return {"count": len(on), "names": ", ".join(full_name(entry.user) for entry, _resting in on),
            "on_break": sum(1 for _entry, resting in on if resting)}


# ---------- shifts ----------

def _moment(day, minute):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min) + datetime.timedelta(minutes=minute))


def iso(moment) -> str:
    return timezone.localtime(moment).isoformat(timespec="seconds")


def day_of(p, key, label):
    """A "2026-12-24" param as a date (None when it was left out), or a 422."""
    if not p.get(key):
        return None
    try:
        return datetime.date.fromisoformat(p[key][:10])
    except ValueError:
        raise invalid(f"{label} must be a day like 2026-12-24.")


def money(value) -> float:
    return float(value or 0)


def number(value):
    value = round(float(value or 0), 3)
    return int(value) if value == int(value) else value


def shift_cancel(call, p):
    """The Shifts page's Delete (admin_portal/shifts.py: delete): a shift staff were told about is marked deleted and
    waits for Publish, which tells them; one that never was published just goes."""
    from admin_portal import shifts as shifts_page
    from pavilion.realtime import publish
    from team import swaps
    from team.models import Shift
    shift = Shift.objects.filter(pk=int(p["shift_id"])).first() if p["shift_id"].isdigit() else None
    if shift is None:
        if not p["shift_id"].isdigit():
            raise invalid("Shift ID must be the number from Add a shift or Next shift.")
        return {"shift_id": p["shift_id"], "status": "removed", "cancelled": False}
    if shift.deleted:
        return {"shift_id": str(shift.pk), "status": "waiting_for_publish", "cancelled": False}
    if swaps.started(shift):
        raise conflict("That shift has already started, so it can’t be cancelled.")
    pk, told = shift.pk, bool(shift.published_as)
    shifts_page.delete(shift)
    publish("schedule")
    return {"shift_id": str(pk), "status": "waiting_for_publish" if told else "removed", "cancelled": True}


def shift_next(call, p):
    from team.models import Shift
    user = staff_by_id(p["staff_id"])
    now = timezone.localtime()
    today = now.date()
    coming = [s for s in Shift.objects.filter(user=user, published=True, deleted=False, date__gte=today,
                                              date__lt=today + datetime.timedelta(days=14)).select_related("role").order_by("date", "start")
              if s.date > today or s.end > now.hour * 60 + now.minute]
    if not coming:
        later = (Shift.objects.filter(user=user, published=True, deleted=False, date__gte=today + datetime.timedelta(days=14))
                 .select_related("role").order_by("date", "start").first())
        if later is None:
            return {"found": False}
        coming, upcoming = [later], 0
    else:
        upcoming = len(coming)
    s = coming[0]
    return {"found": True, "shift_id": str(s.pk), "starts_at": iso(_moment(s.date, s.start)), "ends_at": iso(_moment(s.date, s.end)),
            "role": s.role.name if s.role else "", "upcoming": upcoming}


def time_off_request(call, p):
    """What asking for time off in their store account does (team/requests.py: ask_time_off): a pending request a
    manager answers on the admin's Availability requests page."""
    from team import requests
    from team.models import TimeOff
    user = staff_by_id(p["staff_id"])
    first = day_of(p, "first_day", "First day off")
    last = day_of(p, "last_day", "Last day off") or first
    same = TimeOff.objects.filter(user=user, first_day=first, last_day=last, all_day=True).exclude(status=TimeOff.DECLINED).first()
    if same is not None:
        return {"request_id": str(same.pk), "status": same.status, "created": False}
    try:
        req = requests.ask_time_off(user, first, last, True, category=p.get("category", ""), reason=p.get("reason", ""))
    except requests.RequestError as e:
        raise invalid(str(e))
    return {"request_id": str(req.pk), "status": req.status, "created": True}


# ---------- customers and orders ----------

def customer_by_id(value):
    user = User.objects.filter(pk=int(value)).first() if str(value).isdigit() else None
    if user is None:
        raise invalid(f"There is no customer with the ID {value}. Use the Customer ID from Find customer.")
    return user


def customer_find(call, p):
    from django.db.models import Count, Max, Sum

    from pos_server.models import Order, Refund
    from users.models import Customer
    user = User.objects.filter(email__iexact=p["email"]).order_by("is_staff", "id").first()
    if user is None:
        return {"found": False}
    profile = Customer.objects.get_or_create(user=user)[0]
    orders = Order.objects.filter(customer=user, voided_at__isnull=True)
    stats = orders.aggregate(n=Count("id"), total=Sum("total"), last=Max("timestamp"))
    refunded = Refund.objects.filter(order__in=orders).aggregate(total=Sum("amount"))["total"] or 0
    out = {"found": True, "customer_id": str(user.pk), "name": full_name(user), "email": user.email, "phone": str(profile.phone or ""),
           "orders": stats["n"] or 0, "total_spent": money((stats["total"] or 0) - refunded), "newsletter": profile.newsletter,
           "email_verified": profile.email_verified, "notes": profile.staff_notes}
    if stats["last"]:
        out["last_order_at"] = iso(stats["last"])
    return out


def customer_add_note(call, p):
    """A line added to the staff notes the admin's Customers page edits (users.Customer.staff_notes, 200 characters)."""
    from users.models import Customer
    user = customer_by_id(p["customer_id"])
    profile = Customer.objects.select_for_update().get_or_create(user=user)[0]
    lines = [l for l in profile.staff_notes.split(" · ") if l]
    if p["note"] not in lines:
        notes = " · ".join(lines + [p["note"]])
        if len(notes) > 200:
            raise conflict(f"The staff notes about {full_name(user)} are full (200 characters). Shorten them on Pavilion’s Customers page.")
        profile.staff_notes = notes
        profile.save(update_fields=["staff_notes"])
    return {"customer_id": str(user.pk), "notes": profile.staff_notes}


def _order_by(value):
    from pos_server.models import Order
    digits = str(value).lstrip("#").strip()
    return Order.objects.filter(pk=int(digits)).first() if digits.isdigit() else None


def order_find(call, p):
    from pos_server import refunds
    from pos_server.models import OrderStation

    from .events import _order
    order = _order_by(p["number"])
    if order is None:
        return {"found": False}
    _subject, data, number_ = _order(order)
    statuses = order.statuses()
    trip = order.delivery.first() if order.channel == "delivery" else None
    if trip is not None and trip.delivered_at:
        status = "delivered"
    elif order.picked_up:
        status = "picked_up"
    elif order.all_stations_done():
        status = "ready"
    elif OrderStation.STATUS_PENDING in statuses:
        status = "waiting"
    else:
        status = "in_progress"
    o, c = data["order"], data["customer"]
    return {"found": True, "order_id": o["id"], "number": o["number"], "type": o["type"], "channel": o["channel"], "status": status,
            "payment": refunds.status(order), "total": o["total"], "refunded": money(refunds.refunded_total(order)), "items": o["items"],
            "summary": o["summary"], "placed_at": o["placed_at"], "customer_name": c["name"], "customer_email": c["email"]}


def order_send_receipt(call, p):
    """The order history's Send (pos_server/history.py): the receipt email, logged on the order."""
    from django.conf import settings
    from django.utils import translation

    from admin_portal import orders as orders_data
    from admin_portal.models import StoreSettings
    from misc_tools.email import EmailError, language_for, send
    from pos_server.models import ReceiptLog
    order = _order_by(p["order_id"])
    if order is None:
        raise invalid(f"There is no order with the ID {p['order_id']}.")
    to = p.get("email") or order.email or (order.customer.email if order.customer else "")
    if not to:
        raise invalid("This order has no email address. Give one in Email.")
    if not EMAIL.match(to):
        raise invalid("Email doesn’t look like an email address.")
    try:
        lang = language_for(to, user=order.customer, fallback=settings.LANGUAGE_CODE)
        with translation.override(lang):
            data = orders_data.receipt_data(order, StoreSettings.load().name)
        sent = send("receipt", to, data, language=lang)
    except EmailError as e:
        raise conflict(f"The receipt wasn’t sent: {e}")
    if not sent:
        raise conflict(NO_EMAIL)
    ReceiptLog.objects.create(order=order, channel=ReceiptLog.EMAIL, to=to[:140])
    return {"order_id": str(order.pk), "email": to, "sent": True}


def sales_summary(call, p):
    """One day's orders, added up the way the admin's reports do (admin_portal/reports.py: summaries)."""
    from django.db.models import Sum

    from admin_portal import reports
    from pos_server.models import Order, Refund
    day = day_of(p, "date", "Day") or timezone.localdate()
    start, end = reports.day_start(day), reports.day_start(day + datetime.timedelta(days=1))
    s = reports.summaries(day, day).get(day) or reports.DaySummary(day)
    orders = Order.objects.filter(timestamp__gte=start, timestamp__lt=end)
    sums = orders.aggregate(tax=Sum("tax"), tips=Sum("tip"), discounts=Sum("discount"))
    refunds = Refund.objects.filter(created_at__gte=start, created_at__lt=end).aggregate(total=Sum("amount"))["total"]
    return {"date": day.isoformat(), "orders": s.orders, "sales": money(s.sales), "average": round(money(s.avg), 2), "tax": money(sums["tax"]),
            "tips": money(sums["tips"]), "discounts": money(sums["discounts"]), "refunds": money(refunds),
            "items": int(sum(s.dishes.values())), "top_dish": s.top_dish if s.dishes else "",
            "online_orders": orders.exclude(channel="store").count()}


def _catering_by(value):
    from catering.models import CateringOrder
    digits = str(value).upper().replace("C-", "").strip()
    return CateringOrder.objects.filter(pk=int(digits) - 1000).first() if digits.isdigit() and int(digits) > 1000 else None


def catering_find(call, p):
    from .events import _catering
    order = _catering_by(p["number"])
    if order is None:
        return {"found": False}
    _subject, data = _catering(order)
    c = data["catering"]
    return {"found": True, "catering_id": c["id"], "number": c["number"], "title": c["title"], "status": c["status"], "event_at": c["event_at"],
            "fulfilment": c["fulfilment"], "total": c["total"], "customer_name": order.name, "customer_email": order.email}


def catering_send_message(call, p):
    """A message from the store in the order's conversation, emailed to the customer (catering/flow.py: write)."""
    from catering import flow
    from catering.models import CateringMessage
    order = _catering_by(p["number"])
    if order is None:
        raise invalid(f"There is no catering order with the number {p['number']}. Numbers look like C-1014.")
    try:
        flow.write(order, CateringMessage.STAFF, p["message"], request=call.request)
    except flow.CateringError as e:
        raise conflict(str(e))
    return {"catering_id": str(order.pk), "number": order.number, "sent": True}


# ---------- menu and stock ----------

def _closest(queryset, name):
    """(how many match, the closest): an exact name first, then one that starts with it, then the shortest."""
    found = list(queryset.filter(title__icontains=name))
    low = name.lower()
    found.sort(key=lambda x: (x.title.lower() != low, not x.title.lower().startswith(low), len(x.title), x.title.lower(), x.pk))
    return found


def _dish_reason(dish) -> str:
    if dish.available:
        return ""
    if dish.availability == "off":
        return "turned_off"
    if dish.blocked:
        return "part_turned_off"
    return "station_paused" if not dish.station.taking_tickets else "out_of_stock"


def dish_find(call, p):
    from pos_server.models import Dish
    found = _closest(Dish.objects.filter(archived=False).select_related("station"), p["name"])
    if not found:
        return {"found": False, "count": 0}
    dish = found[0]
    return {"found": True, "count": len(found), "dish_id": str(dish.pk), "name": dish.title, "price": money(dish.price),
            "station": dish.station.name, "available": dish.available, "mode": dish.availability, "reason": _dish_reason(dish),
            "matches": ", ".join(d.title for d in found[:20])}


def dish_set_availability(call, p):
    """The Availability screen's switch (inventory/availability.py: apply_changes)."""
    from inventory.availability import apply_changes
    from pos_server.models import Dish
    dish = Dish.objects.filter(pk=int(p["dish_id"]), archived=False).first() if p["dish_id"].isdigit() else None
    if dish is None:
        raise invalid(f"There is no dish with the ID {p['dish_id']}. Use the Dish ID from Find dish.")
    if p["mode"] not in ("off", "auto", "always"):
        raise invalid("Setting must be off, auto or always.")
    apply_changes([{"kind": "dish", "id": dish.pk, "mode": p["mode"]}])
    dish = Dish.objects.select_related("station").get(pk=dish.pk)
    return {"dish_id": str(dish.pk), "name": dish.title, "mode": dish.availability, "available": dish.available}


def station_set_taking_tickets(call, p):
    """A station's switch on the admin's Stations page."""
    from django.db.models import Q

    from pavilion.realtime import publish
    from pos_server.models import Station

    from . import events
    station = Station.objects.filter(Q(name__iexact=p["station"]) | Q(code__iexact=p["station"]), archived=False).first()
    if station is None:
        names = ", ".join(Station.objects.filter(archived=False).values_list("name", flat=True)) or "none yet"
        raise invalid(f"Pavilion has no station called “{p['station']}”. Its stations: {names}.")
    taking = p["taking_tickets"]
    changed = bool(Station.objects.filter(pk=station.pk).exclude(taking_tickets=taking).update(taking_tickets=taking))
    if changed:
        events.station_switched(station, taking)
        for topic in ("orders", "inventory"):  # registers, the store and kitchen screens show its dishes differently now
            publish(topic)
    return {"station": station.code, "name": station.name, "taking_tickets": taking, "changed": changed}


def _stock_models(kind=None):
    from pos_server.models import Component, Ingredient
    models = {"ingredient": Ingredient, "component": Component}
    return [(kind, models[kind])] if kind else list(models.items())


def _stock_kind(p):
    if p.get("kind") and p["kind"] not in ("ingredient", "component"):
        raise invalid("Kind must be ingredient or component.")
    return p.get("kind")


def _stock_answer(kind, obj) -> dict:
    out = {"kind": kind, "item_id": str(obj.pk), "name": obj.title, "on_hand": number(obj.inventory), "unit": obj.unit_of_measurement}
    if obj.par is not None:
        out.update(par=number(obj.par), below_par=(obj.inventory or 0) < obj.par)
    return out


def stock_find(call, p):
    found = []
    for kind, model in _stock_models(_stock_kind(p)):
        found += [(kind, obj) for obj in _closest(model.objects.filter(archived=False), p["name"])]
        if found and not p.get("kind"):
            break  # ingredients first; components only when no ingredient matches
    if not found:
        return {"found": False, "count": 0}
    kind, obj = found[0]
    return {"found": True, "count": len(found), **_stock_answer(kind, obj), "matches": ", ".join(o.title for _k, o in found[:20])}


def stock_adjust(call, p):
    """Cookery's own way of changing a count (inventory/cookery.py: _adjust): locked, saved, so dishes and screens follow."""
    from inventory.cookery import _adjust
    kind = _stock_kind(p)
    model = _stock_models(kind)[0][1]
    obj = model.objects.select_for_update().filter(pk=int(p["item_id"]), archived=False).first() if p["item_id"].isdigit() else None
    if obj is None:
        raise invalid(f"There is no {kind} with the ID {p['item_id']}. Use the Item ID from Check stock.")
    if getattr(obj, "unlimited", False):
        raise conflict(f"{obj.title} isn’t counted in Pavilion, so there is nothing to change.")
    change = float(p["change"])
    if change == 0:
        raise invalid("Change can’t be 0.")
    if (obj.inventory or 0) + change < 0:
        raise conflict(f"Only {number(obj.inventory)} {obj.unit_of_measurement} of {obj.title} is on hand, so {number(-change)} can’t be taken away.")
    return _stock_answer(kind, _adjust(model, obj.pk, change))


# ---------- gift cards and coupons ----------

NO_EMAIL = "Email isn’t set up in Pavilion yet, so nothing was sent. Connect it on the admin’s Email page."


def _issuer(call) -> str:
    return f"Switchboard ({call.connection.name})"[:80]


def _gift_by_id(value):
    from gift_cards.models import GiftCard
    return GiftCard.objects.select_related("design").filter(pk=int(value)).first() if str(value).isdigit() else None


def _gift_or_422(value):
    card = _gift_by_id(value)
    if card is None:
        raise invalid(f"There is no gift card with the ID {value}. Empty cards are deleted after 30 days.")
    return card


def _gift_answer(card) -> dict:
    return {"card_id": str(card.pk), "last4": card.last4, "balance": money(card.available_balance)}


def gift_card_issue(call, p):
    """The Gift cards page's Issue with "Email it" (gift_cards/giftcards.py: issue). The number exists only while the
    card is made and goes into the email to its recipient: it is never answered, logged or stored. The email is sent
    before the card is kept, so a card nobody could ever read the number of isn't left behind."""
    from gift_cards import giftcards as gc
    from misc_tools import listmonk
    from misc_tools.email import enabled
    if not EMAIL.match(p["email"]) or len(p["email"]) > 64:
        raise invalid("Their email doesn’t look like an email address.")
    amount = p["amount"]
    if amount != amount.to_integral_value() or amount <= 0 or amount > 1000:
        raise invalid("Amount must be whole dollars, from 1 to 1,000.")
    if not listmonk.is_configured() or not enabled("gift_card_issued"):
        raise conflict("Pavilion can’t email gift cards yet, and the email is the only way the card’s number reaches its owner. "
                       "Connect email and turn on “Gift card from the store” on the admin’s Email page.")
    card, number_ = gc.issue(design=gc.designs_on_sale().first(), amount=amount, recipient=p["name"], email=p["email"],
                             message=p.get("message", ""), note=p.get("note", ""), by=None, issuer=_issuer(call))
    if not gc.email_issued(call.request, card, number_):
        raise conflict("The gift card email couldn’t be sent, so no card was made. Try again in a minute.")  # rolls the card back
    return {**_gift_answer(card), "amount": money(amount), "emailed": True}


def gift_card_get(call, p):
    card = _gift_by_id(p["card_id"])
    if card is None:
        return {"found": False}
    return {"found": True, **_gift_answer(card), "original": money(card.original), "frozen": card.frozen, "name": card.recipient,
            "email": card.email, "issued_at": iso(card.issued_at)}


def gift_card_load(call, p):
    """The Gift cards page's Adjust, adding only (gift_cards/giftcards.py: adjust)."""
    from gift_cards import giftcards as gc
    card = _gift_or_422(p["card_id"])
    amount = p["amount"].quantize(Decimal("0.01"))
    if amount <= 0:
        raise invalid("Amount to add must be more than 0.")
    if gc.adjust_problem(card, amount):
        raise conflict("Cards can hold up to $1,000, and this would put more than that on it.")
    card = gc.adjust(card, amount, p["reason"], None, issuer="Switchboard")
    return {**_gift_answer(card), "added": money(amount)}


def gift_card_freeze(call, p):
    from gift_cards import giftcards as gc
    card = _gift_or_422(p["card_id"])
    changed = card.frozen != p["frozen"]
    if changed:
        if card.available_balance <= 0:
            raise conflict("That gift card is empty, so there is nothing to freeze.")
        gc.set_frozen(card, p["frozen"], None, issuer="Switchboard")
    return {**_gift_answer(card), "frozen": card.frozen, "changed": changed}


def gift_card_resend(call, p):
    from gift_cards import giftcards as gc
    card = _gift_or_422(p["card_id"])
    if not card.email:
        raise conflict("That gift card has no email address.")
    if card.available_balance <= 0 or card.frozen:
        raise conflict("That gift card is empty or frozen, so its email isn’t sent.")
    if not gc.resend(card, call.request):
        raise conflict(NO_EMAIL)
    return {**_gift_answer(card), "email": card.email, "sent": True}


def coupon_create(call, p):
    """A coupon as the admin's Coupons page saves one (admin_portal/coupons.py: errors, save), off the whole order."""
    from admin_portal import coupons
    from pos_server.models import Discount
    code = re.sub(r"\s+", "", p.get("code", "")).upper()
    if code and not re.fullmatch(r"[A-Z0-9_-]{3,40}", code):
        raise invalid("Code must be 3 to 40 letters, digits, dashes or underscores.")
    if code and Discount.objects.filter(code=code).exists():
        raise conflict(f"Another coupon already uses {code}.")
    while not code or Discount.objects.filter(code=code).exists():
        code = coupons.new_code()
    if p["kind"] not in ("pct", "amt"):
        raise invalid("Kind must be pct (percent off) or amt (amount off).")
    ends = day_of(p, "ends_on", "Last day it works")
    if ends and ends < timezone.localdate():
        raise invalid("Last day it works is in the past.")
    row = coupons.blank(code)
    row.update(title=p.get("name", "")[:80], kind=p["kind"], value=str(p["value"]), end=ends.isoformat() if ends else "",
               limit=str(p["max_uses"]) if p.get("max_uses") else "", per="once" if p.get("once_per_customer") else "any",
               first=bool(p.get("first_order_only")), min=str(p["min_subtotal"]) if p.get("min_subtotal") else "",
               chans=["pickup", "delivery"] if p.get("online_only") else ["pickup", "delivery", "store"])
    problems = coupons.errors(row, [row])
    if problems:
        raise invalid(" ".join(str(text) for text in problems.values()))
    coupons.save({"gone": [], "rows": [row]})
    coupon = Discount.objects.get(code=code)
    return {"coupon_id": str(coupon.pk), "code": coupon.code, "name": coupon.name}


def coupon_find(call, p):
    from pos_server.models import Discount
    coupon = Discount.objects.filter(code=re.sub(r"\s+", "", p["code"]).upper()).first()
    if coupon is None:
        return {"found": False}
    out = {"found": True, "coupon_id": str(coupon.pk), "code": coupon.code, "name": coupon.name, "kind": coupon.kind,
           "value": number(coupon.value), "active": coupon.active, "times_used": coupon.times_used,
           "ends_on": coupon.ends_on.isoformat() if coupon.ends_on else ""}
    if coupon.max_uses is not None:
        out["max_uses"] = coupon.max_uses
    return out


# ---------- the store ----------

def store_status(call, p):
    from events import hours as hours_text
    from events.hours import Hours
    now = timezone.now()
    hours = Hours()
    current, paused = hours.current(now), hours.paused_until(now)
    out = {"open": current is not None, "ordering_paused": paused is not None,
           "hours_today": hours_text.ranges_text(hours.ranges(timezone.localdate()))}
    if paused:
        out["paused_until"] = iso(paused)
    if current:
        out["closes_at"] = iso(current[1])
    else:
        opens = hours.next_start(now)
        if opens:
            out["opens_at"] = iso(opens)
    return out


def store_pause_ordering(call, p):
    """The Store hours page's Pause (admin_portal/hours.py: pause)."""
    from admin_portal import hours as hours_page
    minutes = p.get("minutes", 0)
    if minutes < 0 or minutes > 240:
        raise invalid("Minutes must be from 0 to 240.")
    return {"paused_until": iso(hours_page.pause(minutes))}


def store_resume_ordering(call, p):
    from admin_portal import hours as hours_page
    return {"resumed": hours_page.resume()}


def _minutes(p, key, label):
    if not p.get(key):
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", p[key])
    if not match or int(match[1]) > 24 or int(match[2]) > 59 or (int(match[1]) == 24 and int(match[2])):
        raise invalid(f"{label} must be a time like 09:00 or 21:30.")
    return int(match[1]) * 60 + int(match[2])


def store_event_create(call, p):
    """An event as the admin's Events page saves one (admin_portal/events.py: errors, save). Customers aren't emailed:
    that is the page's own Save."""
    from admin_portal import events as events_page
    from events.models import Event
    if p["kind"] not in (Event.OPEN, Event.CLOSED):
        raise invalid("Kind must be closed or open.")
    closed = p["kind"] == Event.CLOSED
    start = day_of(p, "starts_on", "First day")
    end = day_of(p, "ends_on", "Last day") or start
    opens, closes = _minutes(p, "opens_at", "From"), _minutes(p, "closes_at", "To")
    if (opens is None) != (closes is None):
        raise invalid("Give both From and To, or neither.")
    if opens is None and not closed:
        raise invalid("An event that opens the store needs From and To.")
    same = Event.objects.filter(title__iexact=p["title"], kind=p["kind"], start_date=start, end_date=end).first()
    if same is not None:
        return {"event_id": str(same.pk), "created": False}
    row = {"id": "", "kind": p["kind"], "name": p["title"][:120], "desc": p.get("description", "")[:500], "start": start.isoformat(),
           "end": end.isoformat(), "all_day": closed and opens is None, "from": 540 if opens is None else opens,
           "to": 1260 if closes is None else closes, "dine": p.get("dine_in", True), "pickup": p.get("pickup", True),
           "delivery": p.get("delivery", closed), "banner": p.get("banner", True), "lead": 7}
    problems = events_page.errors(row)
    if problems:
        raise invalid(" ".join(str(text) for text in problems))
    added = events_page.save({"gone": [], "events": [row]})
    return {"event_id": str(added[0].pk), "created": True}


def announcement_post(call, p):
    """The register's Screens sheet's Announce (screens/views.py: announce): the announcement pop-up, read out on the
    voice channels screens picked for it."""
    from screens.popups import record
    text = p["text"][:200]
    record("announcement", {"text": text, "by": call.connection.name})
    if p.get("speak", True):
        from audio.calls import speak
        speak(text, "announcement")
    return {"posted": True}


RUN = {
    "staff.find": staff_find, "staff.ensure": staff_ensure, "staff.invite": staff_invite, "team.on_now": team_on_now,
    "staff_card.issue": staff_card_issue,
    "shift.add": shift_add, "shift.cancel": shift_cancel, "shift.next": shift_next, "time_off.request": time_off_request,
    "customer.find": customer_find, "customer.add_note": customer_add_note, "order.find": order_find,
    "order.send_receipt": order_send_receipt, "sales.summary": sales_summary, "catering.find": catering_find,
    "catering.send_message": catering_send_message,
    "dish.find": dish_find, "dish.set_availability": dish_set_availability, "station.set_taking_tickets": station_set_taking_tickets,
    "stock.find": stock_find, "stock.adjust": stock_adjust,
    "gift_card.issue": gift_card_issue, "gift_card.get": gift_card_get, "gift_card.load": gift_card_load,
    "gift_card.freeze": gift_card_freeze, "gift_card.resend": gift_card_resend, "coupon.create": coupon_create, "coupon.find": coupon_find,
    "store.status": store_status, "store.pause_ordering": store_pause_ordering, "store.resume_ordering": store_resume_ordering,
    "store_event.create": store_event_create, "announcement.post": announcement_post,
}
