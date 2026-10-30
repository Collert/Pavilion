from django.apps import AppConfig


class OnlineStoreConfig(AppConfig):
    name = 'online_store'

    def ready(self):
        from . import order_push  # noqa: F401  (push notifications follow online orders)
