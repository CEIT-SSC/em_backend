from django.test import SimpleTestCase

from payment_core.providers import (
    CreatePaymentRequest,
    FakePaymentProvider,
    InquiryRequest,
    NormalizedProviderError,
    PaymentInquiryStatus,
    PaymentProviderRegistry,
    ProviderContractError,
    ProviderErrorCategory,
    ProviderRuntimeOptions,
    REDACTED,
    ReversalRequest,
    ReversalStatus,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    sanitize_provider_data,
)


class ProviderContractTests(SimpleTestCase):
    def request(self, amount=1000):
        return CreatePaymentRequest(
            amount, "https://api.example.com/callback", "Order", "attempt-1",
        )

    def test_amounts_are_positive_integer_rials(self):
        for invalid in (True, 1.5, "100", 0, -1):
            with self.assertRaises(ProviderContractError):
                self.request(invalid)

    def test_fake_provider_has_idempotent_lifecycle(self):
        provider = FakePaymentProvider()
        created = provider.create_payment(self.request())
        verification = provider.verify_payment(VerificationRequest(created.authority, 1000))
        repeated = provider.verify_payment(VerificationRequest(created.authority, 1000))
        inquiry = provider.inquire_payment(InquiryRequest(created.authority, 1000))
        reversal = provider.reverse_payment(ReversalRequest(created.authority, 1000))
        repeated_reversal = provider.reverse_payment(ReversalRequest(created.authority, 1000))

        self.assertEqual(verification.status, VerificationStatus.VERIFIED)
        self.assertEqual(repeated.status, VerificationStatus.ALREADY_VERIFIED)
        self.assertEqual(inquiry.status, PaymentInquiryStatus.VERIFIED)
        self.assertEqual(reversal.status, ReversalStatus.REVERSED)
        self.assertEqual(repeated_reversal.status, ReversalStatus.ALREADY_REVERSED)

    def test_scripted_failure_and_registry(self):
        error = NormalizedProviderError(
            "provider_timeout", "Timed out", ProviderErrorCategory.TIMEOUT, retryable=True,
        )
        provider = FakePaymentProvider(name="Gateway")
        provider.queue_verification_result(VerificationResult(VerificationStatus.ERROR, error=error))
        registry = PaymentProviderRegistry()
        registry.register(provider)
        self.assertIs(registry.get("GATEWAY"), provider)
        self.assertEqual(
            provider.verify_payment(VerificationRequest("anything", 1)).error.category,
            ProviderErrorCategory.TIMEOUT,
        )

    def test_runtime_options_and_recursive_redaction(self):
        with self.assertRaises(ProviderContractError):
            ProviderRuntimeOptions(read_timeout_seconds=0)
        sanitized = sanitize_provider_data({
            "authorization": "Bearer secret",
            "data": {"merchant_id": "secret", "card_pan": "1234", "ref_id": "safe"},
        })
        self.assertEqual(sanitized["authorization"], REDACTED)
        self.assertEqual(sanitized["data"]["merchant_id"], REDACTED)
        self.assertEqual(sanitized["data"]["card_pan"], REDACTED)
        self.assertEqual(sanitized["data"]["ref_id"], "safe")
