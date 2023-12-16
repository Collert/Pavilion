from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order
from .globals import new_data_queue  # Import the queue

@receiver(post_save, sender=Order)
def model_saved(sender, instance, created, **kwargs):
    if created:
        new_data_queue.append(instance)
