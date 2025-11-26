from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from .notifications import trigger_push_notifications
from .models import *

@receiver(post_save, sender=Delivery)
def new_delivery(sender, instance, created, **kwargs):
    """
    Signal handler for new delivery creation.

    This function is triggered when a new delivery instance is created. It checks if all stations
    are complete for the order associated with the delivery instance. If all statuses are complete,
    it triggers a push notification to inform about the new delivery.

    Args:
        sender (Model): The model class that sent the signal.
        instance (Model instance): The actual instance being saved.
        created (bool): A boolean indicating whether a new record was created.
        **kwargs: Additional keyword arguments.

    Returns:
        None
    """
    # Use the new all_stations_complete method for checking order readiness
    if instance.order.all_stations_complete():
        trigger_push_notifications(
            "New delivery!",
            "New order available to deliver. Check your app!",
            "See delivery",
            reverse("delivery_order", kwargs={"id":instance.id})
        )