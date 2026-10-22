from django.apps import AppConfig


class AudioConfig(AppConfig):
    name = "audio"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .library import ensure_folder
        ensure_folder()  # media/files/music/: drop songs here (development and NAS mounts)
