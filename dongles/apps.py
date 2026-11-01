from django.apps import AppConfig


class DonglesConfig(AppConfig):
    name = "dongles"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.db.models.signals import post_delete, post_save, pre_delete

        from screens.models import Screen

        from . import pairing
        from .models import Dongle

        def dongle_saved(instance, **kwargs):
            pairing.send_config(instance)  # its TV picks up a new name, screen or orientation straight away

        def dongle_deleted(instance, **kwargs):
            pairing.send(instance.pk, {"type": "unpaired"})  # back to "Scan to start"

        def screen_saved(instance, **kwargs):
            for dongle in instance.dongles.all():  # renamed: the TVs' names for it
                pairing.send_config(dongle)

        def screen_deleted(instance, **kwargs):
            ids = list(instance.dongles.values_list("pk", flat=True))  # SET_NULL doesn't send signals
            from django.db import transaction
            transaction.on_commit(lambda: [pairing.send_config(d) for d in Dongle.objects.filter(pk__in=ids)])

        post_save.connect(dongle_saved, sender=Dongle, dispatch_uid="dongles-saved", weak=False)
        post_delete.connect(dongle_deleted, sender=Dongle, dispatch_uid="dongles-deleted", weak=False)
        post_save.connect(screen_saved, sender=Screen, dispatch_uid="dongles-screen-saved", weak=False)
        pre_delete.connect(screen_deleted, sender=Screen, dispatch_uid="dongles-screen-deleted", weak=False)
