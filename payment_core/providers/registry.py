from .base import PaymentProvider
from .types import ProviderContractError


class ProviderNotRegistered(LookupError):
    pass


class PaymentProviderRegistry:
    def __init__(self):
        self._providers = {}

    @staticmethod
    def _normalize_name(name):
        if not isinstance(name, str) or not name.strip():
            raise ProviderContractError("Provider name must be a non-empty string.")
        return name.strip().lower()

    def register(self, provider: PaymentProvider, *, replace=False):
        if not isinstance(provider, PaymentProvider):
            raise ProviderContractError("provider must implement PaymentProvider.")
        name = self._normalize_name(provider.name)
        if name in self._providers and not replace:
            raise ProviderContractError(f"Payment provider '{name}' is already registered.")
        self._providers[name] = provider
        return provider

    def get(self, name):
        normalized = self._normalize_name(name)
        try:
            return self._providers[normalized]
        except KeyError as exc:
            raise ProviderNotRegistered(f"Payment provider '{normalized}' is not registered.") from exc

    def names(self):
        return tuple(sorted(self._providers))


provider_registry = PaymentProviderRegistry()
