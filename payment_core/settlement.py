"""Coordinates domain settlement through configured application services only."""

from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string

from .exceptions import SettlementConfigurationError


@dataclass(frozen=True)
class SettlementRequest:
    payment_intent_id: str
    purpose: str
    reference_id: str
    amount_rial: int
    idempotency_key: str
    user_id: int | None
    metadata: dict


class ConfiguredSettlementCoordinator:
    """Loads a purpose-specific application service without importing domain models."""

    def _handler(self, purpose, action):
        handlers = getattr(settings, "PAYMENT_SETTLEMENT_HANDLERS", {})
        path = handlers.get(purpose)
        if not path:
            raise SettlementConfigurationError(
                f"No settlement application service is configured for purpose '{purpose}'."
            )
        handler = import_string(path) if isinstance(path, str) else path
        method = handler if action == "settle" and callable(handler) else getattr(handler, action, None)
        if not callable(method):
            raise SettlementConfigurationError(
                f"Settlement service for '{purpose}' does not support '{action}'."
            )
        return method

    @staticmethod
    def _request(intent, key):
        return SettlementRequest(
            payment_intent_id=str(intent.id),
            purpose=intent.purpose,
            reference_id=intent.reference_id,
            amount_rial=intent.amount_rial,
            idempotency_key=key,
            user_id=intent.user_id,
            metadata=dict(intent.metadata),
        )

    def settle(self, intent, idempotency_key):
        return self._handler(intent.purpose, "settle")(self._request(intent, idempotency_key))

    def reverse(self, intent, idempotency_key):
        return self._handler(intent.purpose, "reverse")(self._request(intent, idempotency_key))
