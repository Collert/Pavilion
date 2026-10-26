"""
Sending Pavilion's email through listmonk's transactional API (https://listmonk.app/docs/apis/transactional/).

Each email (misc_tools/emails.py) has a listmonk template per language the store has on, put there from the admin's
Email page (misc_tools/listmonk.py: install) and kept in misc_tools.models.EmailTemplate. `send` picks the template
in the recipient's language: the one given, else their account's (users.Preference), else the language of the
page that sent it, falling back to the store's default and then any. Data is available in templates as
{{ .Tx.Data.<key> }}; "store" (the store's name) is always there.

Without listmonk connected nothing is sent and the email is logged, so local development works offline. An email
turned off on the Email page isn't sent either.
"""
import logging

from django.conf import settings
from django.utils import translation

from . import listmonk
from .emails import BY_KEY
from .listmonk import ListmonkError

logger = logging.getLogger(__name__)
EmailError = ListmonkError  # what callers catch


def enabled(kind) -> bool:
    from .models import EmailSwitch
    entry = BY_KEY[kind]
    if entry.always:
        return True
    switch = EmailSwitch.objects.filter(kind=kind).first()
    return switch.enabled if switch else entry.default_on


def language_for(to, language=None, user=None, fallback=None) -> str:
    """The language to write to `to` in (see the module docstring). `fallback` instead of the page's language, for
    emails staff send to someone Pavilion doesn't know (their language isn't the staff member's)."""
    from django.contrib.auth.models import User

    from users.models import enabled_language, language_of
    if enabled_language(language):
        return enabled_language(language)
    if user is None and to:
        user = User.objects.filter(email__iexact=to).select_related("preference").first()
    if user is not None and getattr(getattr(user, "preference", None), "language", ""):
        return language_of(user)
    return enabled_language(fallback or translation.get_language()) or settings.LANGUAGE_CODE


def template_for(kind, lang):
    from .models import EmailTemplate
    rows = dict(EmailTemplate.objects.filter(kind=kind).values_list("language", "template_id"))
    return rows.get(lang) or rows.get(settings.LANGUAGE_CODE) or next(iter(rows.values()), None)


def send(kind, to, data, language=None, user=None, fallback=None):
    """
    Send one email of `kind` to the address `to`. Returns True when listmonk took it, False when it wasn't sent
    (listmonk isn't connected, or the email is turned off). Raises EmailError when listmonk refuses or can't be
    reached. `language` or `user` say who it's for (see language_for).
    """
    if kind not in BY_KEY:
        raise ValueError(f"Unknown email kind: {kind}")
    from admin_portal.models import StoreSettings
    data = {"store": StoreSettings.load().name, **data}
    if not listmonk.is_configured():
        logger.info("Email not sent (listmonk isn't connected): %s to %s with %s", kind, to, data)
        return False
    if not enabled(kind):
        logger.info("Email not sent (turned off on the Email page): %s to %s", kind, to)
        return False
    lang = language_for(to, language, user, fallback)
    template = template_for(kind, lang)
    if template is None:
        listmonk.install(keys=[kind])  # its templates aren't in listmonk yet
        template = template_for(kind, lang)
    if template is None:
        raise EmailError(f"listmonk has no template for {kind}. Install Pavilion’s templates on the Email page.")
    payload = {"subscriber_emails": [to], "subscriber_mode": "fallback",  # also to addresses that aren't subscribers
               "template_id": template, "data": data, "content_type": "html"}
    sender = listmonk.config()["from"]
    if sender:
        payload["from_email"] = sender
    try:
        listmonk.api("POST", "/tx", payload)
    except ListmonkError as e:
        if e.status != 400:
            raise
        listmonk.add_address(to)  # older listmonk only sends to subscribers: make one, then try again
        listmonk.api("POST", "/tx", payload)
    return True
