import json
from pywebpush import webpush, WebPushException
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.templatetags.static import static

# This is a bit weird, I know. But that's the best way I could figure out how to do this without making it too complex and with no circular imports.
class PushSubscription(models.Model):
    """
    PushSubscription model to store web push notification subscription details for a user.
    Attributes:
        user (ForeignKey): A reference to the User model, representing the user who owns the subscription.
        endpoint (TextField): The endpoint URL for the push service.
        p256dh (TextField): The public key used in the push subscription.
        auth (TextField): The authentication secret used in the push subscription.
    Methods:
        __str__(): Returns a string representation of the subscription, including the username and endpoint.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="web_notifications_sub")
    endpoint = models.TextField()
    p256dh = models.TextField()
    auth = models.TextField()

    def __str__(self):
        return f"{self.user.username} - {self.endpoint}"

def send_push_notification(subscription_info, message_body):
    """
    Sends a push notification using the provided subscription information and message body.

    Args:
        subscription_info (dict): The subscription information required to send the push notification.
        message_body (dict): The message content to be sent in the push notification.

    Returns:
        Response: The response from the web push service.

    Raises:
        WebPushException: If there is an error sending the push notification.

    Example:
        subscription_info = {
            "endpoint": "https://example.com/...",
            "keys": {
                "p256dh": "BNc...",
                "auth": "..."
        message_body = {
            "title": "New Notification",
            "body": "You have a new message."
        send_push_notification(subscription_info, message_body)
    """
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

def trigger_push_notifications(title, body, action_name, action_endpoint, recipients = []):
    """
    Sends push notifications to a list of recipients.
    Args:
        title (str): The title of the notification.
        body (str): The body content of the notification.
        action_name (str): The name of the action button in the notification.
        action_endpoint (str): The endpoint URL that the action button will open.
        recipients (list): A list of recipient user objects to send the notification to.
    Returns:
        None
    """
    HOST = settings.NOTIFICATIONS_HOST
    subscriptions = PushSubscription.objects.filter(user__in=recipients)
    message_body = {
        "title": title,
        "body": body,
        "icon": static("online_store/icon-192x192.png"),
        "badge": static("online_store/icon-monochrome.png"),
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