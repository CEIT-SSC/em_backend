from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, override_settings

from wallet.exceptions import TopUpGatewayError
from wallet.payments import get_wallet_payment_url
from wallet.views import _frontend_topup_redirect


class PaymentHandoffTests(SimpleTestCase):
    @override_settings(WALLET_PAYMENT_START_URL='https://ceit-ssc.ir/payment/start')
    def test_production_and_sandbox_links_pass_through_registered_domain(self):
        for host in ('payment.zarinpal.com', 'sandbox.zarinpal.com'):
            gateway = f'https://{host}/pg/StartPay/A123'
            target = urlparse(get_wallet_payment_url(gateway))
            self.assertEqual(target.netloc, 'ceit-ssc.ir')
            self.assertEqual(target.path, '/payment/start')
            self.assertEqual(parse_qs(target.query), {'gateway': [gateway]})

    @override_settings(WALLET_PAYMENT_START_URL='')
    def test_existing_direct_flow_is_preserved_when_disabled(self):
        gateway = 'https://payment.zarinpal.com/pg/StartPay/A123'
        self.assertEqual(get_wallet_payment_url(gateway), gateway)

    @override_settings(WALLET_PAYMENT_START_URL='https://ceit-ssc.ir/payment/start')
    def test_other_providers_are_not_sent_to_zarinpal_handoff(self):
        gateway = 'https://other-provider.example/pay/123'
        self.assertEqual(get_wallet_payment_url(gateway), gateway)

    def test_invalid_start_configuration_is_rejected_before_payment(self):
        for start in ('/payment/start', 'http://ceit-ssc.ir/payment/start',
                      'https://ceit-ssc.ir/payment/start?x=1',
                      'https://user@ceit-ssc.ir/payment/start'):
            with self.subTest(start=start), override_settings(WALLET_PAYMENT_START_URL=start):
                with self.assertRaises(TopUpGatewayError):
                    get_wallet_payment_url(None)

    @override_settings(
        FRONTEND_URL='https://ceit-ssc.ir',
        WALLET_PAYMENT_RETURN_URL='https://gamecraft.ir/wallet/top-up/callback',
    )
    def test_return_to_gamecraft_preserves_verified_result(self):
        target = urlparse(_frontend_topup_redirect({'success': 'true', 'order_id': '42'}))
        self.assertEqual(target.netloc, 'gamecraft.ir')
        self.assertEqual(target.path, '/wallet/top-up/callback')
        self.assertEqual(parse_qs(target.query), {'success': ['true'], 'order_id': ['42']})

    @override_settings(FRONTEND_URL='https://ceit-ssc.ir/', WALLET_PAYMENT_RETURN_URL='')
    def test_existing_return_destination_is_preserved_when_disabled(self):
        self.assertEqual(_frontend_topup_redirect({'success': 'false'}),
                         'https://ceit-ssc.ir/wallet/top-up/callback?success=false')
