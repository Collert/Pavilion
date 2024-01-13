from django import template
from datetime import timedelta

register = template.Library()

@register.filter(name='has_kitchen_item')
def has_kitchen_item(dishes):
    return any(item["station"] == "kitchen" for item in dishes)

@register.filter(name='has_bar_item')
def has_bar_item(dishes):
    return any(item["station"] == "bar" for item in dishes)

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