from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import *
from . import globals

@receiver(pre_save, sender=Order)
def kitchen_update(sender, instance, **kwargs):
    try:
        prev_order = Order.objects.get(id=instance.id)
    except:
        return
    if instance.kitchen_done != prev_order.kitchen_done:
        print("ready to pick up")
        print(instance.kitchen_needed, instance.picked_up)
        globals.kitchen_update_queue.append(instance)

@receiver(post_save, sender=Order)
def model_saved(sender, instance, created, **kwargs):
    if created:
        print("created")
        globals.new_data_queue.append(instance)
    if instance.kitchen_needed and instance.picked_up:
        print("picked up")
        globals.kitchen_done_queue.append(instance)

@receiver(pre_save, sender=Component)
@receiver(post_save, sender=Dish)
def inv_updated(sender, instance, **kwargs):
    if sender == Component:
        try:
            previous = Component.objects.get(id=instance.id)
        except:
            return
        if previous.in_stock == instance.in_stock:
            globals.stock_updated = "COMPONENT"
            return
    globals.stock_updated = "DISH"