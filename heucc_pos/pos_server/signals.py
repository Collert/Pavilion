from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import *
from . import globals

@receiver(post_save, sender=Order)
def model_saved(sender, instance, created, **kwargs):
    if created:
        globals.new_data_queue.append(instance)

@receiver(pre_save, sender=Component)
@receiver(post_save, sender=Dish)
def inv_updated(sender, instance, **kwargs):
    if sender == Component:
        previous = Component.objects.get(id=instance.id)
        if previous.in_stock == instance.in_stock:
            return
    globals.stock_updated = True
