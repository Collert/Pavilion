"""
What this Pavilion tells Switchboard it can emit and do (protocol §1 and §5): GET /.well-known/switchboard.

Switchboard builds its nodes from this, so `type`, `name` and every `key` are a contract: workflows store them. Add
to these lists freely; never rename or remove. Titles and descriptions are for people and always in English (the
protocol's language), so they aren't wrapped for translation. The actions themselves are in actions.py (one function
per name) and the events in events.py.
"""
from django.templatetags.static import static
from django.urls import reverse

from .connections import BUILDING, instance_name


def field(key, type_, label, example, **more):
    return {"key": key, "type": type_, "label": label, "example": example, **more}


def param(key, type_, label, required=False, help="", **more):
    out = {"key": key, "type": type_, "label": label, "required": required}
    if help:
        out["help"] = help
    return {**out, **more}


ORDER_FIELDS = [
    field("order.id", "string", "Order ID", "1842"),
    field("order.number", "string", "Order number", "1842"),
    field("order.type", "string", "Order type (dine_in, to_go, pickup or delivery)", "pickup"),
    field("order.channel", "string", "Where it was placed (store, web or delivery)", "web"),
    field("order.total", "money", "Total", 24.5),
    field("order.items", "int", "Number of items", 3),
    field("customer.name", "string", "Customer name", "Maria K."),
    field("customer.email", "string", "Customer email", "maria@example.org"),
]
STAFF_FIELDS = [
    field("staff.id", "string", "Staff ID", "12"),
    field("staff.name", "string", "Name", "Dana Lee"),
    field("staff.email", "string", "Email", "dana@example.org"),
    field("at", "datetime", "When", "2026-11-19T16:20:04-08:00"),
    field("role", "string", "Role on the shift", "Barista"),
]
TRIGGERS = [
    {"type": "order.placed", "title": "Order placed",
     "description": "Starts every time an order is placed: on a register, in the online store or for delivery.",
     "summary": "Order {order.number} placed ({order.type})", "fields": ORDER_FIELDS},
    {"type": "order.ready", "title": "Order ready",
     "description": "Starts when the last station finishes an order and it is ready for the customer or the courier.",
     "summary": "Order {order.number} is ready", "fields": ORDER_FIELDS},
    {"type": "staff.clocked_in", "title": "Staff member clocked in",
     "description": "Starts when someone clocks in at the time clock.",
     "summary": "{staff.name} clocked in", "fields": STAFF_FIELDS},
    {"type": "staff.clocked_out", "title": "Staff member clocked out",
     "description": "Starts when someone clocks out at the time clock.",
     "summary": "{staff.name} clocked out after {hours} hours",
     "fields": STAFF_FIELDS + [field("hours", "number", "Hours worked (without unpaid breaks)", 5.25)]},
]

STAFF_OUTPUT = [
    field("staff_id", "string", "Staff ID", "12"),
    field("name", "string", "Name", "Dana Lee"),
    field("email", "string", "Email", "dana@example.org"),
]
ACTIONS = [
    {"name": "staff.find", "kind": "lookup", "title": "Find staff member",
     "description": "Looks for a staff member by email.",
     "params": [param("email", "string", "Email", True, "The address on their staff account.")],
     "output": [field("found", "bool", "Found", True)] + STAFF_OUTPUT + [
         field("roles", "string", "Roles (separated by commas)", "Barista, Cashier"),
         field("active", "bool", "Can sign in", True)]},
    {"name": "staff.ensure", "kind": "action", "title": "Make sure they are staff",
     "description": "Finds the staff account with this email, or makes one when there isn't one. A new account has no "
                    "password and belongs to no group until a manager sets it up on the Staff page.",
     "params": [param("name", "string", "Name", True, "First and last name, used when the account is made."),
                param("email", "string", "Email", True),
                param("role", "string", "Role", False, "The name of a role on Pavilion's Roles page. They get the role and its starting groups."),
                param("invite", "bool", "Email an invitation", False,
                      "Sends a new staff member the link where they set their password.", default=False)],
     "output": STAFF_OUTPUT + [field("created", "bool", "A new staff account was made", True)]},
    {"name": "staff_card.issue", "kind": "action", "title": "Issue a staff card",
     "description": "Gives a staff member a balance card worth an amount, which they spend like a gift card. The label is "
                    "the card type on Pavilion's Staff cards page: an existing balance type with that name is used with its "
                    "rules and refills, otherwise a one-time balance type with that name is made.",
     "params": [param("staff_id", "string", "Staff ID", True, "From Find staff member or Make sure they are staff."),
                param("amount", "money", "Amount", True, "What the card is worth, in dollars.", min=0.01, max=9999.99),
                param("label", "string", "Label", False, "The card type's name.", default="Switchboard", max_length=30),
                param("replace_previous", "bool", "Replace the previous card", False,
                      "Retires the cards this Switchboard gave them before, so they hold one card instead of several.", default=False)],
     "output": [field("card_id", "string", "Card ID", "57"), field("amount", "money", "Amount", 15.0),
                field("label", "string", "Label", "Volunteer meal"),
                field("replaced", "string", "Replaced card ID (empty when none)", "41")]},
    {"name": "shift.add", "kind": "action", "title": "Add a shift",
     "description": "Puts a published shift on Pavilion's schedule for a staff member, so they can clock in for it. "
                    "A shift they already have at exactly those times is returned instead of added twice.",
     "params": [param("staff_id", "string", "Staff ID", True),
                param("starts_at", "datetime", "Starts", True, "The shift is saved in the store's time zone."),
                param("ends_at", "datetime", "Ends", True, "The same day as the start, or midnight."),
                param("role", "string", "Role", False, "The name of a role on Pavilion's Roles page. Left out: their first role."),
                param("note", "string", "Note", False, "Shows on their schedule.", max_length=80)],
     "output": [field("shift_id", "string", "Shift ID", "903"),
                field("status", "string", "Status (published or unpublished)", "published"),
                field("created", "bool", "A new shift was added", True)]},
]
BY_NAME = {a["name"]: a for a in ACTIONS}


def build(request) -> dict:
    api_base = request.build_absolute_uri(reverse("switchboard_api")).rstrip("/")
    return {
        "protocol": "1", "building": BUILDING, "name": "Pavilion", "instance": instance_name(),
        # Pavilion's mark, so Switchboard draws this building's steps with it.
        "icon": request.build_absolute_uri(static("pavilion/pavilion.svg")),
        "authorize_url": request.build_absolute_uri(reverse("switchboard_authorize")),
        "token_url": request.build_absolute_uri(reverse("switchboard_token")),
        "api_base": api_base,
        "triggers": TRIGGERS,
        "actions": [{**a, "method": "POST", "path": f"/actions/{a['name']}"} for a in ACTIONS],
    }
