from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from payment_core.exceptions import IdempotencyConflict, InvalidStateTransition, ProviderFailure
from payment_core.models import PaymentAttempt, PaymentIntent, PaymentSettlement
from payment_core.providers import (
    FakePaymentProvider,
    NormalizedProviderError,
    ProviderErrorCategory,
    ReversalResult,
    ReversalStatus,
    VerificationResult,
    VerificationStatus,
)
from payment_core.services import (
    create_intent,
    record_reversal,
    start_payment_attempt,
    verify_callback,
)


class RecordingSettlementCoordinator:
    def __init__(self):
        self.settle_calls = []
        self.reverse_calls = []

    def settle(self, intent, idempotency_key):
        self.settle_calls.append((str(intent.id), idempotency_key))

    def reverse(self, intent, idempotency_key):
        self.reverse_calls.append((str(intent.id), idempotency_key))


class PaymentServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="payments@example.com", password="test", is_active=True,
        )
        self.provider = FakePaymentProvider()

    def create_intent(self, key="intent-key", **changes):
        values = {
            "user": self.user,
            "amount_rial": 250000,
            "purpose": PaymentIntent.PURPOSE_ORDER,
            "reference_id": "order-123",
            "description": "Order 123",
            "idempotency_key": key,
            "metadata": {"source": "test"},
        }
        values.update(changes)
        return create_intent(**values)

    def start(self, intent, key="attempt-key"):
        return start_payment_attempt(
            intent=intent,
            provider="fake",
            idempotency_key=key,
            callback_url="https://api.example.com/api/payments/callbacks/fake/",
            adapter=self.provider,
        )

    def test_intent_idempotency_returns_existing_and_rejects_changed_request(self):
        first, created = self.create_intent()
        repeated, repeated_created = self.create_intent()
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first, repeated)
        with self.assertRaises(IdempotencyConflict):
            self.create_intent(amount_rial=250001)

    def test_one_intent_safely_has_multiple_attempts(self):
        intent, _ = self.create_intent()
        first, _ = self.start(intent, "attempt-one")
        second, _ = self.start(intent, "attempt-two")
        repeated, created = self.start(intent, "attempt-one")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(repeated.pk, first.pk)
        self.assertFalse(created)
        self.assertEqual(intent.attempts.count(), 2)

    def test_successful_and_repeated_callbacks_settle_once(self):
        intent, _ = self.create_intent()
        attempt, _ = self.start(intent)
        coordinator = RecordingSettlementCoordinator()

        verified, settlement = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=self.provider,
            settlement_coordinator=coordinator,
        )
        repeated, repeated_settlement = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=self.provider,
            settlement_coordinator=coordinator,
        )

        intent.refresh_from_db()
        self.assertEqual(verified.status, PaymentAttempt.STATUS_VERIFIED)
        self.assertEqual(repeated.status, PaymentAttempt.STATUS_VERIFIED)
        self.assertEqual(intent.status, PaymentIntent.STATUS_SUCCEEDED)
        self.assertEqual(settlement.status, PaymentSettlement.STATUS_SUCCEEDED)
        self.assertEqual(repeated_settlement.status, PaymentSettlement.STATUS_SUCCEEDED)
        self.assertEqual(len(coordinator.settle_calls), 1)
        self.assertEqual(PaymentSettlement.objects.filter(intent=intent).count(), 1)

    def test_failure_is_normalized_and_does_not_settle(self):
        intent, _ = self.create_intent()
        attempt, _ = self.start(intent)
        provider_error = NormalizedProviderError(
            "declined", "Provider declined payment.", ProviderErrorCategory.REJECTED,
        )
        self.provider.queue_verification_result(
            VerificationResult(VerificationStatus.NOT_VERIFIED, error=provider_error)
        )
        coordinator = RecordingSettlementCoordinator()
        failed, settlement = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=self.provider,
            settlement_coordinator=coordinator,
        )
        intent.refresh_from_db()
        self.assertEqual(failed.status, PaymentAttempt.STATUS_FAILED)
        self.assertEqual(failed.error_code, "declined")
        self.assertEqual(intent.status, PaymentIntent.STATUS_FAILED)
        self.assertIsNone(settlement)
        self.assertEqual(coordinator.settle_calls, [])

    def test_late_success_after_failure_is_accepted_and_settled(self):
        intent, _ = self.create_intent()
        attempt, _ = self.start(intent)
        error = NormalizedProviderError("pending", "Not verified yet.", ProviderErrorCategory.REJECTED)
        self.provider.queue_verification_result(
            VerificationResult(VerificationStatus.NOT_VERIFIED, error=error)
        )
        verify_callback(provider="fake", authority=attempt.gateway_authority, adapter=self.provider)
        coordinator = RecordingSettlementCoordinator()
        late, settlement = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=self.provider,
            settlement_coordinator=coordinator,
        )
        self.assertEqual(late.status, PaymentAttempt.STATUS_VERIFIED)
        self.assertEqual(settlement.status, PaymentSettlement.STATUS_SUCCEEDED)
        self.assertEqual(len(coordinator.settle_calls), 1)

    def test_invalid_transitions_and_unique_gateway_ids_are_rejected(self):
        intent, _ = self.create_intent()
        with self.assertRaises(InvalidStateTransition):
            intent.transition_to(PaymentIntent.STATUS_REVERSED)
        intent.status = PaymentIntent.STATUS_REVERSED
        with self.assertRaises(InvalidStateTransition):
            intent.save(update_fields=["status"])
        intent.status = PaymentIntent.STATUS_REQUIRES_PAYMENT
        first, _ = self.start(intent, "attempt-one")
        duplicate = PaymentAttempt(
            intent=intent, provider="other", idempotency_key="attempt-other",
            gateway_authority=first.gateway_authority,
        )
        with self.assertRaises(IntegrityError):
            duplicate.save()

    def test_reversal_is_recorded_and_coordinated_without_domain_imports(self):
        intent, _ = self.create_intent()
        attempt, _ = self.start(intent)
        coordinator = RecordingSettlementCoordinator()
        attempt, _ = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=self.provider,
            settlement_coordinator=coordinator,
        )
        record_reversal(
            attempt=attempt,
            result=ReversalResult(ReversalStatus.REVERSED, attempt.gateway_reference_id),
            settlement_coordinator=coordinator,
        )
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntent.STATUS_REVERSED)
        self.assertEqual(len(coordinator.reverse_calls), 1)

    def test_provider_exception_is_exposed_as_core_error(self):
        intent, _ = self.create_intent()

        def timeout(_request):
            raise TimeoutError

        self.provider.create_payment = timeout
        with self.assertRaises(ProviderFailure) as caught:
            self.start(intent)
        self.assertEqual(caught.exception.provider_error.category, ProviderErrorCategory.TIMEOUT)
