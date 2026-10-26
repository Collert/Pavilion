"""
Keeping couriers and the kitchen in step:
- a new delivery order is pushed to every courier on shift;
- the courier who took an order hears when it's ready, and when the restaurant rejects it (the Delivery stays,
  with the reason, until they've seen it; unclaimed ones go with the order);
- any change to a delivery or a shift nudges the screens ("orders"), since it decides what the KDS shows.
"""
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext as _

from pavilion.realtime import publish
from pos_server.models import Order, update_active_orders_cache
from pos_server.signals import order_ready
from .models import CourierShift, Delivery
from .notifications import trigger_push_notifications


def push_later(*args, **kwargs):
    transaction.on_commit(lambda: trigger_push_notifications(*args, **kwargs))


@receiver(post_save, sender=Delivery, dispatch_uid="deliveries-new")
def notify_new_delivery(sender, instance, created, **kwargs):
    if created and instance.order_id and not instance.courier_id:
        push_later(_("New delivery"), _("Order #%(n)s to %(street)s is looking for a courier.") % {
            "n": instance.order_number, "street": instance.street}, _("See order"),
            reverse("delivery_order", kwargs={"id": instance.id}))


@receiver(order_ready, dispatch_uid="deliveries-ready")
def notify_ready(sender, order, **kwargs):
    """Tell the courier who took a delivery order that it's on the pickup shelf."""
    if order.channel != "delivery":
        return
    delivery = order.delivery.filter(courier__isnull=False).first()
    if delivery:
        push_later(_("Ready for pickup"), _("Order #%(n)s is on the shelf.") % {"n": order.id}, _("Open"),
                   reverse("delivery_current"), user_ids=[delivery.courier_id])


@receiver(pre_delete, sender=Order, dispatch_uid="deliveries-rejected")
def order_rejected(sender, instance, **kwargs):
    """The restaurant rejected (deleted) an order: keep what its courier needs to know, drop the rest."""
    from online_store.models import RejectedOrder
    for delivery in Delivery.objects.filter(order=instance):
        if not delivery.courier_id or delivery.delivered_at:
            delivery.delete()
            continue
        rejected = RejectedOrder.objects.filter(order_id=instance.id).first()
        delivery.declined_reason = rejected.reason if rejected else _("Cancelled")
        delivery.save(update_fields=["declined_reason"])
        push_later(_("Order cancelled"), _("The restaurant declined order #%(n)s. Nothing to pick up.") % {"n": instance.id},
                   _("Open"), reverse("delivery_current"), user_ids=[delivery.courier_id])


def couriers_changed(sender, **kwargs):
    # Who's on shift and who took or passed on what decides which delivery orders the KDS shows
    transaction.on_commit(update_active_orders_cache)
    publish("orders")


for model in (Delivery, CourierShift):
    post_save.connect(couriers_changed, sender=model, dispatch_uid=f"deliveries-{model.__name__}-save")
    post_delete.connect(couriers_changed, sender=model, dispatch_uid=f"deliveries-{model.__name__}-delete")
m2m_changed.connect(couriers_changed, sender=Delivery.passed_by.through, dispatch_uid="deliveries-passed")
