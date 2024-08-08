from django.utils import timezone
from events.models import Event

def check_business_day(request):
    if "/online-store" in request.get_full_path():
        now = timezone.localtime(timezone.now())
        event = Event.objects.filter(end__date=now.date(), end__gt=now, restaurant_open=True).order_by("end").first()
        return {
            "business_day": event is not None,
            "business_day_start_time": timezone.localtime(event.start).strftime('%H:%M') if event else None,
            "business_day_end_time": timezone.localtime(event.end).strftime('%H:%M') if event else None
        }
    return {}