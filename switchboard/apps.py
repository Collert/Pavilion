from django.apps import AppConfig


class SwitchboardConfig(AppConfig):
    """Pavilion's side of Switchboard, the Hamlet workflow builder: events out, actions in (docs: PROJECT_OVERVIEW §6.15)."""
    name = "switchboard"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from pos_server.signals import order_ready

        def ready_order(order, **kwargs):
            from . import events
            events.order_ready(order)

        order_ready.connect(ready_order, dispatch_uid="switchboard-order-ready", weak=False)  # a local function: keep it alive
