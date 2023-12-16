from django.apps import AppConfig

class PosServerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pos_server'

    def ready(self):
        # Import the signal handlers here
        from . import signals  # Replace '.' with your actual app name if needed
