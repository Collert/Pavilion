"""
Web push (push/__init__ is empty; push/send.py sends, push/vapid.py holds the keys, push/kinds.py lists what's sent).

A PushSubscription is one browser that let one of Pavilion's installable apps notify it: the online store (scope
/store/) or the courier app (/deliveries/). Each app has its own service worker, so the same phone has one
subscription per app. Guests' store subscriptions have no user; they're linked to the orders they asked about
(pos_server.Order.push_subscriptions) and get their account once someone signs in on that browser.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PushSubscription(models.Model):
    STORE, DELIVERIES = "store", "deliveries"
    APPS = ((STORE, _("Online store")), (DELIVERIES, _("Deliveries")))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="push_subscriptions", help_text="Empty for a guest")
    app = models.CharField(max_length=16, choices=APPS)
    endpoint = models.URLField(max_length=1024, unique=True, help_text="The push service's address for this browser")
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    device = models.CharField(max_length=80, blank=True, help_text="e.g. “Chrome on Android”, for the account page")
    language = models.CharField(max_length=10, blank=True, help_text="What its notifications are written in")
    # What a store subscription gets besides the orders it's linked to: every order of its account (for a guest, its
    # later orders on that browser are linked when they're placed), and new events
    order_updates = models.BooleanField(default=True)
    event_updates = models.BooleanField(default=False)
    # Campaigns from listmonk (push/listmonk.py): only for accounts, and only on devices that asked
    news_updates = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(default=timezone.now, help_text="When the browser last said it still has it")
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0, help_text="Refusals in a row; it's dropped after a few")

    class Meta:
        indexes = [models.Index(fields=["app", "user"])]

    def __str__(self):
        return f"{self.get_app_display()}: {self.device or self.endpoint[:40]} ({self.user or 'guest'})"

    def info(self) -> dict:
        """What pywebpush wants."""
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


def _remember(sender, instance, **kwargs):
    instance._listmonk_was = (instance.user_id, instance.news_updates)


def _sync_listmonk(sender, instance, **kwargs):
    """A device with news on was added, removed or switched: its account's subscriber gets the new `pavilion_push` list."""
    from django.contrib.auth.models import User
    from misc_tools import listmonk
    if instance.app != PushSubscription.STORE:
        return
    was_user, was_news = getattr(instance, "_listmonk_was", (None, False))
    if kwargs.get("created"):
        if not instance.news_updates:
            return
        was_user = None
    elif "created" in kwargs:  # saved
        if (was_user, was_news) == (instance.user_id, instance.news_updates) or not (was_news or instance.news_updates):
            return
    elif not instance.news_updates:  # deleted, and it had no news to stop
        return
    instance._listmonk_was = (instance.user_id, instance.news_updates)
    for user_id in {instance.user_id, was_user} - {None}:
        user = User.objects.filter(pk=user_id).first()
        if user:
            listmonk.sync_later(user)


models.signals.post_init.connect(_remember, sender=PushSubscription, dispatch_uid="push-listmonk-init")
models.signals.post_save.connect(_sync_listmonk, sender=PushSubscription, dispatch_uid="push-listmonk-save")
models.signals.post_delete.connect(_sync_listmonk, sender=PushSubscription, dispatch_uid="push-listmonk-delete")


class VapidKeys(models.Model):
    """
    The key pair push services know Pavilion by (push/vapid.py). One row, made the first time it's needed: from the
    VAPID_PRIVATE_KEY setting when there is one (so browsers that subscribed with it keep working), else a new pair.
    The private key is encrypted with a key from SECRET_KEY (payments/secrets.py).
    """
    public_key = models.CharField(max_length=128, help_text="Base64url, what browsers subscribe with")
    private_key = models.TextField(help_text="Encrypted")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "VAPID keys"
        verbose_name_plural = "VAPID keys"

    def __str__(self):
        return self.public_key[:16] + "…"


class NotificationSwitch(models.Model):
    """A notification turned on or off on the admin's Notifications page (no row: on). Keys are push/kinds.py's."""
    kind = models.CharField(max_length=40, unique=True)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.kind}: {'on' if self.enabled else 'off'}"
