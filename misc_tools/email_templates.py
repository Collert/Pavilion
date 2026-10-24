"""
Pavilion's email templates as listmonk wants them: each email (misc_tools/emails.py) rendered from
misc_tools/templates/emails/listmonk/<key>.html in one language, with listmonk's own placeholders
(misc_tools/templatetags/listmonk.py). The store's name is written in (Reset to default writes it again).
"""
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from .emails import BY_KEY
from .templatetags.listmonk import GoData, GoMapping


def _store():
    from admin_portal.models import StoreSettings
    return StoreSettings.load().name


def native(lang) -> str:
    try:
        return translation.get_language_info(lang)["name_local"]
    except KeyError:
        return lang


def template_name(label, lang, store) -> str:
    with translation.override(settings.LANGUAGE_CODE):  # names are in the store's language, whatever the email's
        return f"{store} · {label} · {native(lang)}"


def render(key, lang) -> dict:
    """The listmonk transactional template for email `key` in `lang`."""
    kind, store = BY_KEY[key], _store()
    with translation.override(lang):
        body = render_to_string(f"emails/listmonk/{key}.html", {"tx": GoData(), "store": store, "lang": lang})
        subject = str(kind.subject) % GoMapping(store)
    return {"name": template_name(kind.label, lang, store), "type": "tx", "subject": subject, "body": body}


def render_newsletter(lang) -> dict:
    """The campaign template for newsletters in `lang` (the campaign's content goes in its middle)."""
    store = _store()
    with translation.override(lang):
        body = render_to_string("emails/listmonk/_newsletter.html", {"store": store, "lang": lang})
    return {"name": template_name(translation.gettext_lazy("Newsletter"), lang, store), "type": "campaign", "body": body}
