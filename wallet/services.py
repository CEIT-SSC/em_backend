import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Sum, When
from django.utils import timezone

from shop.fulfillment import process_successful_order
from shop.models import Cart, CartItem, Order
from shop.payments import ZarrinPal

from wallet.exceptions import (
    AdjustmentReasonRequired,
    DuplicateIdempotencyKey,
    InsufficientFunds,
    InvalidAmount,
    OrderNotPayable,
    RefundNotAllowed,
    TopUpGatewayError,
    TopUpNotFound,
    WalletError,
)
from wallet.models import Wallet, WalletEntry, WalletTopUp

logger = logging.getLogger(__name__)

TWOPLACES = Decimal('0.01')
SAFE_METADATA_KEYS = frozenset({
    'source', 'note', 'gateway', 'authority', 'ref_id', 'app', 'card_pan',
})
MAX_METADATA_VALUE_LENGTH = 200
PAYABLE_ORDER_STATUSES = {
    Order.STATUS_PENDING_PAYMENT,
    Order.STATUS_PAYMENT_FAILED,
}


def _as_money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidAmount() from exc
    if not amount.is_finite():
        raise InvalidAmount()
    quantized = amount.quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)
    if quantized <= 0:
        raise InvalidAmount()
    return quantized


def sanitize_metadata(metadata):
    if not metadata:
        return {}
    if not isinstance(metadata, dict):
        return {}
    clean = {}
    for key, value in metadata.items():
        if key not in SAFE_METADATA_KEYS:
            continue
        if value is None:
            continue
        clean[str(key)] = str(value)[:MAX_METADATA_VALUE_LENGTH]
    return clean


class WalletService:
    """Single write path for wallet balances and immutable ledger entries."""

    @staticmethod
    def get_or_create_wallet(user):
        try:
            wallet, _created = Wallet.objects.get_or_create(user=user)
        except IntegrityError:
            wallet = Wallet.objects.get(user=user)
        return wallet

    @classmethod
    def get_balance(cls, user):
        return cls.get_or_create_wallet(user).balance

    @staticmethod
    def ledger_sum(wallet):
        total = wallet.entries.aggregate(
            total=Sum(
                Case(
                    When(direction=WalletEntry.DIRECTION_CREDIT, then=F('amount')),
                    When(direction=WalletEntry.DIRECTION_DEBIT, then=-F('amount')),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
        )['total']
        return (total or Decimal('0.00')).quantize(TWOPLACES)

    @classmethod
    def assert_consistent(cls, wallet):
        wallet.refresh_from_db(fields=['balance'])
        summed = cls.ledger_sum(wallet)
        if summed != wallet.balance:
            raise WalletError(
                f"Wallet {wallet.pk} cache/ledger mismatch: cached={wallet.balance} ledger={summed}"
            )
        return wallet.balance

    @classmethod
    def _locked_wallet(cls, user):
        wallet = cls.get_or_create_wallet(user)
        return Wallet.objects.select_for_update().get(pk=wallet.pk)

    @classmethod
    def _log_admin_action(cls, *, actor, wallet, message):
        if actor is None or not getattr(actor, 'pk', None):
            return
        LogEntry.objects.create(
            user_id=actor.pk,
            content_type_id=ContentType.objects.get_for_model(Wallet).pk,
            object_id=str(wallet.pk),
            object_repr=str(wallet)[:200],
            action_flag=CHANGE,
            change_message=message,
        )

    @classmethod
    def _find_existing_entry(cls, *, idempotency_key, order=None, topup=None, related_entry=None):
        existing = WalletEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
        if order is not None:
            existing = WalletEntry.objects.filter(
                order=order,
                entry_type=WalletEntry.TYPE_PURCHASE,
                direction=WalletEntry.DIRECTION_DEBIT,
            ).first()
            if existing:
                return existing
        if topup is not None:
            existing = WalletEntry.objects.filter(
                topup=topup,
                entry_type=WalletEntry.TYPE_TOPUP,
            ).first()
            if existing:
                return existing
        if related_entry is not None:
            existing = WalletEntry.objects.filter(
                related_entry=related_entry,
                entry_type=WalletEntry.TYPE_REFUND,
            ).first()
            if existing:
                return existing
        return None

    @classmethod
    def _apply_entry(
        cls,
        *,
        wallet,
        entry_type,
        direction,
        amount,
        idempotency_key,
        order=None,
        topup=None,
        related_entry=None,
        reason='',
        metadata=None,
        created_by=None,
    ):
        amount = _as_money(amount)
        if not idempotency_key:
            raise WalletError("Idempotency key is required.")
        if direction not in (WalletEntry.DIRECTION_CREDIT, WalletEntry.DIRECTION_DEBIT):
            raise WalletError("Invalid ledger direction.")

        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        existing = cls._find_existing_entry(
            idempotency_key=idempotency_key,
            order=order if entry_type == WalletEntry.TYPE_PURCHASE else None,
            topup=topup if entry_type == WalletEntry.TYPE_TOPUP else None,
            related_entry=related_entry if entry_type == WalletEntry.TYPE_REFUND else None,
        )
        if existing:
            if existing.wallet_id != wallet.pk:
                raise DuplicateIdempotencyKey()
            if existing.idempotency_key == idempotency_key:
                if (
                    existing.entry_type != entry_type
                    or existing.direction != direction
                    or existing.amount != amount
                ):
                    raise DuplicateIdempotencyKey()
                return existing, False
            return existing, False

        signed = amount if direction == WalletEntry.DIRECTION_CREDIT else -amount
        new_balance = (wallet.balance + signed).quantize(TWOPLACES)
        if new_balance < 0:
            raise InsufficientFunds(available=wallet.balance, required=amount)

        try:
            with transaction.atomic():
                entry = WalletEntry(
                    wallet=wallet,
                    entry_type=entry_type,
                    direction=direction,
                    amount=amount,
                    balance_after=new_balance,
                    idempotency_key=idempotency_key,
                    order=order,
                    topup=topup,
                    related_entry=related_entry,
                    reason=reason or '',
                    metadata=sanitize_metadata(metadata),
                    created_by=created_by,
                )
                entry.full_clean(exclude=['wallet', 'order', 'topup', 'related_entry', 'created_by'])
                entry.save()
                wallet.balance = new_balance
                wallet.save(allow_balance_write=True, update_fields=['balance', 'updated_at'])
        except IntegrityError:
            existing = cls._find_existing_entry(
                idempotency_key=idempotency_key,
                order=order if entry_type == WalletEntry.TYPE_PURCHASE else None,
                topup=topup if entry_type == WalletEntry.TYPE_TOPUP else None,
                related_entry=related_entry if entry_type == WalletEntry.TYPE_REFUND else None,
            )
            if existing:
                return existing, False
            raise

        logger.info(
            "wallet.entry created type=%s direction=%s amount=%s wallet=%s entry=%s key=%s",
            entry_type, direction, amount, wallet.pk, entry.pk, idempotency_key,
        )
        return entry, True

    @classmethod
    def start_topup(
        cls,
        user,
        amount,
        idempotency_key,
        *,
        callback_url,
        metadata=None,
        payment_client=None,
    ):
        amount = _as_money(amount)
        if not idempotency_key:
            raise WalletError("Idempotency key is required.")
        if not callback_url:
            raise TopUpGatewayError("Wallet payment callback URL is not configured.")

        with transaction.atomic():
            wallet = cls._locked_wallet(user)
            topup = WalletTopUp.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
            if topup:
                if topup.wallet_id != wallet.pk:
                    raise DuplicateIdempotencyKey()
                if topup.status == WalletTopUp.STATUS_CREDITED:
                    return topup, False
                if topup.status == WalletTopUp.STATUS_AWAITING_GATEWAY and topup.payment_url:
                    return topup, False
            else:
                try:
                    with transaction.atomic():
                        topup = WalletTopUp.objects.create(
                            wallet=wallet,
                            amount=amount,
                            status=WalletTopUp.STATUS_PENDING,
                            idempotency_key=idempotency_key,
                            metadata=sanitize_metadata(metadata),
                        )
                except IntegrityError:
                    topup = WalletTopUp.objects.select_for_update().get(idempotency_key=idempotency_key)
                    if topup.wallet_id != wallet.pk:
                        raise DuplicateIdempotencyKey()
                    if topup.status == WalletTopUp.STATUS_CREDITED:
                        return topup, False
                    if topup.status == WalletTopUp.STATUS_AWAITING_GATEWAY and topup.payment_url:
                        return topup, False

        client = payment_client or ZarrinPal()
        result = client.create_payment(
            amount=float(amount),
            mobile=getattr(user, 'phone_number', None) or '',
            email=getattr(user, 'email', None) or '',
            order_id=str(topup.public_id),
            callback_url=callback_url,
        )
        if result.get('status') == 'success' and result.get('authority') and result.get('link'):
            topup.gateway_authority = result.get('authority')
            topup.payment_url = result.get('link')
            topup.status = WalletTopUp.STATUS_AWAITING_GATEWAY
            topup.save(update_fields=['gateway_authority', 'payment_url', 'status', 'updated_at'])
            return topup, True

        topup.status = WalletTopUp.STATUS_FAILED
        extra = dict(topup.metadata or {})
        extra['gateway_error'] = str(result.get('error') or 'Payment request failed.')[:MAX_METADATA_VALUE_LENGTH]
        topup.metadata = extra
        topup.save(update_fields=['status', 'metadata', 'updated_at'])
        raise TopUpGatewayError(result.get('error') or 'Payment request failed.')

    @classmethod
    def get_topup(cls, user, public_id):
        wallet = cls.get_or_create_wallet(user)
        try:
            return WalletTopUp.objects.get(wallet=wallet, public_id=public_id)
        except WalletTopUp.DoesNotExist as exc:
            raise TopUpNotFound() from exc

    @classmethod
    def mark_topup_failed(cls, authority, metadata=None):
        with transaction.atomic():
            topup = (
                WalletTopUp.objects.select_for_update()
                .filter(gateway_authority=authority)
                .first()
            )
            if not topup:
                raise TopUpNotFound()
            if topup.status == WalletTopUp.STATUS_CREDITED:
                return topup
            topup.status = WalletTopUp.STATUS_FAILED
            extra = dict(topup.metadata or {})
            extra.update(sanitize_metadata(metadata))
            topup.metadata = extra
            topup.save(update_fields=['status', 'metadata', 'updated_at'])
            return topup

    @classmethod
    def credit_verified_topup(cls, authority, *, payment_client=None):
        client = payment_client or ZarrinPal()
        topup = WalletTopUp.objects.filter(gateway_authority=authority).first()
        if not topup:
            raise TopUpNotFound()
        if topup.status == WalletTopUp.STATUS_CREDITED:
            return topup, False

        result = client.verify_payment(authority=authority, amount=topup.amount)
        with transaction.atomic():
            topup = WalletTopUp.objects.select_for_update().select_related('wallet').get(pk=topup.pk)
            if topup.status == WalletTopUp.STATUS_CREDITED:
                return topup, False
            if result.get('status') != 'success':
                topup.status = WalletTopUp.STATUS_FAILED
                extra = dict(topup.metadata or {})
                extra['gateway_error'] = str(result.get('error') or 'Payment verification failed.')[:MAX_METADATA_VALUE_LENGTH]
                topup.metadata = extra
                topup.save(update_fields=['status', 'metadata', 'updated_at'])
                return topup, False

            entry, created = cls._apply_entry(
                wallet=topup.wallet,
                entry_type=WalletEntry.TYPE_TOPUP,
                direction=WalletEntry.DIRECTION_CREDIT,
                amount=topup.amount,
                idempotency_key=f'topup:{topup.public_id}',
                topup=topup,
                metadata={
                    'gateway': 'zarinpal',
                    'authority': authority,
                    'ref_id': result.get('ref_id'),
                    'card_pan': result.get('card_pan'),
                    'source': 'topup_callback',
                },
            )
            if created or topup.status != WalletTopUp.STATUS_CREDITED:
                topup.status = WalletTopUp.STATUS_CREDITED
                topup.gateway_ref_id = result.get('ref_id')
                topup.credited_at = timezone.now()
                topup.save(update_fields=['status', 'gateway_ref_id', 'credited_at', 'updated_at'])
            return topup, created

    @staticmethod
    def _clear_purchased_cart_items(order):
        if not order.user_id:
            return
        try:
            cart = Cart.objects.get(user=order.user)
        except Cart.DoesNotExist:
            return
        for item in order.items.all():
            CartItem.objects.filter(
                cart=cart,
                content_type=item.content_type,
                object_id=item.object_id,
            ).delete()

    @classmethod
    def pay_order(cls, user, order, idempotency_key=None, metadata=None):
        key = idempotency_key or f'purchase:{order.order_id}'
        with transaction.atomic():
            order = Order.objects.select_for_update().select_related('user').get(pk=order.pk)
            wallet = cls._locked_wallet(user)

            if order.user_id != user.id:
                raise OrderNotPayable("This order does not belong to the current user.")

            existing = WalletEntry.objects.filter(
                order=order,
                entry_type=WalletEntry.TYPE_PURCHASE,
                direction=WalletEntry.DIRECTION_DEBIT,
            ).first()
            if existing:
                return existing, False

            if order.status == Order.STATUS_COMPLETED:
                raise OrderNotPayable("This order has already been settled.")
            if order.status not in PAYABLE_ORDER_STATUSES:
                raise OrderNotPayable(f"Order is not payable with wallet. Status: {order.status}.")
            if order.total_amount <= 0:
                raise InvalidAmount("Order total must be greater than zero.")

            entry, created = cls._apply_entry(
                wallet=wallet,
                entry_type=WalletEntry.TYPE_PURCHASE,
                direction=WalletEntry.DIRECTION_DEBIT,
                amount=order.total_amount,
                idempotency_key=key,
                order=order,
                metadata=metadata or {'source': 'wallet_purchase'},
            )
            if created:
                order.paid_at = timezone.now()
                order.payment_gateway_txn_id = f'wallet:{entry.pk}'
                order.save(update_fields=['paid_at', 'payment_gateway_txn_id'])
                process_successful_order(order)
                cls._clear_purchased_cart_items(order)
            return entry, created

    @classmethod
    def refund_debit(cls, *, actor, reason, order=None, original_entry=None, idempotency_key=None, metadata=None):
        reason = (reason or '').strip()
        if not reason:
            raise AdjustmentReasonRequired()

        with transaction.atomic():
            if original_entry is not None:
                original_entry = WalletEntry.objects.select_for_update().select_related('wallet', 'order').get(
                    pk=original_entry.pk
                )
            elif order is not None:
                original_entry = (
                    WalletEntry.objects.select_for_update()
                    .select_related('wallet', 'order')
                    .filter(
                        order=order,
                        entry_type=WalletEntry.TYPE_PURCHASE,
                        direction=WalletEntry.DIRECTION_DEBIT,
                    )
                    .first()
                )
                if original_entry is None:
                    raise RefundNotAllowed("No wallet purchase debit exists for this order.")
            else:
                raise RefundNotAllowed("A purchase entry or order is required to refund.")

            if (
                original_entry.entry_type != WalletEntry.TYPE_PURCHASE
                or original_entry.direction != WalletEntry.DIRECTION_DEBIT
            ):
                raise RefundNotAllowed("Only purchase debit entries can be refunded.")

            key = idempotency_key or f'refund:{original_entry.pk}'
            existing = WalletEntry.objects.filter(
                related_entry=original_entry,
                entry_type=WalletEntry.TYPE_REFUND,
            ).first()
            if existing:
                return existing, False

            wallet = Wallet.objects.select_for_update().get(pk=original_entry.wallet_id)
            entry, created = cls._apply_entry(
                wallet=wallet,
                entry_type=WalletEntry.TYPE_REFUND,
                direction=WalletEntry.DIRECTION_CREDIT,
                amount=original_entry.amount,
                idempotency_key=key,
                order=original_entry.order,
                related_entry=original_entry,
                reason=reason,
                metadata=metadata or {'source': 'wallet_refund'},
                created_by=actor,
            )
            if created and original_entry.order_id:
                related_order = Order.objects.select_for_update().get(pk=original_entry.order_id)
                related_order.status = Order.STATUS_REFUNDED
                related_order.save(update_fields=['status'])
                cls._log_admin_action(
                    actor=actor,
                    wallet=wallet,
                    message=f"Refunded purchase entry {original_entry.pk} for order {related_order.order_id}: {reason}",
                )
                logger.info(
                    "wallet.refund actor=%s entry=%s order=%s amount=%s reason=%s",
                    getattr(actor, 'pk', None), entry.pk, related_order.order_id, entry.amount, reason,
                )
            return entry, created

    @classmethod
    def admin_adjust(cls, user, amount, direction, reason, actor, idempotency_key, metadata=None):
        reason = (reason or '').strip()
        if not reason:
            raise AdjustmentReasonRequired()
        amount = _as_money(amount)
        if direction not in (WalletEntry.DIRECTION_CREDIT, WalletEntry.DIRECTION_DEBIT):
            raise WalletError("Adjustment direction must be credit or debit.")
        if not idempotency_key:
            raise WalletError("Idempotency key is required.")

        with transaction.atomic():
            wallet = cls._locked_wallet(user)
            entry, created = cls._apply_entry(
                wallet=wallet,
                entry_type=WalletEntry.TYPE_ADJUSTMENT,
                direction=direction,
                amount=amount,
                idempotency_key=idempotency_key,
                reason=reason,
                metadata=metadata or {'source': 'admin_adjustment'},
                created_by=actor,
            )
            if created:
                cls._log_admin_action(
                    actor=actor,
                    wallet=wallet,
                    message=f"Adjustment {direction} {amount}: {reason}",
                )
                logger.info(
                    "wallet.adjust actor=%s user=%s direction=%s amount=%s reason=%s entry=%s",
                    getattr(actor, 'pk', None), user.pk, direction, amount, reason, entry.pk,
                )
            return entry, created
