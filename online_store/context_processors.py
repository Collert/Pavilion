from django.utils import timezone
from events.models import Event

def check_business_day(request):
    """
    Checks if the current time falls within a business day for the online store.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        dict: A dictionary containing:
            - "business_day" (bool): True if it is a business day, False otherwise.
            - "business_day_start_time" (str or None): The start time of the business day in 'HH:MM' format if it is a business day, None otherwise.
            - "business_day_end_time" (str or None): The end time of the business day in 'HH:MM' format if it is a business day, None otherwise.
    """
    now = timezone.localtime(timezone.now())
    event = Event.objects.filter(end__date=now.date(), end__gt=now, restaurant_open=True).order_by("end").first()
    return {
        "business_day": event is not None,
        "business_day_start_time": timezone.localtime(event.start).strftime('%H:%M') if event else None,
        "business_day_end_time": timezone.localtime(event.end).strftime('%H:%M') if event else None
    }
    return {}
