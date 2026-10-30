"""
Every kind of push notification the admin's Notifications page can turn on and off (push.NotificationSwitch; no row =
on), by group. Courier notifications aren't listed: couriers can't work without them.
Adding one: a Kind here, then check `enabled(key)` before sending it.
"""
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

BAG = "M5 8h14l-1.2 11.2a1 1 0 0 1-1 .8H7.2a1 1 0 0 1-1-.8z M9 8V6a3 3 0 0 1 6 0v2"
CALENDAR = "M4 5h16v15H4z M4 10h16 M9 3v4 M15 3v4"
STORE = "M4 9h16l-1-4H5z M5 9v11h14V9 M9 20v-6h6v6"
MEGAPHONE = "M3 10v4h3l7 4V6l-7 4z M16 9a4 4 0 0 1 0 6 M6 14l1 5h3l-1-5"


@dataclass(frozen=True)
class Kind:
    key: str
    group: str
    icon: str
    label: str
    when: str
    to: str
    example_title: str
    example_body: str


GROUPS = [("store", _("Online store"), STORE)]

KINDS = [
    Kind("order_updates", "store", BAG, _("Order updates"),
         _("Each step of an online order: accepted, ready for pickup, on its way with a courier, delivered or picked up, and if it’s rejected or refunded."),
         _("Whoever placed the order and turned on notifications: guests on that device, customers on every device they turned them on."),
         _("Order #128 is ready"), _("It’s waiting for you on the pickup shelf.")),
    Kind("event_updates", "store", CALENDAR, _("Event updates"),
         _("You save a new event on the Events page: extra hours, or a closure."),
         _("Customers who turned on event updates in their account."),
         _("Open late for trivia night"), _("Friday, November 6 · 7 PM – 10 PM")),
    Kind("campaigns", "store", MEGAPHONE, _("News from listmonk"),
         _("You send a listmonk campaign with the Pavilion push messenger: its subject is the title, its text the message, its first link where a tap goes."),
         _("Customers with an account, on the devices where they turned on News and offers."),
         _("Two-for-one tacos tonight"), _("Show this at the counter, 5 to 7 PM.")),
]

BY_KEY = {k.key: k for k in KINDS}


def enabled(key) -> bool:
    from .models import NotificationSwitch
    row = NotificationSwitch.objects.filter(kind=key).first()
    return row.enabled if row else True
