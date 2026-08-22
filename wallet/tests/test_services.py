from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.test import TestCase

from shop.models import Order
from wallet.exceptions import (
    AdjustmentReasonRequired,
    DuplicateIdempotencyKey,
    ImmutableLedgerError,
    InsufficientFunds,
    InvalidAmount,
    OrderNotPayable,
    RefundNotAllowed,
    WalletError,
)
from wallet.models import Wallet, WalletEntry, WalletTopUp
from wallet.services import WalletService
from wallet.tests.helpers import credit, make_order, make_user


class WalletServiceTests(TestCase):
    def setUp(self):
        self.user = make_user('wallet-user@example.com')
        self.staff = make_user('wallet-staff@example.com', staff=True)

    def test_get_balance_creates_wallet(self):
        self.assertFalse(Wallet.objects.filter(user=self.user).exists())
        balance = WalletService.get_balance(self.user)
        self.assertEqual(balance, Decimal('0.00'))
        self.assertTrue(Wallet.objects.filter(user=self.user).exists())

    def test_direct_balance_write_is_rejected(self):
        wallet = WalletService.get_or_create_wallet(self.user)
        wallet.balance = Decimal('50.00')
        with self.assertRaises(ImmutableLedgerError):
            wallet.save()
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('0.00'))

    def test_queryset_cannot_update_balance(self):
        wallet = WalletService.get_or_create_wallet(self.user)
        with self.assertRaises(ImmutableLedgerError):
            Wallet.objects.filter(pk=wallet.pk).update(balance=Decimal('99.00'))

    def test_ledger_entries_are_immutable(self):
        credit(self.user, '25.00', 'seed-immutable', actor=self.staff)
        entry = WalletEntry.objects.get(idempotency_key='seed-immutable')
        entry.amount = Decimal('1.00')
        with self.assertRaises(ImmutableLedgerError):
            entry.save()
        with self.assertRaises(ImmutableLedgerError):
            WalletEntry.objects.filter(pk=entry.pk).update(amount=Decimal('1.00'))
        with self.assertRaises(ImmutableLedgerError):
            entry.delete()
        with self.assertRaises(ImmutableLedgerError):
            WalletEntry.objects.filter(pk=entry.pk).delete()
        self.assertEqual(WalletEntry.objects.filter(pk=entry.pk).count(), 1)

    def test_admin_adjust_requires_reason_and_is_logged(self):
        with self.assertRaises(AdjustmentReasonRequired):
            WalletService.admin_adjust(
                user=self.user,
                amount='10.00',
                direction=WalletEntry.DIRECTION_CREDIT,
                reason='  ',
                actor=self.staff,
                idempotency_key='adj-no-reason',
            )
        entry, created = WalletService.admin_adjust(
            user=self.user,
            amount='10.00',
            direction=WalletEntry.DIRECTION_CREDIT,
            reason='Promo correction',
            actor=self.staff,
            idempotency_key='adj-logged',
        )
        self.assertTrue(created)
        self.assertEqual(entry.reason, 'Promo correction')
        self.assertEqual(entry.created_by, self.staff)
        self.assertTrue(LogEntry.objects.filter(change_message__contains='Promo correction').exists())
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('10.00'))
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)

    def test_admin_adjust_is_idempotent(self):
        first, created_first = WalletService.admin_adjust(
            user=self.user,
            amount='7.50',
            direction=WalletEntry.DIRECTION_CREDIT,
            reason='Idempotent adjust',
            actor=self.staff,
            idempotency_key='adj-same',
        )
        second, created_second = WalletService.admin_adjust(
            user=self.user,
            amount='7.50',
            direction=WalletEntry.DIRECTION_CREDIT,
            reason='Idempotent adjust',
            actor=self.staff,
            idempotency_key='adj-same',
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('7.50'))
        self.assertEqual(WalletEntry.objects.filter(wallet__user=self.user).count(), 1)

    def test_reused_idempotency_key_for_different_amount_is_rejected(self):
        WalletService.admin_adjust(
            user=self.user,
            amount='5.00',
            direction=WalletEntry.DIRECTION_CREDIT,
            reason='First',
            actor=self.staff,
            idempotency_key='adj-conflict',
        )
        with self.assertRaises(DuplicateIdempotencyKey):
            WalletService.admin_adjust(
                user=self.user,
                amount='8.00',
                direction=WalletEntry.DIRECTION_CREDIT,
                reason='Second',
                actor=self.staff,
                idempotency_key='adj-conflict',
            )

    def test_topup_is_credited_only_after_verified_payment(self):
        topup, created = WalletService.start_topup(self.user, '100.00', 'topup-key-1')
        self.assertTrue(created)
        self.assertEqual(topup.status, WalletTopUp.STATUS_PENDING)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertEqual(WalletEntry.objects.filter(entry_type=WalletEntry.TYPE_TOPUP).count(), 0)

        credited, first_credit = WalletService.credit_verified_topup(
            topup,
            gateway_authority='AUTH-100',
            gateway_ref_id='REF-100',
        )
        self.assertTrue(first_credit)
        self.assertEqual(credited.status, WalletTopUp.STATUS_CREDITED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('100.00'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)

    def test_duplicate_topup_credit_does_not_credit_twice(self):
        topup, _ = WalletService.start_topup(self.user, '40.00', 'topup-dup')
        WalletService.credit_verified_topup(topup, gateway_authority='AUTH-40', gateway_ref_id='REF-40')
        WalletService.credit_verified_topup(topup, gateway_authority='AUTH-40', gateway_ref_id='REF-40')
        self.assertEqual(WalletService.get_balance(self.user), Decimal('40.00'))
        self.assertEqual(WalletEntry.objects.filter(topup=topup).count(), 1)

    def test_failed_topup_is_not_credited(self):
        topup, _ = WalletService.start_topup(self.user, '40.00', 'topup-fail')
        WalletService.mark_topup_failed(topup, metadata={'note': 'payment_failed'})
        with self.assertRaises(WalletError):
            WalletService.credit_verified_topup(topup, gateway_authority='AUTH-FAIL')
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertEqual(WalletEntry.objects.count(), 0)

    def test_start_topup_is_idempotent(self):
        first, created_first = WalletService.start_topup(self.user, '20.00', 'topup-repeat')
        second, created_second = WalletService.start_topup(self.user, '20.00', 'topup-repeat')
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, WalletTopUp.STATUS_PENDING)

    def test_pay_order_debits_and_completes_once(self):
        credit(self.user, '80.00', 'seed-pay', actor=self.staff)
        order = make_order(self.user, '50.00')
        entry, created = WalletService.pay_order(self.user, order)
        self.assertTrue(created)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertIsNotNone(order.paid_at)
        self.assertTrue(order.payment_gateway_txn_id.startswith('wallet:'))
        self.assertEqual(WalletService.get_balance(self.user), Decimal('30.00'))

        again, created_again = WalletService.pay_order(self.user, order)
        self.assertFalse(created_again)
        self.assertEqual(entry.pk, again.pk)
        self.assertEqual(WalletEntry.objects.filter(order=order, entry_type=WalletEntry.TYPE_PURCHASE).count(), 1)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('30.00'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)

    def test_duplicate_order_request_with_new_key_does_not_debit_twice(self):
        credit(self.user, '100.00', 'seed-dup-order', actor=self.staff)
        order = make_order(self.user, '40.00')
        first, _ = WalletService.pay_order(self.user, order, idempotency_key='pay-order-a')
        second, created = WalletService.pay_order(self.user, order, idempotency_key='pay-order-b')
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('60.00'))

    def test_insufficient_funds_changes_nothing(self):
        credit(self.user, '10.00', 'seed-nsf', actor=self.staff)
        order = make_order(self.user, '25.00')
        with self.assertRaises(InsufficientFunds):
            WalletService.pay_order(self.user, order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)
        self.assertIsNone(order.paid_at)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('10.00'))
        self.assertFalse(
            WalletEntry.objects.filter(order=order, entry_type=WalletEntry.TYPE_PURCHASE).exists()
        )
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)

    def test_invalid_amount_is_rejected(self):
        with self.assertRaises(InvalidAmount):
            WalletService.admin_adjust(
                user=self.user,
                amount='0',
                direction=WalletEntry.DIRECTION_CREDIT,
                reason='zero',
                actor=self.staff,
                idempotency_key='zero-amt',
            )
        with self.assertRaises(InvalidAmount):
            WalletService.admin_adjust(
                user=self.user,
                amount='-5',
                direction=WalletEntry.DIRECTION_CREDIT,
                reason='negative',
                actor=self.staff,
                idempotency_key='neg-amt',
            )

    def test_non_payable_order_is_rejected(self):
        credit(self.user, '50.00', 'seed-not-payable', actor=self.staff)
        order = make_order(self.user, '10.00', status=Order.STATUS_CANCELLED)
        with self.assertRaises(OrderNotPayable):
            WalletService.pay_order(self.user, order)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))

    def test_refund_is_idempotent_and_auditable(self):
        credit(self.user, '60.00', 'seed-refund', actor=self.staff)
        order = make_order(self.user, '15.00')
        purchase, _ = WalletService.pay_order(self.user, order)
        refund, created = WalletService.refund_debit(
            actor=self.staff,
            reason='Customer cancelled workshop',
            order=order,
        )
        self.assertTrue(created)
        self.assertEqual(refund.entry_type, WalletEntry.TYPE_REFUND)
        self.assertEqual(refund.related_entry_id, purchase.pk)
        self.assertEqual(refund.reason, 'Customer cancelled workshop')
        self.assertEqual(refund.created_by, self.staff)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_REFUNDED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('60.00'))

        again, created_again = WalletService.refund_debit(
            actor=self.staff,
            reason='Customer cancelled workshop',
            order=order,
        )
        self.assertFalse(created_again)
        self.assertEqual(again.pk, refund.pk)
        self.assertEqual(WalletEntry.objects.filter(related_entry=purchase).count(), 1)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('60.00'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)
        self.assertTrue(LogEntry.objects.filter(change_message__contains='Customer cancelled workshop').exists())

    def test_refund_without_purchase_is_rejected(self):
        order = make_order(self.user, '15.00')
        with self.assertRaises(RefundNotAllowed):
            WalletService.refund_debit(actor=self.staff, reason='Nothing to reverse', order=order)

    def test_ledger_sum_matches_cached_balance_after_mixed_operations(self):
        topup, _ = WalletService.start_topup(self.user, '200.00', 'mix-topup')
        WalletService.credit_verified_topup(topup, gateway_authority='AUTH-200', gateway_ref_id='REF-200')
        order = make_order(self.user, '75.00')
        WalletService.pay_order(self.user, order)
        WalletService.admin_adjust(
            user=self.user,
            amount='5.00',
            direction=WalletEntry.DIRECTION_DEBIT,
            reason='Manual correction',
            actor=self.staff,
            idempotency_key='mix-adjust',
        )
        WalletService.refund_debit(actor=self.staff, reason='Partial event cancel', order=order)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('200.00') - Decimal('5.00'))
        WalletService.assert_consistent(wallet)
