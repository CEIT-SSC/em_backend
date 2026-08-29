class PaymentError(Exception):
    """Base error exposed by the provider-independent payment core."""

    code = "payment_error"

    def __init__(self, message=None, *, provider_error=None):
        self.provider_error = provider_error
        super().__init__(message or "A payment operation failed.")


class InvalidPaymentAmount(PaymentError):
    code = "invalid_payment_amount"


class IdempotencyConflict(PaymentError):
    code = "idempotency_conflict"


class InvalidStateTransition(PaymentError):
    code = "invalid_state_transition"


class PaymentNotFound(PaymentError):
    code = "payment_not_found"


class AttemptNotFound(PaymentError):
    code = "attempt_not_found"


class DuplicateGatewayIdentifier(PaymentError):
    code = "duplicate_gateway_identifier"


class ProviderFailure(PaymentError):
    code = "provider_failure"


class SettlementError(PaymentError):
    code = "settlement_error"


class SettlementConfigurationError(SettlementError):
    code = "settlement_not_configured"
