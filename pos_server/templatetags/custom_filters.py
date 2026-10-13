from django import template
from datetime import timedelta

register = template.Library()

@register.filter(name='has_options')
def has_options(dish):
    return len(dish.serialize_with_options()["fields"]["choice_components"]) > 0

@register.filter(name='only_choices')
def only_choices(dish):
    return dish.check_if_only_choice_dish()

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