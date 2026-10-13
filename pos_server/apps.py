from django.apps import AppConfig

class PosServerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pos_server'

    def ready(self):
        from .signals import connect_realtime
        connect_realtime()
