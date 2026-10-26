from django.apps import AppConfig


class TeamConfig(AppConfig):
    name = "team"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # The staff side of the account (Shifts, Up for grabs, Availability and time off) refetches on "schedule"
        from django.db.models.signals import post_delete, post_save

        from pavilion.realtime import publish

        from .models import Availability, Shift, ShiftOffer, TimeOff

        def changed(**kwargs):
            publish("schedule")

        for model in (Shift, ShiftOffer, TimeOff, Availability):
            post_save.connect(changed, sender=model, dispatch_uid=f"realtime-schedule-{model.__name__}-save", weak=False)
            post_delete.connect(changed, sender=model, dispatch_uid=f"realtime-schedule-{model.__name__}-delete", weak=False)
