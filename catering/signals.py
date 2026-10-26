"""Tell open admin pages (and customer order pages) that catering orders changed."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from pavilion.realtime import publish

from .models import CateringLine, CateringMessage, CateringOrder


@receiver([post_save, post_delete], sender=CateringOrder)
@receiver([post_save, post_delete], sender=CateringLine)
@receiver([post_save, post_delete], sender=CateringMessage)
def catering_changed(sender, **kwargs):
    publish("catering")
