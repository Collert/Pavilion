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

        post_save.connect(screen_changed, sender=Screen, dispatch_uid="realtime-Screen-save", weak=False)

        from pos_server.signals import order_ready

        def call_out(order, **kwargs):
            """An order is ready: screens with the order-ready pop-up call it out (screens/popups.py)."""
            from .popups import record
            from .providers import public_name
            if order.channel != "delivery":  # couriers collect those
                record("order_ready", {"name": public_name(order.name) or "", "number": f"{order.id:04d}"})

        order_ready.connect(call_out, dispatch_uid="screens-order-ready", weak=False)  # a local function: keep it alive
        post_delete.connect(screen_changed, sender=Screen, dispatch_uid="realtime-Screen-delete", weak=False)
