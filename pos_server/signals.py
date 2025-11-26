from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from .models import *
from . import globals
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


def broadcast_order_update(order, update_type='order_updated'):
    """
    Broadcast order update to all connected WebSocket clients.
    
    Args:
        order: The Order instance that was updated
        update_type: The type of update ('order_created', 'order_updated', 'order_deleted')
    """
    from .views import collect_order
    
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    
    order_data = collect_order(order) if order else None
    
    # Broadcast to order marking screen
    async_to_sync(channel_layer.group_send)(
        'orders',
        {
            'type': 'order_update',
            'update_type': update_type,
            'order': order_data
        }
    )
    
    # Broadcast to order progress screen
    async_to_sync(channel_layer.group_send)(
        'order_progress',
        {
            'type': 'order_update',
            'update_type': update_type,
            'order': order_data
        }
    )


def broadcast_order_delete(order_id):
    """
    Broadcast order deletion to all connected WebSocket clients.
    
    Args:
        order_id: The ID of the deleted order
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    
    # Broadcast to order marking screen
    async_to_sync(channel_layer.group_send)(
        'orders',
        {
            'type': 'order_delete',
            'order_id': order_id
        }
    )
    
    # Broadcast to order progress screen
    async_to_sync(channel_layer.group_send)(
        'order_progress',
        {
            'type': 'order_delete',
            'order_id': order_id
        }
    )


@receiver(post_save, sender=Order)
def order_saved(sender, instance, created, **kwargs):
    """
    Signal handler for when an Order is saved.
    Broadcasts the update to all connected WebSocket clients.
    """
    update_type = 'order_created' if created else 'order_updated'
    broadcast_order_update(instance, update_type)


@receiver(pre_save, sender=Order)
def store_order_id_before_delete(sender, instance, **kwargs):
    """Store the order ID before deletion for broadcasting."""
    pass  # pre_save is not needed for deletion


@receiver(post_delete, sender=Order)
def order_deleted(sender, instance, **kwargs):
    """
    Signal handler for when an Order is deleted.
    Broadcasts the deletion to all connected WebSocket clients.
    """
    broadcast_order_delete(instance.id)


# This is a corpse, maybe will be needed in the future.

# @receiver(pre_save, sender=Order)
# def kitchen_update(sender, instance, **kwargs):
#     try:
#         prev_order = Order.objects.get(id=instance.id)
#     except:
#         return
#     if instance.kitchen_done != prev_order.kitchen_done:
#         print("ready to pick up")
#         print(instance.kitchen_needed, instance.picked_up)
#         globals.kitchen_update_queue.append(instance)

# @receiver(post_save, sender=Order)
# def model_saved(sender, instance, created, **kwargs):
#     if created:
#         print("created")
#         globals.new_data_queue.append(instance)
#     if instance.kitchen_needed and instance.picked_up:
#         print("picked up")
#         globals.kitchen_done_queue.append(instance)

# @receiver(pre_save, sender=Component)
# @receiver(post_save, sender=Dish)
# def inv_updated(sender, instance, **kwargs):
#     if sender == Component:
#         try:
#             previous = Component.objects.get(id=instance.id)
#         except:
#             return
#         if previous.in_stock == instance.in_stock:
#             globals.stock_updated = "COMPONENT"
#             return
#     globals.stock_updated = "DISH"