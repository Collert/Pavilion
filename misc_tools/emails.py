"""
Every email Pavilion sends (design: AdminEmail): its key (what `misc_tools.email.send` is called with), group on the
admin's Email page, what it says and when, who gets it, whether it can be turned off, the data its template gets
(`{{ .Tx.Data.<field> }}` in listmonk) and its subject. The template itself is
misc_tools/templates/emails/listmonk/<key>.html, put in listmonk once per language (misc_tools/listmonk.py: install).
Subjects use %(field)s for data and %(store)s for the store's name.

Marketing emails (`campaign`) aren't sent to one person: each is a listmonk campaign to the customers list (people who
ticked "Monthly menu email") and the other listmonk lists picked for it on the Email page (EmailSwitch.lists), never
the users list. One campaign template holds every language that's on (listmonk can't pick one per subscriber), and
Pavilion writes the campaign's content (events/announce.py).
"""
from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Kind:
    key: str
    group: str
    icon: str
    label: str
    when: str
    to: str
    subject: str
    fields: tuple = ()          # (name, what it is)
    always: bool = False        # can't be turned off (people need it to get into their account)
    default_on: bool = True
    hidden: bool = False        # not listed (the test email)
    campaign: bool = False      # marketing: a listmonk campaign to lists, not a transactional email


ICONS = {
    "user": "M12 8a3.5 3.5 0 1 0 0-.01 M5 20c0-4 3-6.5 7-6.5s7 2.5 7 6.5",
    "key": "M15 7a4 4 0 1 1-3.9 5H8v3H5v-3H3v-3h8.1A4 4 0 0 1 15 7z",
    "mail": "M3 6h18v12H3z M3 7l9 6 9-6",
    "invite": "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M2.5 20a6.5 6.5 0 0 1 13 0 M19 8v6 M16 11h6",
    "receipt": "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6",
    "bag": "M5 8h14l-1.2 11.2a1 1 0 0 1-1 .8H7.2a1 1 0 0 1-1-.8z M9 8V6a3 3 0 0 1 6 0v2",
    "refund": "M3.5 12a8.5 8.5 0 1 0 2.5-6 M3 4v4h4 M12 8v8 M9.5 10.5h4a1.5 1.5 0 0 1 0 3h-3a1.5 1.5 0 0 0 0 3h4",
    "cater": "M3 18h18 M5 18a7 7 0 0 1 14 0 M12 8V6 M10 6h4",
    "edit": "M4 20h4L19 9l-4-4L4 16z M13 7l4 4",
    "check": "m5 12.5 4.5 4.5L19 7.5",
    "x": "M6 6l12 12M18 6 6 18",
    "chat": "M4 5h16v11H8l-4 4z",
    "gift": "M3 9h18v4H3z M5 13v8h14v-8 M12 9v12 M12 9s-4-6-6-3 6 3 6 3 4-6 6-3-6 3-6 3",
    "cal": "M4 5h16v15H4z M4 10h16 M9 3v4 M15 3v4",
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3 2",
    "swap": "M7 4 3 8l4 4 M3 8h14 M17 20l4-4-4-4 M21 16H7",
    "spark": "M12 3v4 M12 17v4 M3 12h4 M17 12h4 M6 6l2.5 2.5 M15.5 15.5 18 18 M18 6l-2.5 2.5 M8.5 15.5 6 18",
    "star": "m12 3 2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6-4.5-4.2 6.1-.7z",
    "closed": "M4 5h16v15H4z M4 10h16 M9 3v4 M15 3v4 M9.5 13l5 5 M14.5 13l-5 5",
}

GROUPS = [("accounts", _("Accounts"), ICONS["user"]), ("orders", _("Orders"), ICONS["receipt"]), ("catering", _("Catering"), ICONS["cater"]),
          ("gifts", _("Gift cards"), ICONS["gift"]), ("team", _("Team"), ICONS["cal"]),
          ("events", _("Events"), ICONS["star"])]

NAME, STORE, LINK = ("name", _("Their first name")), ("store", _("The store’s name")), ("link", _("The link to press"))
CATERING = (NAME, ("number", _("Request number")), ("title", _("What the event is")), ("when", _("Event date and time")),
            ("total", _("The order’s total")), ("order_url", _("Their request page")), STORE)

KINDS = [
    # Accounts
    Kind("verify_email", "accounts", ICONS["mail"], _("Confirm your email"), _("Someone makes an account in the online store."),
         _("The new customer"), _("Confirm your email for %(store)s"), (NAME, LINK, STORE), always=True),
    Kind("email_change_confirm", "accounts", ICONS["mail"], _("Confirm a new email"), _("Staff change a customer’s email address."),
         _("The new address"), _("Confirm your new email for %(store)s"), (NAME, LINK, STORE), always=True),
    Kind("email_change_notice", "accounts", ICONS["mail"], _("Email changed"), _("A customer’s email address is changed."),
         _("The old address"), _("Your email for %(store)s was changed"), (NAME, ("new", _("The new address")), STORE), always=True),
    Kind("password_reset", "accounts", ICONS["key"], _("Password reset"), _("Someone asks to set a new password, or staff send them a link."),
         _("The account holder"), _("Set a new password for %(store)s"), (NAME, LINK, STORE), always=True),
    Kind("staff_invite", "accounts", ICONS["invite"], _("Staff invite"), _("A manager adds a new staff member."),
         _("The new staff member"), _("Welcome to the team at %(store)s"), (NAME, LINK, STORE), always=True),
    # Orders
    Kind("receipt", "orders", ICONS["receipt"], _("Order receipt"), _("Staff email a receipt from an order’s page."),
         _("The customer"), _("Your receipt from %(store)s · order #%(number)s"),
         (("number", _("Order number")), ("when", _("When it was placed")), ("items", _("A list: qty, name, total")),
          ("totals", _("A list: label, value")), STORE)),
    Kind("order_ready", "orders", ICONS["bag"], _("Ready for pickup"), _("The kitchen finishes an online pickup order."),
         _("The customer"), _("Order #%(number)s is ready"), (NAME, ("number", _("Order number")), ("address", _("Where to pick it up")), STORE)),
    Kind("refund_issued", "orders", ICONS["refund"], _("Refund issued"), _("Part or all of an order is refunded."),
         _("The customer"), _("Your refund from %(store)s"),
         (NAME, ("number", _("Order number")), ("amount", _("How much")), ("to", _("Where the money goes")), ("reason", _("Why")), STORE)),
    # Catering
    Kind("catering_received", "catering", ICONS["cater"], _("Request received"), _("A customer sends a catering request."),
         _("The customer"), _("We got your catering request · %(number)s"), CATERING + (("message", _("Your auto-reply")),)),
    Kind("catering_new", "catering", ICONS["cater"], _("New catering request"), _("A customer sends a catering request."),
         _("Staff who answer catering"), _("New catering request · %(number)s"),
         (("number", _("Request number")), ("customer", _("Who sent it")), ("title", _("What the event is")),
          ("when", _("Event date and time")), ("total", _("The quote")), ("admin_url", _("Its page in the admin")), STORE)),
    Kind("catering_changes", "catering", ICONS["edit"], _("Changes proposed"), _("You send a new version of the order."),
         _("The customer"), _("Changes to your catering order · %(number)s"),
         CATERING + (("changes", _("A list of what changed")), ("was", _("The old total")), ("now", _("The new total")), ("message", _("Your note")))),
    Kind("catering_accepted", "catering", ICONS["check"], _("Confirmed and charged"), _("The customer accepts, or you accept their request."),
         _("The customer"), _("Your catering order is confirmed · %(number)s"), CATERING + (("message", _("Your note")),)),
    Kind("catering_closed", "catering", ICONS["x"], _("Request declined"), _("You reject or cancel a catering request."),
         _("The customer"), _("About your catering order · %(number)s"),
         CATERING + (("reason", _("Why")), ("message", _("Your note")), ("refunded", _("What was refunded, if anything")))),
    Kind("catering_message", "catering", ICONS["chat"], _("New message"), _("You write in a catering conversation."),
         _("The customer"), _("A message about your catering order · %(number)s"), CATERING + (("message", _("The message")),)),
    Kind("catering_reminder", "catering", ICONS["clock"], _("Waiting on them"), _("Proposed changes are still unanswered after a while."),
         _("The customer"), _("Your catering order is waiting on you · %(number)s"), CATERING),
    # Gift cards
    Kind("gift_card", "gifts", ICONS["gift"], _("Gift card for yourself"), _("Someone buys a gift card for themselves."),
         _("The buyer"), _("Your %(amount)s gift card from %(store)s"),
         (NAME, ("amount", _("Its value")), ("card_url", _("The card, with its number")), ("last4", _("Its last 4 digits")), STORE)),
    Kind("gift_card_gifted", "gifts", ICONS["gift"], _("Gift card delivered"), _("Someone buys a gift card for someone else, online or at the register."),
         _("The recipient"), _("%(sender)s sent you a gift card"),
         (NAME, ("sender", _("Who it’s from")), ("amount", _("Its value")), ("message", _("Their message")),
          ("card_url", _("The card, with its number")), ("last4", _("Its last 4 digits")), STORE)),
    Kind("gift_card_issued", "gifts", ICONS["gift"], _("Gift card from the store"), _("Staff issue a gift card on the admin’s Gift cards page."),
         _("The recipient"), _("%(store)s sent you a %(amount)s gift card"),
         (NAME, ("amount", _("Its value")), ("message", _("Your message")), ("card_url", _("The card, with its number")),
          ("last4", _("Its last 4 digits")), STORE)),
    Kind("gift_card_receipt", "gifts", ICONS["receipt"], _("Gift card receipt"), _("Someone buys a gift card online for someone else."),
         _("The buyer"), _("Your gift card for %(recipient)s"),
         (NAME, ("recipient", _("Who it’s for")), ("amount", _("Its value")), ("last4", _("Its last 4 digits")), STORE)),
    Kind("gift_card_balance", "gifts", ICONS["gift"], _("Gift card balance"), _("Staff resend a gift card’s email."),
         _("The card holder"), _("Your %(store)s gift card •• %(last4)s"),
         (NAME, ("last4", _("Its last 4 digits")), ("balance", _("What’s left")), ("balance_url", _("Its balance page")), STORE)),
    # Team
    Kind("schedule_changed", "team", ICONS["cal"], _("Schedule published"), _("A manager publishes shifts."),
         _("Staff with shifts"), _("Your shifts at %(store)s"), (NAME, ("shifts", _("A list of their next two weeks")), STORE)),
    Kind("request_decided", "team", ICONS["clock"], _("Time off decision"), _("A manager approves or declines a request."),
         _("The staff member"), _("Your request was %(decision)s"),
         (NAME, ("what", _("What they asked for")), ("decision", _("approved or declined")), ("message", _("The manager’s note")), STORE)),
    Kind("shift_swap", "team", ICONS["swap"], _("Shift swap request"), _("A coworker asks someone to take or swap a shift."),
         _("The coworker asked"), _("%(from)s asked you to take a shift"),
         (NAME, ("from", _("Who’s asking")), ("shift", _("Their shift")), ("theirs", _("Your shift they’d take, for a swap")),
          ("note", _("Their note")), STORE)),
    Kind("shift_taken", "team", ICONS["bag"], _("Shift picked up"), _("Someone grabs a shift that was given away."),
         _("Who gave it away"), _("%(who)s took your shift"), (NAME, ("who", _("Who took it")), ("shift", _("The shift")), STORE)),
    Kind("shift_swap_decided", "team", ICONS["swap"], _("Swap decision"), _("A coworker says no, or a manager decides a swap or hand-off."),
         _("Both people"), _("Your shift swap was %(decision)s"),
         (NAME, ("shift", _("The shift")), ("decision", _("approved or declined")), ("message", _("The manager’s note")), STORE)),
    # Events (marketing), sent when a new event is saved on the Events page
    Kind("event_open", "events", ICONS["spark"], _("Extra hours"), _("You add an event that opens the store (extra hours) on the Events page."),
         _("Customers who get the menu email, and the lists you pick"), _("%(title)s at %(store)s"), campaign=True),
    Kind("event_closed", "events", ICONS["closed"], _("We’re closed"), _("You add an event that closes the store on the Events page."),
         _("Customers who get the menu email, and the lists you pick"), _("Heads up: %(title)s"), campaign=True),
    # The admin's "Send a test email"
    Kind("test", "", ICONS["mail"], _("Test email"), "", "", _("Test email from %(store)s"), (NAME, STORE), always=True, hidden=True),
]

BY_KEY = {k.key: k for k in KINDS}

# What a campaign template can use (listmonk's own; Pavilion's part is the content)
CAMPAIGN_VARS = [('{{ template "content" . }}', _("The event: its name, dates, times and a button, written by Pavilion")),
                 ("{{ .Subscriber.FirstName }}", _("Their first name")), ("{{ .Subscriber.Attribs.language }}", _("Their language, for people with an account")),
                 ("{{ UnsubscribeURL }}", _("Leave the list")), ("{{ MessageURL }}", _("The email in a browser"))]
