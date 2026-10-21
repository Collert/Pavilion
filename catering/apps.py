from django.apps import AppConfig


class CateringConfig(AppConfig):
    name = "catering"

    def ready(self):
        from . import signals  # noqa: F401
