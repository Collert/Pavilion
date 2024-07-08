from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from .notifications import trigger_push_notifications
from .models import *

@receiver(post_save, sender=Delivery)
def new_delivery(sender, instance, created, **kwargs):
    if created:
        trigger_push_notifications(
            "New delivery!",
            "New order available to deliver. Check your app!",
            "See delivery",
            reverse("delivery_order", kwargs={"id":instance.id})
        )