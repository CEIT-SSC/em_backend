"""Unit tests for ZarinpalProvider in payment_core."""

from unittest.mock import MagicMock, patch
from django.test import TestCase
import requests

from payment_core.providers.types import (
    CreatePaymentRequest,
    CustomerData,
    InquiryRequest,
    PaymentInquiryStatus,
    PaymentRequestStatus,
    ProviderErrorCategory,
    ProviderHealthStatus,
    ProviderRuntimeOptions,
    ReversalRequest,
    ReversalStatus,
    VerificationRequest,
    VerificationStatus,
)
from payment_core.providers.zarinpal import ZarinpalProvider

VALID_MERCHANT_ID = "11111111-2222-3333-4444-555555555555"


class ZarinpalProviderTests(TestCase):
    def setUp(self):
        self.provider = ZarinpalProvider(
            merchant_id=VALID_MERCHANT_ID,
            runtime_options=ProviderRuntimeOptions(sandbox=True),
        )

    # ── Configuration & Health ───────────────────────────────────────────────

    def test_validate_configuration_valid(self):
        result = self.provider.validate_configuration()
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())

    def test_validate_configuration_invalid_uuid(self):
        invalid_provider = ZarinpalProvider(merchant_id="invalid-merchant-uuid")
        result = invalid_provider.validate_configuration()
        self.assertFalse(result.valid)
        self.assertIn("not a valid UUID", result.errors[0])

    def test_validate_configuration_empty(self):
        empty_provider = ZarinpalProvider(merchant_id="")
        result = empty_provider.validate_configuration()
        self.assertFalse(result.valid)
        self.assertIn("not set", result.errors[0])

    def test_check_health_healthy(self):
        health = self.provider.check_health()
        self.assertEqual(health.status, ProviderHealthStatus.HEALTHY)

    def test_check_health_unhealthy(self):
        invalid_provider = ZarinpalProvider(merchant_id="invalid")
        health = invalid_provider.check_health()
        self.assertEqual(health.status, ProviderHealthStatus.UNAVAILABLE)

    # ── Redirect URL ──────────────────────────────────────────────────────────

    def test_generate_redirect_url(self):
        authority = "A0000000000000000000000000000wwOGYpd"
        url = self.provider.generate_redirect_url(authority)
        self.assertEqual(url, f"https://sandbox.zarinpal.com/pg/StartPay/{authority}")

    def test_production_urls(self):
        prod_provider = ZarinpalProvider(
            merchant_id=VALID_MERCHANT_ID,
            runtime_options=ProviderRuntimeOptions(sandbox=False),
        )
        authority = "A000"
        self.assertEqual(prod_provider.generate_redirect_url(authority), f"https://payment.zarinpal.com/pg/StartPay/{authority}")

    # ── Create Payment ───────────────────────────────────────────────────────

    @patch("requests.post")
    def test_create_payment_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "code": 100,
                "message": "Success",
                "authority": "A0000000000000000000000000000wwOGYpd",
                "fee_type": "Merchant",
                "fee": 100,
            },
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = CreatePaymentRequest(
            amount_rial=100000,
            callback_url="https://example.com/callback",
            description="Test payment",
            client_reference="order-123",
            customer=CustomerData(email="test@example.com", mobile="09121111111"),
        )
        res = self.provider.create_payment(req)

        self.assertEqual(res.status, PaymentRequestStatus.CREATED)
        self.assertEqual(res.authority, "A0000000000000000000000000000wwOGYpd")
        self.assertIsNone(res.error)

    @patch("requests.post")
    def test_create_payment_rejected(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"code": -9, "message": "Validation error"},
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = CreatePaymentRequest(
            amount_rial=100000,
            callback_url="https://example.com/callback",
            description="Test payment",
            client_reference="order-123",
        )
        res = self.provider.create_payment(req)

        self.assertEqual(res.status, PaymentRequestStatus.REJECTED)
        self.assertIsNotNone(res.error)
        self.assertEqual(res.error.code, "validation_error")
        self.assertEqual(res.error.category, ProviderErrorCategory.REJECTED)

    @patch("requests.post")
    def test_create_payment_timeout(self, mock_post):
        mock_post.side_effect = requests.Timeout("Connection timed out")

        req = CreatePaymentRequest(
            amount_rial=100000,
            callback_url="https://example.com/callback",
            description="Test payment",
            client_reference="order-123",
        )
        res = self.provider.create_payment(req)

        self.assertEqual(res.status, PaymentRequestStatus.ERROR)
        self.assertIsNotNone(res.error)
        self.assertEqual(res.error.code, "provider_timeout")
        self.assertEqual(res.error.category, ProviderErrorCategory.TIMEOUT)
        self.assertTrue(res.error.retryable)

    # ── Verify Payment ───────────────────────────────────────────────────────

    @patch("requests.post")
    def test_verify_payment_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "code": 100,
                "message": "Verified",
                "card_pan": "502229******5995",
                "ref_id": 20102030,
            },
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = VerificationRequest(
            authority="A0000000000000000000000000000wwOGYpd",
            expected_amount_rial=100000,
        )
        res = self.provider.verify_payment(req)

        self.assertEqual(res.status, VerificationStatus.VERIFIED)
        self.assertEqual(res.transaction_reference, "20102030")
        self.assertEqual(res.verified_amount_rial, 100000)

    @patch("requests.post")
    def test_verify_payment_already_verified(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "code": 101,
                "message": "Verified already",
                "card_pan": "502229******5995",
                "ref_id": 20102030,
            },
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = VerificationRequest(
            authority="A0000000000000000000000000000wwOGYpd",
            expected_amount_rial=100000,
        )
        res = self.provider.verify_payment(req)

        self.assertEqual(res.status, VerificationStatus.ALREADY_VERIFIED)
        self.assertEqual(res.transaction_reference, "20102030")

    @patch("requests.post")
    def test_verify_payment_amount_mismatch(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"code": -50, "message": "Amount mismatch"},
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = VerificationRequest(
            authority="A0000000000000000000000000000wwOGYpd",
            expected_amount_rial=100000,
        )
        res = self.provider.verify_payment(req)

        self.assertEqual(res.status, VerificationStatus.AMOUNT_MISMATCH)
        self.assertEqual(res.error.category, ProviderErrorCategory.AMOUNT_MISMATCH)

    # ── Inquiry Payment ───────────────────────────────────────────────────────

    @patch("requests.post")
    def test_inquire_payment_verified(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "code": 100,
                "status": "SUCCESS",
                "amount": 100000,
                "ref_id": 20102030,
            },
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = InquiryRequest(authority="A0000000000000000000000000000wwOGYpd")
        res = self.provider.inquire_payment(req)

        self.assertEqual(res.status, PaymentInquiryStatus.VERIFIED)
        self.assertEqual(res.transaction_reference, "20102030")
        self.assertEqual(res.amount_rial, 100000)

    # ── Reverse Payment ───────────────────────────────────────────────────────

    @patch("requests.post")
    def test_reverse_payment_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"code": 100, "message": "Reversed"},
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = ReversalRequest(
            authority="A0000000000000000000000000000wwOGYpd",
            expected_amount_rial=100000,
        )
        res = self.provider.reverse_payment(req)

        self.assertEqual(res.status, ReversalStatus.REVERSED)

    @patch("requests.post")
    def test_reverse_payment_already_reversed(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"code": -61, "message": "Already reversed"},
            "errors": [],
        }
        mock_post.return_value = mock_response

        req = ReversalRequest(
            authority="A0000000000000000000000000000wwOGYpd",
            expected_amount_rial=100000,
        )
        res = self.provider.reverse_payment(req)

        self.assertEqual(res.status, ReversalStatus.ALREADY_REVERSED)
