from django.db.models.signals import post_save
from django.dispatch import receiver
from pos_server.models import Ingredient
from . import globals

@receiver(post_save, sender=Ingredient)
def model_saved(sender, instance, created, **kwargs):
    globals.new_inventory_updates = True
