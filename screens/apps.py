from django.apps import AppConfig


class ScreensConfig(AppConfig):
    name = "screens"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from pavilion.realtime import publish

        from .models import Screen

        def screen_changed(**kwargs):
            publish("screens")  # open screens refetch; a changed layout reloads the whole page

        post_save.connect(screen_changed, sender=Screen, dispatch_uid="realtime-Screen-save")
        post_delete.connect(screen_changed, sender=Screen, dispatch_uid="realtime-Screen-delete")
