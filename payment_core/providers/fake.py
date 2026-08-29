"""Deterministic in-memory provider used by payment-core tests."""

from collections import deque
from dataclasses import dataclass, field
from urllib.parse import quote

from .base import PaymentProvider
from .types import (
    ConfigurationValidationResult,
    InquiryResult,
    NormalizedProviderError,
    PaymentInquiryStatus,
    PaymentRequestResult,
    PaymentRequestStatus,
    ProviderErrorCategory,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderRuntimeOptions,
    ReversalResult,
    ReversalStatus,
    VerificationResult,
    VerificationStatus,
)


@dataclass
class FakeProviderCalls:
    create_payment: list = field(default_factory=list)
    generate_redirect_url: list[str] = field(default_factory=list)
    verify_payment: list = field(default_factory=list)
    inquire_payment: list = field(default_factory=list)
    reverse_payment: list = field(default_factory=list)
    validate_configuration: int = 0
    check_health: int = 0


@dataclass
class _FakeAttempt:
    amount_rial: int
    status: PaymentInquiryStatus = PaymentInquiryStatus.PENDING
    transaction_reference: str | None = None


class FakePaymentProvider(PaymentProvider):
    def __init__(
        self, *, name="fake", redirect_base_url="https://fake-payments.example/redirect",
        runtime_options=None, configured=True, healthy=True,
    ):
        self._name = name.strip().lower()
        self._redirect_base_url = redirect_base_url.rstrip("/")
        self._runtime_options = runtime_options or ProviderRuntimeOptions(sandbox=True)
        self.configured = configured
        self.healthy = healthy
        self.calls = FakeProviderCalls()
        self._attempts = {}
        self._authority_sequence = 0
        self._transaction_sequence = 0
        self._create_results = deque()
        self._verification_results = deque()
        self._inquiry_results = deque()
        self._reversal_results = deque()

    @property
    def name(self):
        return self._name

    @property
    def runtime_options(self):
        return self._runtime_options

    def queue_create_result(self, result):
        self._create_results.append(result)

    def queue_verification_result(self, result):
        self._verification_results.append(result)

    def queue_inquiry_result(self, result):
        self._inquiry_results.append(result)

    def queue_reversal_result(self, result):
        self._reversal_results.append(result)

    def seed_attempt(
        self, authority, amount_rial, *, status=PaymentInquiryStatus.PENDING,
        transaction_reference=None,
    ):
        self._attempts[authority] = _FakeAttempt(amount_rial, status, transaction_reference)

    @staticmethod
    def _not_found_error():
        return NormalizedProviderError(
            "payment_not_found", "Payment authority was not found.", ProviderErrorCategory.NOT_FOUND,
        )

    @staticmethod
    def _amount_mismatch_error():
        return NormalizedProviderError(
            "amount_mismatch", "Payment amount does not match.", ProviderErrorCategory.AMOUNT_MISMATCH,
        )

    def create_payment(self, request):
        self.calls.create_payment.append(request)
        if self._create_results:
            result = self._create_results.popleft()
            if result.status == PaymentRequestStatus.CREATED:
                self.seed_attempt(result.authority, request.amount_rial)
            return result
        self._authority_sequence += 1
        authority = f"fake-authority-{self._authority_sequence}"
        self.seed_attempt(authority, request.amount_rial)
        return PaymentRequestResult(PaymentRequestStatus.CREATED, authority=authority)

    def generate_redirect_url(self, authority):
        self.calls.generate_redirect_url.append(authority)
        return f"{self._redirect_base_url}/{quote(authority, safe='')}"

    def verify_payment(self, request):
        self.calls.verify_payment.append(request)
        if self._verification_results:
            return self._verification_results.popleft()
        attempt = self._attempts.get(request.authority)
        if attempt is None:
            return VerificationResult(VerificationStatus.NOT_FOUND, error=self._not_found_error())
        if attempt.amount_rial != request.expected_amount_rial:
            return VerificationResult(
                VerificationStatus.AMOUNT_MISMATCH,
                verified_amount_rial=attempt.amount_rial,
                error=self._amount_mismatch_error(),
            )
        if attempt.status == PaymentInquiryStatus.VERIFIED:
            return VerificationResult(
                VerificationStatus.ALREADY_VERIFIED,
                transaction_reference=attempt.transaction_reference,
                verified_amount_rial=attempt.amount_rial,
            )
        if attempt.status in {PaymentInquiryStatus.FAILED, PaymentInquiryStatus.REVERSED}:
            return VerificationResult(
                VerificationStatus.NOT_VERIFIED,
                error=NormalizedProviderError(
                    "payment_not_verifiable", "Payment is not verifiable.", ProviderErrorCategory.REJECTED,
                ),
            )
        self._transaction_sequence += 1
        attempt.status = PaymentInquiryStatus.VERIFIED
        attempt.transaction_reference = f"fake-ref-{self._transaction_sequence}"
        return VerificationResult(
            VerificationStatus.VERIFIED,
            transaction_reference=attempt.transaction_reference,
            verified_amount_rial=attempt.amount_rial,
        )

    def inquire_payment(self, request):
        self.calls.inquire_payment.append(request)
        if self._inquiry_results:
            return self._inquiry_results.popleft()
        attempt = self._attempts.get(request.authority)
        if attempt is None:
            return InquiryResult(PaymentInquiryStatus.NOT_FOUND, error=self._not_found_error())
        if request.expected_amount_rial is not None and request.expected_amount_rial != attempt.amount_rial:
            return InquiryResult(
                PaymentInquiryStatus.ERROR, amount_rial=attempt.amount_rial,
                error=self._amount_mismatch_error(),
            )
        return InquiryResult(
            attempt.status, transaction_reference=attempt.transaction_reference,
            amount_rial=attempt.amount_rial,
        )

    def reverse_payment(self, request):
        self.calls.reverse_payment.append(request)
        if self._reversal_results:
            return self._reversal_results.popleft()
        attempt = self._attempts.get(request.authority)
        if attempt is None:
            return ReversalResult(ReversalStatus.NOT_FOUND, error=self._not_found_error())
        if attempt.amount_rial != request.expected_amount_rial:
            return ReversalResult(ReversalStatus.ERROR, error=self._amount_mismatch_error())
        if attempt.status == PaymentInquiryStatus.REVERSED:
            return ReversalResult(
                ReversalStatus.ALREADY_REVERSED, transaction_reference=attempt.transaction_reference,
            )
        if attempt.status != PaymentInquiryStatus.VERIFIED:
            return ReversalResult(
                ReversalStatus.NOT_REVERSIBLE,
                error=NormalizedProviderError(
                    "payment_not_reversible", "Payment is not reversible.", ProviderErrorCategory.REJECTED,
                ),
            )
        attempt.status = PaymentInquiryStatus.REVERSED
        return ReversalResult(ReversalStatus.REVERSED, transaction_reference=attempt.transaction_reference)

    def validate_configuration(self):
        self.calls.validate_configuration += 1
        if self.configured:
            return ConfigurationValidationResult(True)
        return ConfigurationValidationResult(False, errors=("Fake provider is not configured.",))

    def check_health(self):
        self.calls.check_health += 1
        status = ProviderHealthStatus.HEALTHY if self.healthy else ProviderHealthStatus.UNAVAILABLE
        return ProviderHealthResult(status)
