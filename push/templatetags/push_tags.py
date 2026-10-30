from django import template

from push import vapid

register = template.Library()


@register.simple_tag
def vapid_key():
    """The key browsers subscribe with (push/vapid.py), for a page's data attribute."""
    return vapid.public_key()
