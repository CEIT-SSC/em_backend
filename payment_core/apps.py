from django.apps import AppConfig


class PaymentCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payment_core"
    verbose_name = "Payments"
