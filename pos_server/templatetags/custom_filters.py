from django import template
from datetime import timedelta

register = template.Library()

@register.filter(name='format_duration')
def format_duration(value: timedelta):
    try:
        # Extract total seconds from timedelta
        total_seconds = int(value.total_seconds())
        
        # Calculate minutes and seconds
        minutes = total_seconds // 60
        seconds = total_seconds % 60

        # Format the string as MM:SS
        return f"{minutes:02d}:{seconds:02d}"
    except (ValueError, TypeError, AttributeError):
        return ""

@register.simple_tag
def kds_url():
    """The kitchen display covering every station."""
    from django.urls import reverse
    from django.utils.http import urlencode
    from pos_server.models import Station
    codes = list(Station.objects.values_list("code", flat=True))
    return reverse("order_marking") + ("?" + urlencode([("filter", code) for code in codes]) if codes else "")
