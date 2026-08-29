class WalletError(Exception):
    default_message = "Wallet error."

    def __init__(self, message=None):
        self.message = message or self.default_message
        super().__init__(self.message)


class InsufficientFunds(WalletError):
    default_message = "Insufficient wallet balance."

    def __init__(self, available=None, required=None, message=None):
        self.available = available
        self.required = required
        if message is None and available is not None and required is not None:
            message = f"Insufficient wallet balance. Available: {available}, required: {required}."
        super().__init__(message)


class InvalidAmount(WalletError):
    default_message = "Amount must be a positive value with at most two decimal places."


class OrderNotPayable(WalletError):
    default_message = "This order cannot be paid with wallet balance."


class DuplicateIdempotencyKey(WalletError):
    default_message = "This idempotency key was already used for a different operation."


class AdjustmentReasonRequired(WalletError):
    default_message = "Administrative adjustments require a reason."


class ImmutableLedgerError(WalletError):
    default_message = "Ledger entries cannot be edited or deleted."


class TopUpNotFound(WalletError):
    default_message = "Wallet top-up was not found."


class TopUpGatewayError(WalletError):
    default_message = "The wallet top-up payment gateway request failed."


class RefundNotAllowed(WalletError):
    default_message = "This ledger entry cannot be refunded."
