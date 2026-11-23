from django.apps import AppConfig


class SwitchboardConfig(AppConfig):
    """Pavilion's side of Switchboard, the Hamlet workflow builder: events out, actions in (docs: PROJECT_OVERVIEW §6.15)."""
    name = "switchboard"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import hooks
        hooks.connect()
