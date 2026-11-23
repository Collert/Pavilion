"""Lists a param offers in Switchboard's builder (Switchboard's docs/PROTOCOL.md: "Options from the building").

A manifest param names one with `"options": "<name>"`; Switchboard reads it from `GET /switchboard/v1/options/<name>`
when someone opens the step. Each option's `value` is exactly what the param accepts (the name the action looks
up, or the id). Only what the matching lookups already answer, and a fixed number of queries per list.
"""
from django.contrib.auth.models import User

LIMIT = 500
TIME_OFF_KINDS = ["Vacation", "Appointment", "Personal", "Leave", "Sick"]


def _name(user):
    return " ".join(part for part in (user.first_name, user.last_name) if part) or user.username


def roles():
    """Roles on the Roles page, as the Roles page orders them (`actions._role_named` takes the name)."""
    from users.models import Role
    return [{"value": name, "label": name} for name in Role.objects.filter(archived=False).values_list("name", flat=True)]


def stations():
    """Stations, in the Stations page's order (`station.set_taking_tickets` takes the name)."""
    from pos_server.models import Station
    return [{"value": name, "label": name} for name in Station.objects.filter(archived=False).values_list("name", flat=True)]


def staff():
    """Staff who can sign in, by name: the id `staff_id` params take, labelled with the name and email."""
    people = sorted(User.objects.filter(is_staff=True, is_active=True), key=lambda u: (_name(u).lower(), u.pk))
    return [{"value": str(u.pk), "label": f"{_name(u)} · {u.email}" if u.email else _name(u)} for u in people]


def dishes():
    """Dishes not archived, by station and name: the id `dish_id` params take, labelled with the dish and station."""
    from pos_server.models import Dish
    rows = Dish.objects.filter(archived=False).select_related("station").order_by("station__position", "station__id", "title")
    return [{"value": str(d.pk), "label": f"{d.title} · {d.station.name}"} for d in rows]


def staff_card_types():
    """Balance card types (what `staff_card.issue`'s label picks; a discount type can't hold a balance)."""
    from gift_cards.models import StaffCardType
    names = StaffCardType.objects.filter(archived=False, kind=StaffCardType.BALANCE).values_list("name", flat=True)
    return [{"value": name, "label": name} for name in names]


def time_off_kinds():
    """The usual kinds of time off, then any others people have used, by name."""
    from team.models import TimeOff
    used = {c.strip() for c in TimeOff.objects.exclude(category="").values_list("category", flat=True).distinct()}
    known = {k.lower() for k in TIME_OFF_KINDS}
    return [{"value": k, "label": k} for k in TIME_OFF_KINDS + sorted(c for c in used if c and c.lower() not in known)]


SOURCES = {
    "roles": roles,
    "stations": stations,
    "staff": staff,
    "dishes": dishes,
    "staff_card_types": staff_card_types,
    "time_off.kinds": time_off_kinds,
}


def get(name):
    """The list called `name` (at most LIMIT), or None when there is no such list."""
    source = SOURCES.get(name)
    return None if source is None else source()[:LIMIT]
