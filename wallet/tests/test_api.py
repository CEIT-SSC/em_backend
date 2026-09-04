from decimal import Decimal
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from shop.models import (
    Cart,
    CartItem,
    DiscountCode,
    DiscountRedemption,
    Order,
    Product,
)
from wallet.models import Wallet, WalletEntry, WalletTopUp
from wallet.services import WalletService
from wallet.tests.helpers import FakePaymentClient, credit, make_order, make_user, register_fake_zarinpal


class WalletAPITests(TestCase):
    def setUp(self):
        self.user = make_user('api-user@example.com')
        self.other = make_user('api-other@example.com')
        self.staff = make_user('api-staff@example.com', staff=True)
        self.client = APIClient()

    def test_balance_requires_auth(self):
        response = self.client.get('/api/wallet/')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_balance_endpoint(self):
        credit(self.user, '12.50', 'api-seed', actor=self.staff)
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/wallet/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(response.data['balance'])), Decimal('12.50'))
        self.assertEqual(response.data['currency'], 'IRT')

    def test_transaction_history_is_paginated_and_isolated(self):
        for i in range(21):
            credit(self.user, '1.00', f'hist-{i:02d}', actor=self.staff)
        credit(self.other, '9.00', 'other-hist', actor=self.staff)

        self.client.force_authenticate(self.user)
        first_page = self.client.get('/api/wallet/transactions/?page_size=20')
        self.assertEqual(first_page.status_code, status.HTTP_200_OK)
        self.assertEqual(first_page.data['count'], 21)
        self.assertEqual(len(first_page.data['results']), 20)
        self.assertIsNotNone(first_page.data['next'])

        second_page = self.client.get('/api/wallet/transactions/?page=2&page_size=20')
        self.assertEqual(len(second_page.data['results']), 1)
        keys = {row['idempotency_key'] for row in first_page.data['results'] + second_page.data['results']}
        self.assertNotIn('other-hist', keys)

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_start_topup_and_status(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        self.client.force_authenticate(self.user)
        response = self.client.post(
            '/api/wallet/top-ups/',
            {'amount': '150.00'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], WalletTopUp.STATUS_AWAITING_GATEWAY)
        self.assertEqual(response.data['payment_url'], fake.create_result['link'])
        public_id = response.data['public_id']
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

        status_response = self.client.get(f'/api/wallet/top-ups/{public_id}/')
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data['status'], WalletTopUp.STATUS_AWAITING_GATEWAY)

        another = self.client.post(
            '/api/wallet/top-ups/',
            {'amount': '150.00'},
            format='json',
        )
        self.assertEqual(another.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(str(another.data['public_id']), str(public_id))
        self.assertEqual(fake.create_calls, 2)

    def test_topup_callback_verifies_and_credits_once(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        topup, _ = WalletService.start_topup(
            self.user,
            '80.00',
            callback_url='https://example.com/callback',
            payment_client=fake,
        )
        first = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': topup.gateway_authority,
            'Status': 'OK',
        })
        second = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': topup.gateway_authority,
            'Status': 'OK',
        })

        self.assertEqual(first.status_code, status.HTTP_302_FOUND)
        self.assertEqual(second.status_code, status.HTTP_302_FOUND)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('80.00'))
        self.assertEqual(WalletEntry.objects.filter(topup=topup).count(), 1)
        self.assertEqual(fake.verify_calls, 1)

    def test_topup_callback_cannot_credit_an_unverified_payment(self):
        fake = FakePaymentClient(verify_result={
            'status': 'failed', 'ref_id': None, 'error': 'not paid', 'card_pan': None,
        })
        register_fake_zarinpal(self, fake)
        topup, _ = WalletService.start_topup(
            self.user,
            '80.00',
            callback_url='https://example.com/callback',
            payment_client=fake,
        )
        response = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': topup.gateway_authority,
            'Status': 'OK',
        })

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertFalse(WalletEntry.objects.filter(topup=topup).exists())

    def test_pay_order_endpoint(self):
        credit(self.user, '90.00', 'api-pay-seed', actor=self.staff)
        order = make_order(self.user, '40.00')
        self.client.force_authenticate(self.user)

        response = self.client.post('/api/wallet/pay/', {
            'order_id': str(order.order_id),
        }, format='json')
        replay = self.client.post('/api/wallet/pay/', {
            'order_id': str(order.order_id),
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['already_processed'])
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertTrue(replay.data['already_processed'])
        self.assertEqual(Decimal(str(replay.data['balance'])), Decimal('50.00'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)

    def test_pay_order_endpoint_rejects_another_users_order(self):
        credit(self.user, '50.00', 'api-foreign-order', actor=self.staff)
        order = make_order(self.other, '10.00')
        self.client.force_authenticate(self.user)

        response = self.client.post('/api/wallet/pay/', {
            'order_id': str(order.order_id),
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_pay_order_endpoint_returns_gateway_link_when_balance_is_short(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        order = make_order(self.user, '40.00')
        self.client.force_authenticate(self.user)

        response = self.client.post('/api/wallet/pay/', {
            'order_id': str(order.order_id),
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['payment_required'])
        self.assertEqual(response.data['payment_url'], fake.create_result['link'])
        self.assertIsNone(response.data['entry_id'])
        topup = WalletTopUp.objects.get(public_id=response.data['topup_id'])
        self.assertEqual(topup.order, order)
        self.assertEqual(topup.amount, Decimal('40.00'))
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

    def _add_product_to_cart(self, *, price='40.00'):
        product = Product.objects.create(
            name='Checkout product',
            description='Wallet checkout product',
            price=price,
            image='products/checkout.png',
        )
        cart, _ = Cart.objects.get_or_create(user=self.user)
        CartItem.objects.create(
            cart=cart,
            content_type=ContentType.objects.get_for_model(product),
            object_id=product.pk,
        )
        return cart

    def _add_existing_product_to_cart(self, cart, product):
        return CartItem.objects.create(
            cart=cart,
            content_type=ContentType.objects.get_for_model(product),
            object_id=product.pk,
        )

    def test_cart_checkout_completes_immediately_when_wallet_is_enough(self):
        cart = self._add_product_to_cart(price='40.00')
        credit(self.user, '50.00', 'checkout-wallet-seed', actor=self.staff)
        self.client.force_authenticate(self.user)

        response = self.client.post('/api/orders/checkout/', format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data['payment_required'])
        self.assertIsNone(response.data['payment_url'])
        self.assertEqual(response.data['order']['status'], Order.STATUS_COMPLETED)
        self.assertEqual(Decimal(str(response.data['wallet_balance'])), Decimal('10.00'))
        self.assertFalse(cart.items.exists())

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_cart_checkout_returns_link_then_callback_completes_order(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        cart = self._add_product_to_cart(price='40.00')
        self.client.force_authenticate(self.user)

        response = self.client.post('/api/orders/checkout/', format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['payment_required'])
        self.assertEqual(response.data['payment_url'], fake.create_result['link'])
        order = Order.objects.get(order_id=response.data['order']['order_id'])
        topup = WalletTopUp.objects.get(public_id=response.data['topup_id'])
        self.assertEqual(topup.order, order)
        self.assertEqual(topup.amount, order.total_amount)
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)

        self.client.force_authenticate(user=None)
        callback = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': topup.gateway_authority,
            'Status': 'OK',
        })

        self.assertEqual(callback.status_code, status.HTTP_302_FOUND)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertFalse(cart.items.exists())

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_retrying_unchanged_cart_reuses_order_and_creates_fresh_shortfall_link(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        self._add_product_to_cart(price='50.00')
        credit(self.user, '20.00', 'retry-cart-seed', actor=self.staff)
        self.client.force_authenticate(self.user)

        first = self.client.post('/api/orders/checkout/', format='json')
        second = self.client.post('/api/orders/checkout/', format='json')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data['order']['order_id'], second.data['order']['order_id'])
        self.assertNotEqual(first.data['topup_id'], second.data['topup_id'])
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)
        topups = WalletTopUp.objects.filter(order__order_id=first.data['order']['order_id'])
        self.assertEqual(topups.count(), 2)
        self.assertEqual({topup.amount for topup in topups}, {Decimal('30.00')})
        self.assertEqual(WalletService.get_balance(self.user), Decimal('20.00'))

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_one_failed_retry_link_does_not_block_another_successful_link(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        self._add_product_to_cart(price='50.00')
        credit(self.user, '20.00', 'retry-failure-seed', actor=self.staff)
        self.client.force_authenticate(self.user)
        first = self.client.post('/api/orders/checkout/', format='json')
        second = self.client.post('/api/orders/checkout/', format='json')
        first_topup = WalletTopUp.objects.get(public_id=first.data['topup_id'])
        second_topup = WalletTopUp.objects.get(public_id=second.data['topup_id'])

        fake.queue_verify_result({
            'status': 'failed', 'ref_id': None, 'error': 'Payment cancelled', 'card_pan': None,
        })
        self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': first_topup.gateway_authority,
            'Status': 'NOK',
        })
        self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': second_topup.gateway_authority,
            'Status': 'OK',
        })

        first_topup.refresh_from_db()
        second_topup.refresh_from_db()
        order = second_topup.order
        order.refresh_from_db()
        self.assertEqual(first_topup.status, WalletTopUp.STATUS_FAILED)
        self.assertEqual(second_topup.status, WalletTopUp.STATUS_CREDITED)
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_two_successful_retry_links_credit_twice_but_debit_shared_order_once(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        self._add_product_to_cart(price='50.00')
        credit(self.user, '20.00', 'retry-double-success-seed', actor=self.staff)
        self.client.force_authenticate(self.user)
        first = self.client.post('/api/orders/checkout/', format='json')
        second = self.client.post('/api/orders/checkout/', format='json')
        topups = [
            WalletTopUp.objects.get(public_id=first.data['topup_id']),
            WalletTopUp.objects.get(public_id=second.data['topup_id']),
        ]

        for topup in reversed(topups):
            self.client.get('/api/wallet/top-ups/callback/', {
                'Authority': topup.gateway_authority,
                'Status': 'OK',
            })

        order = topups[0].order
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('30.00'))
        self.assertEqual(
            WalletEntry.objects.filter(order=order, entry_type=WalletEntry.TYPE_PURCHASE).count(),
            1,
        )

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_new_overlapping_order_supersedes_old_order_even_if_old_link_verifies(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        products = [
            Product.objects.create(
                name=name,
                description=name,
                price=price,
                image=f'products/{name.lower()}.png',
            )
            for name, price in [('Shared', '40.00'), ('Old only', '10.00'), ('New only', '20.00')]
        ]
        cart, _ = Cart.objects.get_or_create(user=self.user)
        shared_item = self._add_existing_product_to_cart(cart, products[0])
        old_only_item = self._add_existing_product_to_cart(cart, products[1])
        self.client.force_authenticate(self.user)

        first = self.client.post('/api/orders/checkout/', format='json')
        old_order = Order.objects.get(order_id=first.data['order']['order_id'])
        old_topup = WalletTopUp.objects.get(public_id=first.data['topup_id'])

        old_only_item.delete()
        self._add_existing_product_to_cart(cart, products[2])
        second = self.client.post('/api/orders/checkout/', format='json')
        new_order = Order.objects.get(order_id=second.data['order']['order_id'])
        new_topup = WalletTopUp.objects.get(public_id=second.data['topup_id'])

        self.assertNotEqual(old_order.pk, new_order.pk)
        old_order.refresh_from_db()
        self.assertEqual(old_order.status, Order.STATUS_CANCELLED)
        self.assertEqual(
            set(old_order.items.values_list('object_id', flat=True)),
            {products[0].pk, products[1].pk},
        )
        self.assertEqual(
            set(new_order.items.values_list('object_id', flat=True)),
            {products[0].pk, products[2].pk},
        )

        self.client.force_authenticate(user=None)
        old_callback = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': old_topup.gateway_authority,
            'Status': 'OK',
        })

        self.assertEqual(old_callback.status_code, status.HTTP_302_FOUND)
        old_order.refresh_from_db()
        new_order.refresh_from_db()
        self.assertEqual(old_order.status, Order.STATUS_CANCELLED)
        self.assertEqual(new_order.status, Order.STATUS_PENDING_PAYMENT)
        self.assertFalse(WalletEntry.objects.filter(
            order=old_order,
            entry_type=WalletEntry.TYPE_PURCHASE,
        ).exists())
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))
        self.assertTrue(CartItem.objects.filter(pk=shared_item.pk).exists())

        new_callback = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': new_topup.gateway_authority,
            'Status': 'OK',
        })

        self.assertEqual(new_callback.status_code, status.HTTP_302_FOUND)
        old_order.refresh_from_db()
        new_order.refresh_from_db()
        self.assertEqual(old_order.status, Order.STATUS_CANCELLED)
        self.assertEqual(new_order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))
        self.assertEqual(
            WalletEntry.objects.filter(entry_type=WalletEntry.TYPE_PURCHASE).count(),
            1,
        )

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_discount_is_redeemed_only_after_successful_retry(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        cart = self._add_product_to_cart(price='50.00')
        discount = DiscountCode.objects.create(
            code='SAVE10',
            amount='10.00',
            max_uses=1,
            max_uses_per_user=1,
        )
        self.client.force_authenticate(self.user)
        applied = self.client.post(
            '/api/cart/apply-discount/',
            {'code': discount.code},
            format='json',
        )
        self.assertEqual(applied.status_code, status.HTTP_200_OK)

        first = self.client.post('/api/orders/checkout/', format='json')
        first_topup = WalletTopUp.objects.get(public_id=first.data['topup_id'])
        order = first_topup.order
        self.assertEqual(order.subtotal_amount, Decimal('50.00'))
        self.assertEqual(order.discount_amount, Decimal('10.00'))
        self.assertEqual(order.total_amount, Decimal('40.00'))
        self.assertEqual(first_topup.amount, Decimal('40.00'))

        self.client.force_authenticate(user=None)
        fake.queue_verify_result({
            'status': 'failed', 'ref_id': None, 'error': 'Payment cancelled', 'card_pan': None,
        })
        failed = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': first_topup.gateway_authority,
            'Status': 'NOK',
        })
        self.assertEqual(failed.status_code, status.HTTP_302_FOUND)
        discount.refresh_from_db()
        cart.refresh_from_db()
        self.assertEqual(discount.times_used, 0)
        self.assertFalse(DiscountRedemption.objects.filter(code=discount, user=self.user).exists())
        self.assertEqual(cart.applied_discount_code, discount)
        self.assertTrue(cart.items.exists())

        self.client.force_authenticate(self.user)
        second = self.client.post('/api/orders/checkout/', format='json')
        second_topup = WalletTopUp.objects.get(public_id=second.data['topup_id'])
        self.assertEqual(second.data['order']['order_id'], str(order.order_id))
        self.assertEqual(second_topup.amount, Decimal('40.00'))

        self.client.force_authenticate(user=None)
        succeeded = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': second_topup.gateway_authority,
            'Status': 'OK',
        })
        self.assertEqual(succeeded.status_code, status.HTTP_302_FOUND)
        order.refresh_from_db()
        discount.refresh_from_db()
        cart.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(discount.times_used, 1)
        self.assertEqual(
            DiscountRedemption.objects.filter(code=discount, user=self.user, order=order).count(),
            1,
        )
        self.assertIsNone(cart.applied_discount_code)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

        duplicate_callback = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': second_topup.gateway_authority,
            'Status': 'OK',
        })
        self.assertEqual(duplicate_callback.status_code, status.HTTP_302_FOUND)
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)

        replacement = Product.objects.create(
            name='Replacement',
            description='Replacement',
            price='50.00',
            image='products/replacement.png',
        )
        self._add_existing_product_to_cart(cart, replacement)
        self.client.force_authenticate(self.user)
        exhausted = self.client.post(
            '/api/cart/apply-discount/',
            {'code': discount.code},
            format='json',
        )
        self.assertEqual(exhausted.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_adjust_requires_staff(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            '/api/wallet/admin/adjustments/',
            {
                'user_id': self.user.pk,
                'amount': '10.00',
                'direction': 'credit',
                'reason': 'Should be forbidden',
                'idempotency_key': 'admin-forbidden',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

    def test_staff_can_adjust_and_refund(self):
        self.client.force_authenticate(self.staff)
        adjust = self.client.post(
            '/api/wallet/admin/adjustments/',
            {
                'user_id': self.user.pk,
                'amount': '70.00',
                'direction': 'credit',
                'reason': 'Support compensation',
                'idempotency_key': 'admin-comp-1',
            },
            format='json',
        )
        self.assertEqual(adjust.status_code, status.HTTP_200_OK)
        self.assertFalse(adjust.data['already_processed'])
        self.assertEqual(WalletService.get_balance(self.user), Decimal('70.00'))

        order = make_order(self.user, '20.00')
        WalletService.pay_order(self.user, order)

        refund = self.client.post(
            '/api/wallet/admin/refunds/',
            {
                'order_id': str(order.order_id),
                'reason': 'Event cancelled by organizer',
                'idempotency_key': 'admin-refund-1',
            },
            format='json',
        )
        self.assertEqual(refund.status_code, status.HTTP_200_OK)
        replay = self.client.post(
            '/api/wallet/admin/refunds/',
            {
                'order_id': str(order.order_id),
                'reason': 'Event cancelled by organizer',
                'idempotency_key': 'admin-refund-1',
            },
            format='json',
        )
        self.assertTrue(replay.data['already_processed'])
        self.assertEqual(WalletService.get_balance(self.user), Decimal('70.00'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_REFUNDED)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)
