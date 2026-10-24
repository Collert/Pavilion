from django.apps import AppConfig


class AdminPortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "admin_portal"
    verbose_name = "Admin portal"

    def ready(self):
        from . import languages
        languages.load()  # the store's languages (admin_portal/languages.py)
