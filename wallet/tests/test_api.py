from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from shop.models import Order
from wallet.models import Wallet, WalletTopUp
from wallet.services import WalletService
from wallet.tests.helpers import credit, make_order, make_user


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
        self.assertEqual(response.data['currency'], 'IRR')

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

    def test_start_topup_and_status(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            '/api/wallet/top-ups/',
            {'amount': '150.00', 'idempotency_key': 'api-topup-1'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], WalletTopUp.STATUS_PENDING)
        public_id = response.data['public_id']
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

        status_response = self.client.get(f'/api/wallet/top-ups/{public_id}/')
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data['status'], WalletTopUp.STATUS_PENDING)

        replay = self.client.post(
            '/api/wallet/top-ups/',
            {'amount': '150.00', 'idempotency_key': 'api-topup-1'},
            format='json',
        )
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(str(replay.data['public_id']), str(public_id))

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
