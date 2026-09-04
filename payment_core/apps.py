from django.apps import AppConfig
from django.conf import settings


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def build_zarinpal_provider():
    from .providers import ProviderRuntimeOptions, ZarinpalProvider

    merchant_id = (
        getattr(settings, "ZARINPAL_MERCHANT_ID", "")
        or getattr(settings, "PAYMENT_API_KEY", "")
    )
    return ZarinpalProvider(
        merchant_id=merchant_id,
        runtime_options=ProviderRuntimeOptions(
            sandbox=_as_bool(getattr(settings, "ZARINPAL_SANDBOX", False)),
        ),
    )


class PaymentCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payment_core"
    verbose_name = "Payments"

    def ready(self):
        from .providers import provider_registry

        provider_registry.register(build_zarinpal_provider(), replace=True)
