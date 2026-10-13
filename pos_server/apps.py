from django.apps import AppConfig

class PosServerConfig(AppConfig):
    name = 'pos_server'

    def ready(self):
        from .signals import connect_realtime
        connect_realtime()
