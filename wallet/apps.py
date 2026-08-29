from django.apps import AppConfig


class WalletConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wallet'

    def ready(self):
        from payment_core.providers import provider_registry
        from .payment_provider import ZarinpalPaymentProvider

        provider_registry.register(ZarinpalPaymentProvider(), replace=True)
    verbose_name = 'Wallet'
