"""
Pavilion's email templates as listmonk wants them: each email (misc_tools/emails.py) rendered from
misc_tools/templates/emails/listmonk/<key>.html in one language, with listmonk's own placeholders
(misc_tools/templatetags/listmonk.py). The store's name is written in (Reset to default writes it again).
Marketing emails are campaign templates holding every language (render_campaign); LOOKS are their colours, which
the content Pavilion writes for each campaign (events/announce.py) uses too.
"""
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from .emails import BY_KEY
from .templatetags.listmonk import GoData, GoMapping

LOOKS = {
    "event_open": {"page": "#F3EDE3", "hero": "#1F4A3A", "hero_text": "#FFFFFF", "hero_soft": "#BFD6CB", "accent": "#F4A93B",
                   "accent_ink": "#14261F", "ink": "#1E1A16", "muted": "#6E655B", "soft": "#FBF5EA", "line": "#EADFCD"},
    "event_closed": {"page": "#ECEEF3", "hero": "#272C3A", "hero_text": "#FFFFFF", "hero_soft": "#B9C0D3", "accent": "#FF8A6B",
                     "accent_ink": "#1B1E27", "ink": "#1B1E27", "muted": "#5E6577", "soft": "#F5F6F9", "line": "#E2E5EC"},
}


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


def campaign_languages() -> list:
    """The languages that are on, the store's default last (what {% bylang %} wants)."""
    langs = [code for code, _name in settings.LANGUAGES]
    default = next((c for c in langs if c.lower() == settings.LANGUAGE_CODE.lower()), settings.LANGUAGE_CODE)
    return [c for c in langs if c != default] + [default]


def render_campaign(key) -> dict:
    """The listmonk campaign template for marketing email `key`, in every language that's on."""
    from admin_portal.store_info import full_address
    kind, store = BY_KEY[key], _store()
    langs = campaign_languages()
    with translation.override(langs[-1]):
        body = render_to_string(f"emails/listmonk/{key}.html", {"store": store, "langs": langs, "default": langs[-1],
                                                                 "look": LOOKS[key], "address": full_address()})
        name = f"{store} · {kind.label}"
    return {"name": name, "type": "campaign", "body": body}


def render_newsletter(lang) -> dict:
    """The campaign template for newsletters in `lang` (the campaign's content goes in its middle)."""
    store = _store()
    with translation.override(lang):
        body = render_to_string("emails/listmonk/_newsletter.html", {"store": store, "lang": lang})
    return {"name": template_name(translation.gettext_lazy("Newsletter"), lang, store), "type": "campaign", "body": body}
