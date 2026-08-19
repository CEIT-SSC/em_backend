import threading
from decimal import Decimal

from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from shop.models import Order
from wallet.exceptions import InsufficientFunds
from wallet.models import Wallet, WalletEntry
from wallet.services import WalletService
from wallet.tests.helpers import credit, make_order, make_user


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
