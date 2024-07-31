import json
from pywebpush import webpush, WebPushException
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.templatetags.static import static
from . import globals

# This is a bit weird, I know. But that's the best way I could figure out how to do this without making it too complex and with no circular imports.
class PushSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    endpoint = models.TextField()
    p256dh = models.TextField()
    auth = models.TextField()

    def __str__(self):
        return f"{self.user.username} - {self.endpoint}"

def send_push_notification(subscription_info, message_body):
    try:
        response = webpush(
            subscription_info=subscription_info,
            data=json.dumps(message_body),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": "mailto:your-email@example.com"
            }
        )
        return response
    except WebPushException as ex:
        print("WebPush error: {}", repr(ex))
        if ex.response and ex.response.json():
            extra = ex.response.json()
            print("Remote service replied with a {}:{}, {}",
                  extra.code,
                  extra.errno,
                  extra.message)

def trigger_push_notifications(title, body, action_name, action_endpoint):
    HOST = settings.NOTIFICATIONS_HOST
    active = []
    for cour in globals.active_couriers:
        dict_cour = dict(cour)
        active.append(dict_cour["user"])
    subscriptions = PushSubscription.objects.filter(user__in=active)
    message_body = {
        "title": title,
        "body": body,
        "icon": static("deliveries/icon-192x192.png"),
        "badge": static("deliveries/icon-monochrome.png"),
        "actions": [
            {
                "action": "open_url",
                "title": action_name,
                "url": HOST + action_endpoint
            }
        ],
        "data": {
            "url": HOST + action_endpoint
        }
    }

    for subscription in subscriptions:
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth
            }
        }
        send_push_notification(subscription_info, message_body)