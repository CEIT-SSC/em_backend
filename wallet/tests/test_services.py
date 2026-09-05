from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from shop.models import Order, OrderItem, Product
from wallet.exceptions import (
    AdjustmentReasonRequired,
    DuplicateIdempotencyKey,
    ImmutableLedgerError,
    InsufficientFunds,
    InvalidAmount,
    OrderNotPayable,
    RefundNotAllowed,
    TopUpGatewayError,
)
from wallet.models import Wallet, WalletEntry, WalletTopUp
from wallet.payments import ZarrinPal
from wallet.services import WalletService
from wallet.tests.helpers import FakePaymentClient, credit, make_order, make_user


class WalletServiceTests(TestCase):
    def setUp(self):
        self.user = make_user('wallet-user@example.com')
        self.staff = make_user('wallet-staff@example.com', staff=True)

    def test_get_balance_creates_wallet(self):
        self.assertFalse(Wallet.objects.filter(user=self.user).exists())
        balance = WalletService.get_balance(self.user)
        self.assertEqual(balance, Decimal('0.00'))
        self.assertTrue(Wallet.objects.filter(user=self.user).exists())

    def test_gateway_converts_decimal_toman_amount_without_truncating_first(self):
        self.assertEqual(ZarrinPal._gateway_amount(Decimal('12.50')), 125)

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
        client = FakePaymentClient()
        topup, created = WalletService.start_topup(
            self.user,
            '100.00',
            callback_url='https://example.com/api/wallet/top-ups/callback/',
            payment_client=client,
        )
        self.assertTrue(created)
        self.assertEqual(topup.status, WalletTopUp.STATUS_AWAITING_GATEWAY)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertEqual(WalletEntry.objects.filter(entry_type=WalletEntry.TYPE_TOPUP).count(), 0)

        credited, first_credit = WalletService.credit_verified_topup(
            topup.gateway_authority,
            payment_client=client,
        )
        self.assertTrue(first_credit)
        self.assertEqual(credited.status, WalletTopUp.STATUS_CREDITED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('100.00'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)

    def test_duplicate_topup_credit_does_not_credit_twice(self):
        client = FakePaymentClient()
        topup, _ = WalletService.start_topup(
            self.user,
            '40.00',
            callback_url='https://example.com/callback',
            payment_client=client,
        )
        WalletService.credit_verified_topup(topup.gateway_authority, payment_client=client)
        WalletService.credit_verified_topup(topup.gateway_authority, payment_client=client)
        self.assertEqual(client.verify_calls, 1)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('40.00'))
        self.assertEqual(WalletEntry.objects.filter(topup=topup).count(), 1)

    def test_unverified_topup_is_not_credited(self):
        client = FakePaymentClient(verify_result={
            'status': 'failed', 'ref_id': None, 'error': 'not paid', 'card_pan': None,
        })
        topup, _ = WalletService.start_topup(
            self.user,
            '40.00',
            callback_url='https://example.com/callback',
            payment_client=client,
        )
        result, created = WalletService.credit_verified_topup(
            topup.gateway_authority,
            payment_client=client,
        )
        self.assertFalse(created)
        self.assertEqual(result.status, WalletTopUp.STATUS_FAILED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertEqual(WalletEntry.objects.count(), 0)

    def test_each_topup_request_creates_a_fresh_payment_link(self):
        client = FakePaymentClient()
        first, created_first = WalletService.start_topup(
            self.user, '20.00',
            callback_url='https://example.com/callback', payment_client=client,
        )
        second, created_second = WalletService.start_topup(
            self.user, '20.00',
            callback_url='https://example.com/callback', payment_client=client,
        )
        self.assertTrue(created_first)
        self.assertTrue(created_second)
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.gateway_authority, second.gateway_authority)
        self.assertEqual(first.status, WalletTopUp.STATUS_AWAITING_GATEWAY)
        self.assertEqual(client.create_calls, 2)

    def test_gateway_request_failure_never_credits_wallet(self):
        client = FakePaymentClient(create_result={
            'status': 'failed',
            'authority': None,
            'link': None,
            'error': 'gateway unavailable',
        })

        with self.assertRaises(TopUpGatewayError):
            WalletService.start_topup(
                self.user,
                '20.00',
                callback_url='https://example.com/callback',
                payment_client=client,
            )

        topup = WalletTopUp.objects.get(wallet__user=self.user)
        self.assertEqual(topup.status, WalletTopUp.STATUS_FAILED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        self.assertFalse(WalletEntry.objects.filter(topup=topup).exists())

    def test_order_payment_uses_wallet_without_gateway_when_balance_is_enough(self):
        credit(self.user, '80.00', 'seed-wallet-checkout', actor=self.staff)
        order = make_order(self.user, '50.00')
        client = FakePaymentClient()

        result = WalletService.pay_or_start_order_payment(
            self.user,
            order,
            callback_url='https://example.com/callback',
            payment_client=client,
        )

        self.assertFalse(result['payment_required'])
        self.assertIsNone(result['payment_url'])
        self.assertEqual(client.create_calls, 0)
        self.assertEqual(result['balance'], Decimal('30.00'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)

    def test_order_payment_topup_only_covers_wallet_shortfall(self):
        credit(self.user, '20.00', 'seed-gateway-checkout', actor=self.staff)
        order = make_order(self.user, '50.00')
        client = FakePaymentClient()

        result = WalletService.pay_or_start_order_payment(
            self.user,
            order,
            callback_url='https://example.com/callback',
            payment_client=client,
        )

        self.assertTrue(result['payment_required'])
        self.assertEqual(result['topup'].amount, Decimal('30.00'))
        self.assertEqual(client.last_create['amount'], Decimal('30.00'))
        self.assertEqual(result['topup'].order, order)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('20.00'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)

        topup, credited = WalletService.credit_verified_topup(
            result['topup'].gateway_authority,
            payment_client=client,
        )
        self.assertTrue(credited)
        settlement = WalletService.settle_order_for_credited_topup(topup)
        self.assertIsNotNone(settlement)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)

    def test_retry_recalculates_shortfall_from_current_wallet_balance(self):
        credit(self.user, '20.00', 'shortfall-retry-seed', actor=self.staff)
        order = make_order(self.user, '50.00')
        client = FakePaymentClient()
        first = WalletService.pay_or_start_order_payment(
            self.user,
            order,
            callback_url='https://example.com/callback',
            payment_client=client,
        )['topup']
        credit(self.user, '10.00', 'shortfall-retry-extra', actor=self.staff)

        second = WalletService.pay_or_start_order_payment(
            self.user,
            order,
            callback_url='https://example.com/callback',
            payment_client=client,
        )['topup']

        self.assertEqual(first.amount, Decimal('30.00'))
        self.assertEqual(second.amount, Decimal('20.00'))
        self.assertEqual(WalletService.get_balance(self.user), Decimal('30.00'))

    def test_paying_multiple_links_credits_each_but_debits_order_once(self):
        order = make_order(self.user, '50.00')
        client = FakePaymentClient()
        first = WalletService.pay_or_start_order_payment(
            self.user, order,
            callback_url='https://example.com/callback', payment_client=client,
        )['topup']
        second = WalletService.pay_or_start_order_payment(
            self.user, order,
            callback_url='https://example.com/callback', payment_client=client,
        )['topup']

        for topup in (first, second):
            credited, _ = WalletService.credit_verified_topup(
                topup.gateway_authority,
                payment_client=client,
            )
            WalletService.settle_order_for_credited_topup(credited)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))
        self.assertEqual(
            WalletEntry.objects.filter(order=order, entry_type=WalletEntry.TYPE_PURCHASE).count(),
            1,
        )

    def test_verified_payment_stays_as_wallet_credit_if_order_becomes_unavailable(self):
        product = Product.objects.create(
            name='Later disabled ticket',
            description='Available when the link was created',
            price='50.00',
            image='products/later-disabled.png',
        )
        order = make_order(self.user, '50.00')
        OrderItem.objects.create(
            order=order,
            content_type=ContentType.objects.get_for_model(product),
            object_id=product.pk,
            description=product.name,
            price='50.00',
        )
        client = FakePaymentClient()
        topup = WalletService.pay_or_start_order_payment(
            self.user,
            order,
            callback_url='https://example.com/callback',
            payment_client=client,
        )['topup']
        product.is_active = False
        product.save(update_fields=['is_active'])

        credited_topup, _ = WalletService.credit_verified_topup(
            topup.gateway_authority,
            payment_client=client,
        )
        settlement = WalletService.settle_order_for_credited_topup(credited_topup)

        self.assertIsNone(settlement)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)
        self.assertFalse(
            WalletEntry.objects.filter(order=order, entry_type=WalletEntry.TYPE_PURCHASE).exists()
        )

    def test_pay_order_debits_and_completes_once(self):
        credit(self.user, '80.00', 'seed-pay', actor=self.staff)
        order = make_order(self.user, '50.00')
        entry, created = WalletService.pay_order(self.user, order)
        self.assertTrue(created)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertIsNotNone(order.paid_at)
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

    def test_order_key_cannot_be_reused_for_a_different_same_price_order(self):
        credit(self.user, '100.00', 'seed-order-key-conflict', actor=self.staff)
        first_order = make_order(self.user, '40.00')
        second_order = make_order(self.user, '40.00')
        WalletService.pay_order(self.user, first_order, idempotency_key='shared-order-key')
        with self.assertRaises(DuplicateIdempotencyKey):
            WalletService.pay_order(self.user, second_order, idempotency_key='shared-order-key')
        self.assertEqual(WalletService.get_balance(self.user), Decimal('60.00'))
        second_order.refresh_from_db()
        self.assertEqual(second_order.status, Order.STATUS_PENDING_PAYMENT)

    def test_inactive_order_item_is_rejected_before_debit(self):
        credit(self.user, '100.00', 'seed-inactive-item', actor=self.staff)
        product = Product.objects.create(
            name='Inactive ticket', description='Unavailable', price='40.00',
            image='products/inactive.png', is_active=False,
        )
        order = make_order(self.user, '40.00')
        OrderItem.objects.create(
            order=order,
            content_type=ContentType.objects.get_for_model(product),
            object_id=product.pk,
            description=product.name,
            price='40.00',
        )
        with self.assertRaises(OrderNotPayable):
            WalletService.pay_order(self.user, order)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('100.00'))
        self.assertFalse(WalletEntry.objects.filter(order=order).exists())

    def test_sold_out_order_item_is_rejected_before_debit(self):
        buyer = make_user('capacity-buyer@example.com')
        credit(self.user, '100.00', 'seed-sold-out-item', actor=self.staff)
        product = Product.objects.create(
            name='Limited ticket', description='One only', price='40.00',
            image='products/limited.png', capacity=1,
        )
        completed = make_order(buyer, '40.00', status=Order.STATUS_COMPLETED)
        target = make_order(self.user, '40.00')
        content_type = ContentType.objects.get_for_model(product)
        for order in (completed, target):
            OrderItem.objects.create(
                order=order,
                content_type=content_type,
                object_id=product.pk,
                description=product.name,
                price='40.00',
            )
        with self.assertRaises(OrderNotPayable):
            WalletService.pay_order(self.user, target)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('100.00'))

    def test_already_owned_order_item_is_rejected_before_debit(self):
        credit(self.user, '100.00', 'seed-owned-item', actor=self.staff)
        product = Product.objects.create(
            name='Owned ticket', description='Already bought', price='40.00',
            image='products/owned.png',
        )
        completed = make_order(self.user, '40.00', status=Order.STATUS_COMPLETED)
        target = make_order(self.user, '40.00')
        content_type = ContentType.objects.get_for_model(product)
        for order in (completed, target):
            OrderItem.objects.create(
                order=order,
                content_type=content_type,
                object_id=product.pk,
                description=product.name,
                price='40.00',
            )
        with self.assertRaises(OrderNotPayable):
            WalletService.pay_order(self.user, target)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('100.00'))

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
        client = FakePaymentClient()
        topup, _ = WalletService.start_topup(
            self.user, '200.00',
            callback_url='https://example.com/callback', payment_client=client,
        )
        WalletService.credit_verified_topup(topup.gateway_authority, payment_client=client)
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

    def test_deleting_user_preserves_wallet_topups_and_ledger(self):
        client = FakePaymentClient()
        topup, _ = WalletService.start_topup(
            self.user, '25.00',
            callback_url='https://example.com/callback', payment_client=client,
        )
        WalletService.credit_verified_topup(topup.gateway_authority, payment_client=client)
        wallet = Wallet.objects.get(user=self.user)
        wallet_id = wallet.pk
        topup_id = topup.pk
        entry_id = WalletEntry.objects.get(topup=topup).pk

        self.user.delete()

        wallet = Wallet.objects.get(pk=wallet_id)
        self.assertIsNone(wallet.user_id)
        self.assertTrue(WalletTopUp.objects.filter(pk=topup_id, wallet=wallet).exists())
        self.assertTrue(WalletEntry.objects.filter(pk=entry_id, wallet=wallet).exists())
        self.assertEqual(WalletService.ledger_sum(wallet), wallet.balance)
