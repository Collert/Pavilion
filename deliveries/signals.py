from django.dispatch import receiver
from django.urls import reverse

from pos_server.signals import order_ready
from .notifications import trigger_push_notifications


@receiver(order_ready)
def notify_couriers(sender, order, **kwargs):
    """Push "New delivery!" to couriers when a delivery order is ready at every station."""
    if order.channel != "delivery":
        return
    delivery = order.delivery.first()
    if delivery is None:
        return
    trigger_push_notifications(
        "New delivery!",
        "New order available to deliver. Check your app!",
        "See delivery",
        reverse("delivery_order", kwargs={"id": delivery.id})
    )
