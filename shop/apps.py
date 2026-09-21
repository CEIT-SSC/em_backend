from django.apps import AppConfig
from django.conf import settings

class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'

    def ready(self):
        from . import signals
