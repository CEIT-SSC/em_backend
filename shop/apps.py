from django.apps import AppConfig
from django.conf import settings

class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'

    def ready(self):
        if getattr(settings, "PERIODIC_JOBS_ENABLED", True):
            from .periodic import start
            start()