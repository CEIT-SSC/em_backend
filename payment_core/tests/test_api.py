from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from payment_core.models import PaymentIntent
from payment_core.providers import FakePaymentProvider, provider_registry


SETTLEMENT_CALLS = []


def record_test_settlement(request):
    SETTLEMENT_CALLS.append(request)


@override_settings(PAYMENT_SETTLEMENT_HANDLERS={
    PaymentIntent.PURPOSE_ORDER: "payment_core.tests.test_api.record_test_settlement",
})
class PaymentApiTests(APITestCase):
    def setUp(self):
        SETTLEMENT_CALLS.clear()
        self.user = get_user_model().objects.create_user(
            email="payment-api@example.com", password="test", is_active=True,
        )
        self.client.force_authenticate(self.user)
        self.provider = FakePaymentProvider()
        provider_registry.register(self.provider, replace=True)

    def test_public_endpoints_and_callback_ignore_browser_status(self):
        create_response = self.client.post("/api/payments/intents/", {
            "amount_rial": 100000,
            "purpose": "order",
            "reference_id": "order-api-1",
            "description": "API order",
            "idempotency_key": "api-intent-key",
        }, format="json")
        self.assertEqual(create_response.status_code, 201)
        intent_id = create_response.data["id"]

        repeated = self.client.post("/api/payments/intents/", {
            "amount_rial": 100000,
            "purpose": "order",
            "reference_id": "order-api-1",
            "description": "API order",
            "idempotency_key": "api-intent-key",
        }, format="json")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(str(repeated.data["id"]), str(intent_id))

        attempt_response = self.client.post(f"/api/payments/intents/{intent_id}/attempts/", {
            "provider": "fake",
            "idempotency_key": "api-attempt-key",
            "callback_url": "https://api.example.com/api/payments/callbacks/fake/",
        }, format="json")
        self.assertEqual(attempt_response.status_code, 201)
        authority = attempt_response.data["gateway_authority"]

        self.client.force_authenticate(user=None)
        callback = self.client.get(
            "/api/payments/callbacks/fake/", {"Authority": authority, "Status": "FAILED"},
        )
        self.assertEqual(callback.status_code, 200)
        self.assertEqual(callback.data["payment_status"], PaymentIntent.STATUS_SUCCEEDED)
        self.assertEqual(len(SETTLEMENT_CALLS), 1)

        repeated_callback = self.client.get(
            "/api/payments/callbacks/fake/", {"Authority": authority, "Status": "OK"},
        )
        self.assertEqual(repeated_callback.status_code, 200)
        self.assertEqual(len(SETTLEMENT_CALLS), 1)

        self.client.force_authenticate(self.user)
        status_response = self.client.get(f"/api/payments/intents/{intent_id}/")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.data["status"], PaymentIntent.STATUS_SUCCEEDED)
        self.assertEqual(len(status_response.data["attempts"]), 1)

    def test_user_cannot_query_another_users_intent(self):
        other = get_user_model().objects.create_user(
            email="payment-api-other@example.com", password="test", is_active=True,
        )
        self.client.force_authenticate(other)
        response = self.client.post("/api/payments/intents/", {
            "amount_rial": 1,
            "purpose": "wallet_top_up",
            "reference_id": "wallet-1",
            "description": "Wallet top-up",
            "idempotency_key": "other-user-intent",
        }, format="json")
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(f"/api/payments/intents/{response.data['id']}/").status_code, 404)
