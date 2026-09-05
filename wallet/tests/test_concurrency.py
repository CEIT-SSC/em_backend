import threading
from decimal import Decimal

from unittest import skipUnless
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from shop.models import Cart, CartItem, Order, Product
from wallet.exceptions import DuplicateIdempotencyKey, InsufficientFunds
from wallet.models import Wallet, WalletEntry, WalletTopUp
from wallet.services import WalletService
from wallet.tests.helpers import FakePaymentClient, credit, make_order, make_user, register_fake_zarinpal


@skipUnless(connection.vendor == 'postgresql', 'Concurrent debit locking requires PostgreSQL.')
class ConcurrentWalletTests(TransactionTestCase):
    def setUp(self):
        self.user = make_user('concurrent@example.com')
        self.staff = make_user('concurrent-staff@example.com', staff=True)
        credit(self.user, '100.00', 'concurrent-seed', actor=self.staff)

    def _run_threads(self, fns):
        results = [None] * len(fns)
        errors = [None] * len(fns)
        barrier = threading.Barrier(len(fns), timeout=10)

        def runner(index, fn):
            close_old_connections()
            try:
                barrier.wait()
                results[index] = fn()
            except Exception as exc:
                errors[index] = exc
            finally:
                connection.close()

        threads = [
            threading.Thread(target=runner, args=(i, fn))
            for i, fn in enumerate(fns)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        return results, errors

    def test_concurrent_debits_do_not_overdraw(self):
        orders = [make_order(self.user, '40.00') for _ in range(4)]

        def pay(order_id):
            order = Order.objects.get(pk=order_id)
            return WalletService.pay_order(self.user, order)

        results, errors = self._run_threads([
            (lambda pk=order.pk: pay(pk)) for order in orders
        ])

        successes = [item for item in results if item is not None]
        nsf = [exc for exc in errors if isinstance(exc, InsufficientFunds)]
        unexpected = [
            exc for exc in errors
            if exc is not None and not isinstance(exc, InsufficientFunds)
        ]
        self.assertFalse(unexpected)
        self.assertEqual(len(successes), 2)
        self.assertEqual(len(nsf), 2)

        wallet = Wallet.objects.get(user=self.user)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('20.00'))
        self.assertEqual(
            WalletEntry.objects.filter(wallet=wallet, entry_type=WalletEntry.TYPE_PURCHASE).count(),
            2,
        )
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)
        completed = Order.objects.filter(
            user=self.user,
            status=Order.STATUS_COMPLETED,
        ).count()
        pending = Order.objects.filter(
            user=self.user,
            status=Order.STATUS_PENDING_PAYMENT,
        ).count()
        self.assertEqual(completed, 2)
        self.assertEqual(pending, 2)

    def test_concurrent_duplicate_order_payment_settles_once(self):
        order = make_order(self.user, '30.00')

        def pay():
            return WalletService.pay_order(
                self.user,
                Order.objects.get(pk=order.pk),
                idempotency_key=f'concurrent-pay:{order.order_id}',
            )

        results, errors = self._run_threads([pay, pay, pay])
        unexpected = [exc for exc in errors if exc is not None]
        self.assertFalse(unexpected)

        created_flags = [created for _entry, created in results if _entry is not None]
        self.assertEqual(len(created_flags), 3)
        self.assertEqual(sum(1 for flag in created_flags if flag), 1)
        self.assertEqual(sum(1 for flag in created_flags if not flag), 2)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(
            WalletEntry.objects.filter(order=order, entry_type=WalletEntry.TYPE_PURCHASE).count(),
            1,
        )
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('70.00'))
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)

    def test_same_key_race_across_wallets_rejects_the_loser(self):
        other = make_user('concurrent-other@example.com')

        def adjust(user):
            return WalletService.admin_adjust(
                user=user,
                amount='25.00',
                direction=WalletEntry.DIRECTION_CREDIT,
                reason='Concurrent grant',
                actor=self.staff,
                idempotency_key='cross-wallet-race-key',
            )

        results, errors = self._run_threads([
            lambda: adjust(self.user),
            lambda: adjust(other),
        ])

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(sum(isinstance(exc, DuplicateIdempotencyKey) for exc in errors), 1)
        self.assertEqual(
            WalletEntry.objects.filter(idempotency_key='cross-wallet-race-key').count(),
            1,
        )
        balances = sorted([
            WalletService.get_balance(self.user),
            WalletService.get_balance(other),
        ])
        self.assertEqual(balances, [Decimal('0.00'), Decimal('25.00')])

    def test_concurrent_callbacks_credit_one_topup_once(self):
        client = FakePaymentClient()
        topup, _ = WalletService.start_topup(
            self.user,
            '35.00',
            callback_url='https://example.com/callback',
            payment_client=client,
        )

        def verify():
            return WalletService.credit_verified_topup(
                topup.gateway_authority,
                payment_client=client,
            )

        results, errors = self._run_threads([verify, verify])

        self.assertFalse([exc for exc in errors if exc is not None])
        created_flags = [created for _credited_topup, created in results]
        self.assertEqual(sum(created_flags), 1)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('135.00'))
        self.assertEqual(WalletEntry.objects.filter(topup=topup).count(), 1)

    def test_concurrent_paid_links_credit_both_and_debit_order_once(self):
        order = make_order(self.user, '50.00')
        client = FakePaymentClient()
        first = WalletService.start_topup(
            self.user,
            order.total_amount,
            callback_url='https://example.com/callback',
            order=order,
            payment_client=client,
        )[0]
        second = WalletService.start_topup(
            self.user,
            order.total_amount,
            callback_url='https://example.com/callback',
            order=order,
            payment_client=client,
        )[0]

        def verify_and_settle(authority):
            credited_topup, _ = WalletService.credit_verified_topup(
                authority,
                payment_client=client,
            )
            return WalletService.settle_order_for_credited_topup(credited_topup)

        results, errors = self._run_threads([
            lambda: verify_and_settle(first.gateway_authority),
            lambda: verify_and_settle(second.gateway_authority),
        ])

        self.assertFalse([exc for exc in errors if exc is not None])
        self.assertTrue(all(result is not None for result in results))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('150.00'))
        self.assertEqual(
            WalletEntry.objects.filter(entry_type=WalletEntry.TYPE_TOPUP, topup__order=order).count(),
            2,
        )
        self.assertEqual(
            WalletEntry.objects.filter(entry_type=WalletEntry.TYPE_PURCHASE, order=order).count(),
            1,
        )

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    def test_overlapping_checkout_and_old_callback_never_complete_both_orders(self):
        fake = FakePaymentClient()
        register_fake_zarinpal(self, fake)
        user = make_user('overlap-race@example.com')
        products = [
            Product.objects.create(
                name=name,
                description=name,
                price=price,
                image=f'products/{name.lower()}.png',
            )
            for name, price in [('Shared race', '40.00'), ('Old race', '10.00'), ('New race', '20.00')]
        ]
        cart = Cart.objects.create(user=user)
        content_type = ContentType.objects.get_for_model(Product)
        CartItem.objects.create(cart=cart, content_type=content_type, object_id=products[0].pk)
        old_only = CartItem.objects.create(
            cart=cart,
            content_type=content_type,
            object_id=products[1].pk,
        )
        client = APIClient()
        client.force_authenticate(user)
        first = client.post('/api/orders/checkout/', format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        old_order = Order.objects.get(order_id=first.data['order']['order_id'])
        old_topup = WalletTopUp.objects.get(public_id=first.data['topup_id'])

        old_only.delete()
        CartItem.objects.create(cart=cart, content_type=content_type, object_id=products[2].pk)

        def replacement_checkout():
            thread_client = APIClient()
            thread_client.force_authenticate(user)
            return thread_client.post('/api/orders/checkout/', format='json').status_code

        def old_callback():
            thread_client = APIClient()
            return thread_client.get('/api/wallet/top-ups/callback/', {
                'Authority': old_topup.gateway_authority,
                'Status': 'OK',
            }).status_code

        results, errors = self._run_threads([replacement_checkout, old_callback])
        self.assertFalse([exc for exc in errors if exc is not None])
        self.assertEqual(results[1], status.HTTP_302_FOUND)

        old_order.refresh_from_db()
        orders = list(Order.objects.filter(user=user).order_by('created_at'))
        if len(orders) == 1:
            self.assertEqual(old_order.status, Order.STATUS_COMPLETED)
            self.assertEqual(results[0], status.HTTP_400_BAD_REQUEST)
        else:
            self.assertEqual(len(orders), 2)
            self.assertEqual(old_order.status, Order.STATUS_CANCELLED)
            self.assertNotEqual(orders[1].status, Order.STATUS_COMPLETED)

        self.assertFalse(
            len(orders) > 1 and old_order.status == Order.STATUS_COMPLETED,
            'An older overlapping order completed after its replacement was created.',
        )
