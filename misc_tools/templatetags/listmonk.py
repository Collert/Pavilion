"""
Writing listmonk's own template code from Django email templates (misc_tools/templates/emails/listmonk/).
listmonk templates use Go's {{ ... }}, which Django would read as its own, so they're written with a tag:

    {% go "if .Tx.Data.message" %}...{% go "end" %}     ->  {{ if .Tx.Data.message }}...{{ end }}
    {% go "range .Tx.Data.items" %}{% go ".name" %}{% go "end" %}

and data with `tx`, which also works inside {% blocktrans with name=tx.name %}: tx.name -> {{ .Tx.Data.name }}.

Campaigns go to whole lists, so their templates and content hold every language: {% bylang %}...{% endbylang %}
renders what's inside once per language in `langs` (the context's list, the default language last) and lets listmonk
pick by the subscriber's language attribute (Pavilion sets it for people with an account; others get the default):

    {{ if eq (or .Subscriber.Attribs.language "en") "uk" }}...{{ else }}...{{ end }}
"""
import datetime

from django import template
from django.utils import dateformat, translation
from django.utils.safestring import mark_safe
from django.utils.translation import gettext

register = template.Library()


@register.simple_tag
def go(code):
    return mark_safe("{{ " + code + " }}")


class GoData:
    """`tx` in email templates: any key is the listmonk expression for that piece of data."""

    def __getitem__(self, key):
        return mark_safe("{{ .Tx.Data.%s }}" % key)

    def __contains__(self, key):
        return True


class GoMapping(dict):
    """For subjects ("Your receipt · %(number)s"): any key is the listmonk expression, %(store)s the store's name."""

    def __init__(self, store):
        super().__init__()
        self.store = store

    def __missing__(self, key):
        return self.store if key == "store" else "{{ .Tx.Data.%s }}" % key


def subscriber_is(lang, default) -> str:
    return '(or .Subscriber.Attribs.language "%s") "%s"' % (default, lang)


@register.simple_tag
def subscriber_language(default):
    """The subscriber's language code, for <html lang>."""
    return mark_safe('{{ or .Subscriber.Attribs.language "%s" }}' % default)


def by_language(render, langs) -> str:
    """listmonk code picking `render(lang)` by the subscriber's language; `langs` ends with the default."""
    *others, default = langs
    parts = [(lang, render(lang)) for lang in others]
    fallback = render(default)
    parts = [(lang, text) for lang, text in parts if text != fallback]
    if not parts:
        return fallback
    out = "".join("{{ %s eq %s }}%s" % ("if" if n == 0 else "else if", subscriber_is(lang, default), text)
                  for n, (lang, text) in enumerate(parts))
    return out + "{{ else }}" + fallback + "{{ end }}"


class ByLanguageNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        def one(lang):
            with translation.override(lang):
                return self.nodelist.render(context)
        return mark_safe(by_language(one, context["langs"]))


@register.tag
def bylang(parser, token):
    nodelist = parser.parse(("endbylang",))
    parser.delete_first_token()
    return ByLanguageNode(nodelist)


@register.filter
def long_day(day):
    """Thursday, November 6, in the language being written."""
    if not isinstance(day, datetime.date):
        return day
    return dateformat.format(day, gettext("l, F j"))


@register.filter
def short_day(day):
    """Thu, Nov 6."""
    if not isinstance(day, datetime.date):
        return day
    return dateformat.format(day, gettext("D, M j"))
