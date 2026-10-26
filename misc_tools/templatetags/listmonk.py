"""
Writing listmonk's own template code from Django email templates (misc_tools/templates/emails/listmonk/).
listmonk templates use Go's {{ ... }}, which Django would read as its own, so they're written with a tag:

    {% go "if .Tx.Data.message" %}...{% go "end" %}     ->  {{ if .Tx.Data.message }}...{{ end }}
    {% go "range .Tx.Data.items" %}{% go ".name" %}{% go "end" %}

and data with `tx`, which also works inside {% blocktrans with name=tx.name %}: tx.name -> {{ .Tx.Data.name }}.
"""
from django import template
from django.utils.safestring import mark_safe

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
