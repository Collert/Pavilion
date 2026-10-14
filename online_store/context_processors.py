import functools

from django.utils import timezone
from events.models import Event


def current_business_event():
    """Today's open Event that hasn't ended yet, or None. Online ordering is only possible during one."""
    now = timezone.localtime(timezone.now())
    return Event.objects.filter(end__date=now.date(), end__gt=now, restaurant_open=True).order_by("end").first()

def check_business_day(request):
    """
    Today's business hours for the online store's templates: `business_day` (bool), `business_day_start_time` and
    `business_day_end_time` ('HH:MM' or None).

    The values are functions (templates call them), so the Event query only runs on pages that use them, once.
    """
    @functools.cache
    def event():
        return current_business_event()

    def local_time(field):
        return lambda: timezone.localtime(getattr(event(), field)).strftime("%H:%M") if event() else None

    return {
        "business_day": lambda: event() is not None,
        "business_day_start_time": local_time("start"),
        "business_day_end_time": local_time("end"),
    }
