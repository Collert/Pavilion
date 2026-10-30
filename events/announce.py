"""
Telling customers about a new event by email (the Email page's Events group, misc_tools/emails.py): saving a new event
on the admin's Events page that hasn't ended sends a listmonk campaign, event_open for extra hours and event_closed
for a closure, to the customers list and the lists picked for that email (misc_tools.email.send_campaign). Pavilion
writes the campaign's content and subject in every language that's on (listmonk picks each subscriber's), from
misc_tools/templates/emails/listmonk/event_content.html. It's sent after the save commits, in the background.
"""
import datetime
import logging

from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext

from misc_tools import email, listmonk
from misc_tools.email_templates import LOOKS, campaign_languages
from misc_tools.emails import BY_KEY
from misc_tools.templatetags.listmonk import by_language

from .hours import text as clock
from .models import Event, StoreHours

logger = logging.getLogger(__name__)


def kind_of(event) -> str:
    return "event_open" if event.kind == Event.OPEN else "event_closed"


def plain(text) -> str:
    """Customer-facing text that listmonk mustn't read as its own code."""
    return (text or "").replace("{{", "{ {").replace("}}", "} }").strip()


def details(event) -> dict:
    from admin_portal.store_info import full_address
    all_day = event.kind == Event.CLOSED and event.all_day
    everything = event.closes_everything()
    return {
        "open": event.kind == Event.OPEN, "title": plain(event.title), "desc": plain(event.description),
        "start": event.start_date, "end": event.end_date, "multi": event.end_date != event.start_date,
        "all_day": all_day, "times": "" if all_day else f"{clock(event.opens)} – {clock(event.closes)}",
        "channels": [key for key, on in (("dine", event.dine_in), ("pickup", event.pickup), ("delivery", event.delivery)) if on],
        "everything": everything, "back": event.end_date + datetime.timedelta(days=1) if all_day and everything else None,
        "ahead": event.kind == Event.CLOSED and StoreHours.load().ahead,
        "place": plain(event.location_title), "address": plain(event.location_address) or full_address(),
    }


def campaign(event, base) -> dict:
    """{"kind", "name", "subject", "body"}: the campaign for `event`; `base` is the site's address (https://host)."""
    from admin_portal.models import StoreSettings
    kind, store, langs = kind_of(event), StoreSettings.load().name, campaign_languages()
    e = details(event)
    body = render_to_string("emails/listmonk/event_content.html", {"e": e, "look": LOOKS[kind], "langs": langs, "base": base})

    def subject(lang):
        with translation.override(lang):
            return str(BY_KEY[kind].subject) % {"title": e["title"], "store": store}
    with translation.override(langs[-1]):
        name = gettext("Event: %(title)s (%(day)s)") % {"title": e["title"], "day": event.start_date.isoformat()}
    return {"kind": kind, "title": e["title"], "name": name, "subject": by_language(subject, langs), "body": body}


def announce_later(events, request):
    """After this transaction, in the background: a campaign for each new event that hasn't ended."""
    if not listmonk.is_configured():
        return []
    today = timezone.localdate()
    base = request.build_absolute_uri("/").rstrip("/")
    jobs = [campaign(e, base) for e in events if e.end_date >= today and email.enabled(kind_of(e))]

    def job():
        for c in jobs:
            try:
                email.send_campaign(c["kind"], c["name"], c["subject"], c["body"])
            except listmonk.ListmonkError:
                logger.warning("Couldn't send the email about %s", c["name"], exc_info=True)

    if jobs:
        from django.db import transaction
        transaction.on_commit(lambda: listmonk._background(job))
    return jobs


def notify_later(events) -> int:
    """Push "Event updates" (push/kinds.py) about each new event that hasn't ended to the store subscriptions that
    turned them on, after this transaction and in the background. Returns how many events it's about."""
    from django.urls import reverse
    from django.utils.formats import date_format
    from push import kinds, send
    from push.models import PushSubscription
    if not kinds.enabled("event_updates"):
        return 0
    today = timezone.localdate()
    fresh = [e for e in events if e.end_date >= today]
    subs = list(PushSubscription.objects.filter(app=PushSubscription.STORE, event_updates=True))
    if not fresh or not subs:
        return 0
    from online_store.app import _icon_url
    icon = _icon_url()
    for event in fresh:
        def build(event=event):
            day = date_format(event.start_date, "l, F j")
            if event.end_date != event.start_date:
                day = f"{day} – {date_format(event.end_date, 'l, F j')}"
            if event.kind == Event.OPEN:
                body = f"{day} · {clock(event.opens)} – {clock(event.closes)}"
            elif event.all_day:
                body = gettext("Closed %(day)s") % {"day": day}
            else:
                body = gettext("Closed %(day)s, %(from)s – %(to)s") % {"day": day, "from": clock(event.opens), "to": clock(event.closes)}
            return {"title": event.title, "body": body, "url": reverse("store_home"), "tag": f"event-{event.pk}", "icon": icon}
        send.notify(subs, build)
    return len(fresh)
