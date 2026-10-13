from django.dispatch import Signal

# Sent with `order` when the last station working on an order finishes it.
order_ready = Signal()


def connect_realtime():
    """Publish SSE nudges when anything the screens show changes (see pavilion/realtime.py)."""
    from django.db.models.signals import post_delete, post_save
    from pavilion.realtime import publish
    from .models import Component, Dish, Ingredient, Order, OrderStation

    def orders_changed(sender, **kwargs):
        publish("orders")

    def inventory_changed(sender, **kwargs):
        publish("inventory")

    for model in (Order, OrderStation):
        post_save.connect(orders_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-save")
        post_delete.connect(orders_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-delete")
    for model in (Dish, Component, Ingredient):
        post_save.connect(inventory_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-save")
