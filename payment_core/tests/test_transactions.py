from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from payment_core.models import PaymentIntent, PaymentSettlement
from payment_core.providers import FakePaymentProvider
from payment_core.services import create_intent, start_payment_attempt, verify_callback


class RetryableCoordinator:
    def __init__(self):
        self.calls = 0

    def settle(self, intent, idempotency_key):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary settlement failure")


class CallbackTransactionTests(TransactionTestCase):
    reset_sequences = True

    def test_repeated_callback_retries_failed_settlement_with_the_same_record(self):
        user = get_user_model().objects.create_user(
            email="transaction-payment@example.com", password="test", is_active=True,
        )
        intent, _ = create_intent(
            user=user, amount_rial=5000, purpose=PaymentIntent.PURPOSE_WALLET_TOP_UP,
            reference_id="wallet-transaction", description="Wallet transaction",
            idempotency_key="transaction-intent",
        )
        provider = FakePaymentProvider()
        attempt, _ = start_payment_attempt(
            intent=intent, provider="fake", idempotency_key="transaction-attempt",
            callback_url="https://api.example.com/callback", adapter=provider,
        )
        coordinator = RetryableCoordinator()

        _, first = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=provider,
            settlement_coordinator=coordinator,
        )
        self.assertEqual(first.status, PaymentSettlement.STATUS_FAILED)
        _, second = verify_callback(
            provider="fake", authority=attempt.gateway_authority, adapter=provider,
            settlement_coordinator=coordinator,
        )

        self.assertEqual(second.status, PaymentSettlement.STATUS_SUCCEEDED)
        self.assertEqual(coordinator.calls, 2)
        self.assertEqual(PaymentSettlement.objects.filter(intent=intent).count(), 1)
