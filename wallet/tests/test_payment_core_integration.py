from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APITestCase

from payment_core.models import PaymentAttempt, PaymentIntent, PaymentSettlement
from wallet.models import WalletEntry, WalletTopUp
from wallet.services import WalletService
from wallet.tests.helpers import FakePaymentClient, make_user, register_fake_zarinpal


@override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
class WalletPaymentCoreIntegrationTests(APITestCase):
    def setUp(self):
        self.user = make_user('wallet-core@example.com')
        self.client.force_authenticate(self.user)

    def test_topup_uses_core_intent_attempt_and_idempotent_settlement(self):
        provider = FakePaymentClient()
        topup, _ = WalletService.start_topup(
            self.user,
            Decimal('12.50'),
            callback_url='https://example.com/api/wallet/top-ups/callback/',
            payment_client=provider,
        )

        self.assertIsNotNone(topup.payment_intent_id)
        self.assertIsNotNone(topup.payment_attempt_id)
        self.assertEqual(topup.payment_intent.amount_rial, 125)
        self.assertEqual(topup.payment_intent.status, PaymentIntent.STATUS_PROCESSING)
        self.assertEqual(topup.payment_attempt.status, PaymentAttempt.STATUS_PENDING)
        self.assertEqual(provider.last_create['amount'], Decimal('12.5'))

        credited, created = WalletService.credit_verified_topup(
            topup.gateway_authority, payment_client=provider,
        )
        repeated, repeated_created = WalletService.credit_verified_topup(
            topup.gateway_authority, payment_client=provider,
        )

        credited.payment_intent.refresh_from_db()
        credited.payment_attempt.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(repeated.pk, credited.pk)
        self.assertEqual(credited.status, WalletTopUp.STATUS_CREDITED)
        self.assertEqual(credited.payment_intent.status, PaymentIntent.STATUS_SUCCEEDED)
        self.assertEqual(credited.payment_attempt.status, PaymentAttempt.STATUS_VERIFIED)
        self.assertEqual(PaymentSettlement.objects.filter(intent=credited.payment_intent).count(), 1)
        self.assertEqual(WalletEntry.objects.filter(topup=credited).count(), 1)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('12.50'))

    def test_wallet_callback_ignores_browser_status_and_uses_provider_verification(self):
        provider = FakePaymentClient()
        register_fake_zarinpal(self, provider)
        response = self.client.post('/api/wallet/top-ups/', {'amount': '20.00'}, format='json')
        topup = WalletTopUp.objects.get(public_id=response.data['public_id'])
        self.client.force_authenticate(user=None)
        callback = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': topup.gateway_authority,
            'Status': 'NOK',
        })

        self.assertEqual(callback.status_code, 302)
        topup.refresh_from_db()
        self.assertEqual(topup.status, WalletTopUp.STATUS_CREDITED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('20.00'))
