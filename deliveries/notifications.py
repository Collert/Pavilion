"""Push notifications to couriers (push/: the shared PushSubscription, app "deliveries"), from deliveries/signals.py."""
from django.templatetags.static import static

from push import send
from push.models import PushSubscription


def trigger_push_notifications(title, body, action_name, action_endpoint, user_ids=None):
    """
    Push a notification to couriers on shift (or only to `user_ids`), with a button that opens `action_endpoint`.
    Called after the change commits (signals.push_later); sent in the background.
    """
    from .models import CourierShift
    active = CourierShift.objects.values_list("user_id", flat=True)
    if user_ids is not None:
        active = active.filter(user_id__in=list(user_ids))
    subscriptions = PushSubscription.objects.filter(app=PushSubscription.DELIVERIES, user_id__in=list(active))
    message = {"title": title, "body": body, "action": action_name, "url": action_endpoint,
               "icon": static("deliveries/icon-192x192.png"), "badge": static("deliveries/icon-monochrome.png")}
    subs = list(subscriptions)
    if subs:
        send.background(lambda: send.send_now(subs, lambda: message, urgency="high"))
