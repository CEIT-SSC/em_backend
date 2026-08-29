import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Sum, When
from django.utils import timezone

from payment_core.exceptions import PaymentError
from payment_core.models import PaymentAttempt, PaymentIntent
from payment_core.providers import CustomerData
from payment_core.services import create_intent, start_payment_attempt, verify_callback
from shop.fulfillment import process_successful_order
from shop.eligibility import OrderPaymentEligibilityError, validate_order_items_for_payment
from shop.models import Cart, CartItem, Order

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
from wallet.payments import ZarrinPal
from wallet.payment_provider import ZarinpalPaymentProvider

logger = logging.getLogger(__name__)

TWOPLACES = Decimal('0.01')
SAFE_METADATA_KEYS = frozenset({
    'source', 'note', 'gateway', 'gateway_error', 'authority', 'ref_id', 'app', 'card_pan',
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


def _as_rial(amount):
    rial = amount * Decimal('10')
    if rial != rial.to_integral_value():
        raise InvalidAmount("Wallet amount cannot be represented as a whole number of Rials.")
    value = int(rial)
    if value <= 0:
        raise InvalidAmount()
    return value


def _wallet_provider_adapter(provider_name, payment_client=None):
    # payment_client is the backwards-compatible injection point for the legacy Zarinpal test/client.
    if payment_client is not None:
        return ZarinpalPaymentProvider(payment_client)
    if provider_name == 'zarinpal':
        return ZarinpalPaymentProvider(ZarrinPal())
    return None  # payment-core resolves any other registered provider by name.


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

    @staticmethod
    def _entry_matches_request(
        entry,
        *,
        wallet,
        entry_type,
        direction,
        amount,
        order=None,
        topup=None,
        related_entry=None,
        reason='',
        created_by=None,
    ):
        """Return whether an existing key represents this exact financial operation."""
        return (
            entry.wallet_id == wallet.pk
            and entry.entry_type == entry_type
            and entry.direction == direction
            and entry.amount == amount
            and entry.order_id == getattr(order, 'pk', None)
            and entry.topup_id == getattr(topup, 'pk', None)
            and entry.related_entry_id == getattr(related_entry, 'pk', None)
            and entry.reason == (reason or '')
            and entry.created_by_id == getattr(created_by, 'pk', None)
        )

    @classmethod
    def _validate_existing_entry(cls, entry, **request):
        if not cls._entry_matches_request(entry, **request):
            raise DuplicateIdempotencyKey()
        return entry, False

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
        reference_order = order if entry_type == WalletEntry.TYPE_PURCHASE else None
        reference_topup = topup if entry_type == WalletEntry.TYPE_TOPUP else None
        reference_entry = related_entry if entry_type == WalletEntry.TYPE_REFUND else None
        existing = cls._find_existing_entry(
            idempotency_key=idempotency_key,
            order=reference_order,
            topup=reference_topup,
            related_entry=reference_entry,
        )
        if existing:
            return cls._validate_existing_entry(
                existing,
                wallet=wallet,
                entry_type=entry_type,
                direction=direction,
                amount=amount,
                order=order,
                topup=topup,
                related_entry=related_entry,
                reason=reason,
                created_by=created_by,
            )

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
                order=reference_order,
                topup=reference_topup,
                related_entry=reference_entry,
            )
            if existing:
                return cls._validate_existing_entry(
                    existing,
                    wallet=wallet,
                    entry_type=entry_type,
                    direction=direction,
                    amount=amount,
                    order=order,
                    topup=topup,
                    related_entry=related_entry,
                    reason=reason,
                    created_by=created_by,
                )
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
        *,
        callback_url,
        order=None,
        metadata=None,
        payment_client=None,
    ):
        """Create a fresh top-up and obtain a gateway link without crediting the wallet."""
        amount = _as_money(amount)
        if not callback_url:
            raise TopUpGatewayError("Wallet payment callback URL is not configured.")
        if order is not None and order.user_id != user.pk:
            raise OrderNotPayable("This order does not belong to the current user.")

        with transaction.atomic():
            wallet = cls._locked_wallet(user)
            topup = WalletTopUp.objects.create(
                wallet=wallet,
                order=order,
                amount=amount,
                status=WalletTopUp.STATUS_PENDING,
                metadata=sanitize_metadata(metadata),
            )

        try:
            intent, _ = create_intent(
                user=user,
                amount_rial=_as_rial(amount),
                purpose=PaymentIntent.PURPOSE_WALLET_TOP_UP,
                reference_id=str(topup.public_id),
                description=f'Wallet top-up {topup.public_id}',
                idempotency_key=f'wallet-topup-intent:{topup.public_id}',
                metadata={
                    'source': (metadata or {}).get('source', 'wallet'),
                    'wallet_topup_id': str(topup.public_id),
                    'order_id': str(order.order_id) if order else None,
                },
            )
            provider_name = getattr(settings, 'WALLET_PAYMENT_PROVIDER', 'zarinpal')
            attempt, _ = start_payment_attempt(
                intent=intent,
                provider=provider_name,
                idempotency_key=f'wallet-topup-attempt:{topup.public_id}',
                callback_url=callback_url,
                adapter=_wallet_provider_adapter(provider_name, payment_client),
                customer=CustomerData(
                    mobile=getattr(user, 'phone_number', None) or None,
                    email=getattr(user, 'email', None) or None,
                ),
            )
        except PaymentError as exc:
            gateway_error = str(exc)[:MAX_METADATA_VALUE_LENGTH]
            cls.mark_topup_failed(topup, metadata={'gateway_error': gateway_error})
            raise TopUpGatewayError(gateway_error) from exc

        with transaction.atomic():
            topup = WalletTopUp.objects.select_for_update().get(pk=topup.pk)
            topup.payment_intent = attempt.intent
            topup.payment_attempt = attempt
            topup.gateway_authority = attempt.gateway_authority
            topup.payment_url = attempt.payment_url
            topup.status = WalletTopUp.STATUS_AWAITING_GATEWAY
            topup.save(update_fields=[
                'payment_intent', 'payment_attempt', 'gateway_authority', 'payment_url',
                'status', 'updated_at',
            ])
        return topup, True

    @classmethod
    def get_topup(cls, user, public_id):
        wallet = cls.get_or_create_wallet(user)
        try:
            return WalletTopUp.objects.get(wallet=wallet, public_id=public_id)
        except WalletTopUp.DoesNotExist as exc:
            raise TopUpNotFound() from exc

    @classmethod
    def mark_topup_failed(cls, topup_or_authority, metadata=None):
        with transaction.atomic():
            if isinstance(topup_or_authority, WalletTopUp):
                topup = WalletTopUp.objects.select_for_update().get(pk=topup_or_authority.pk)
            else:
                topup = (
                    WalletTopUp.objects.select_for_update()
                    .filter(gateway_authority=topup_or_authority)
                    .first()
                )
                if topup is None:
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
        """Verify through payment-core, which coordinates idempotent wallet settlement."""
        topup = WalletTopUp.objects.filter(gateway_authority=authority).first()
        if topup is None:
            raise TopUpNotFound()
        if topup.status == WalletTopUp.STATUS_CREDITED:
            return topup, False

        if topup.payment_attempt_id is None:
            raise TopUpGatewayError("This legacy top-up has no payment-core attempt.")
        was_credited = topup.status == WalletTopUp.STATUS_CREDITED
        try:
            provider_name = topup.payment_attempt.provider
            attempt, settlement = verify_callback(
                provider=provider_name,
                authority=authority,
                adapter=_wallet_provider_adapter(provider_name, payment_client),
            )
        except PaymentError as exc:
            cls.mark_topup_failed(topup, metadata={'gateway_error': str(exc)})
            raise TopUpGatewayError(str(exc)) from exc

        topup.refresh_from_db()
        if attempt.status != PaymentAttempt.STATUS_VERIFIED:
            error = attempt.error_message or 'Payment verification failed.'
            cls.mark_topup_failed(topup, metadata={'gateway_error': error})
            topup.refresh_from_db()
            return topup, False
        if settlement is None or settlement.status != settlement.STATUS_SUCCEEDED:
            return topup, False
        return topup, not was_credited and topup.status == WalletTopUp.STATUS_CREDITED

    @classmethod
    def settle_verified_payment_topup(cls, request):
        """Credit a provider-verified top-up; called only by payment-core settlement."""
        with transaction.atomic():
            try:
                topup = (
                    WalletTopUp.objects.select_for_update()
                    .select_related('wallet', 'order')
                    .get(public_id=request.reference_id)
                )
            except (WalletTopUp.DoesNotExist, ValueError) as exc:
                raise TopUpNotFound() from exc
            if topup.wallet.user_id is None or topup.wallet.user_id != request.user_id:
                raise TopUpGatewayError("Payment intent does not belong to this wallet owner.")
            if topup.payment_intent_id and str(topup.payment_intent_id) != request.payment_intent_id:
                raise TopUpGatewayError("Wallet top-up is linked to a different payment intent.")
            if _as_rial(topup.amount) != request.amount_rial:
                raise TopUpGatewayError("Verified payment amount does not match the wallet top-up.")

            attempt = (
                PaymentAttempt.objects.filter(
                    intent_id=request.payment_intent_id,
                    status=PaymentAttempt.STATUS_VERIFIED,
                )
                .order_by('-verified_at')
                .first()
            )
            if attempt is None:
                raise TopUpGatewayError("No verified payment attempt exists for this top-up.")

            topup.payment_intent_id = request.payment_intent_id
            topup.payment_attempt = attempt
            topup.gateway_authority = attempt.gateway_authority
            topup.gateway_ref_id = attempt.gateway_reference_id
            topup.payment_url = attempt.payment_url

            extra_meta = {
                'source': 'topup_callback',
                'gateway': attempt.provider,
                'authority': attempt.gateway_authority,
                'ref_id': attempt.gateway_reference_id,
            }
            _entry, created = cls._apply_entry(
                wallet=topup.wallet,
                entry_type=WalletEntry.TYPE_TOPUP,
                direction=WalletEntry.DIRECTION_CREDIT,
                amount=topup.amount,
                idempotency_key=f'topup:{topup.public_id}',
                topup=topup,
                metadata=extra_meta,
            )
            if created or topup.status != WalletTopUp.STATUS_CREDITED:
                topup.status = WalletTopUp.STATUS_CREDITED
                topup.credited_at = topup.credited_at or timezone.now()
                extra = dict(topup.metadata or {})
                extra.update(extra_meta)
                topup.metadata = extra
            topup.save(update_fields=[
                'payment_intent', 'payment_attempt', 'gateway_authority', 'gateway_ref_id',
                'payment_url', 'status', 'credited_at', 'metadata', 'updated_at',
            ])

        cls.settle_order_for_credited_topup(topup)
        return topup

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
        if cart.applied_discount_code and not cart.items.exists():
            cart.applied_discount_code = None
            cart.save(update_fields=['applied_discount_code'])

    @classmethod
    def pay_order(cls, user, order, idempotency_key=None, metadata=None):
        key = idempotency_key or f'purchase:{order.order_id}'
        with transaction.atomic():
            order = Order.objects.select_for_update().select_related('user').get(pk=order.pk)
            wallet = cls._locked_wallet(user)

            if order.user_id != user.id:
                raise OrderNotPayable("This order does not belong to the current user.")

            existing = cls._find_existing_entry(idempotency_key=key, order=order)
            if existing:
                return cls._validate_existing_entry(
                    existing,
                    wallet=wallet,
                    entry_type=WalletEntry.TYPE_PURCHASE,
                    direction=WalletEntry.DIRECTION_DEBIT,
                    amount=_as_money(order.total_amount),
                    order=order,
                )

            if order.status == Order.STATUS_COMPLETED:
                raise OrderNotPayable("This order has already been settled.")
            if order.status not in PAYABLE_ORDER_STATUSES:
                raise OrderNotPayable(f"Order is not payable with wallet. Status: {order.status}.")
            if order.total_amount <= 0:
                raise InvalidAmount("Order total must be greater than zero.")

            try:
                validate_order_items_for_payment(order)
            except OrderPaymentEligibilityError as exc:
                raise OrderNotPayable(str(exc)) from exc

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
                order.save(update_fields=['paid_at'])
                process_successful_order(order)
                cls._clear_purchased_cart_items(order)
            return entry, created

    @classmethod
    def pay_or_start_order_payment(
        cls,
        user,
        order,
        *,
        callback_url,
        metadata=None,
        payment_client=None,
    ):
        """Pay from wallet when possible; otherwise fund only the current shortfall."""
        try:
            entry, created = cls.pay_order(
                user,
                order,
                metadata=metadata or {'source': 'order_checkout'},
            )
        except InsufficientFunds as exc:
            shortfall = _as_money(exc.required - exc.available)
            topup, _created = cls.start_topup(
                user,
                shortfall,
                callback_url=callback_url,
                order=order,
                metadata={'source': 'order_payment'},
                payment_client=payment_client,
            )
            wallet = cls.get_or_create_wallet(user)
            return {
                'payment_required': True,
                'payment_url': topup.payment_url,
                'topup': topup,
                'entry': None,
                'already_processed': False,
                'balance': wallet.balance,
            }

        entry.wallet.refresh_from_db(fields=['balance'])
        return {
            'payment_required': False,
            'payment_url': None,
            'topup': None,
            'entry': entry,
            'already_processed': not created,
            'balance': entry.wallet.balance,
        }

    @classmethod
    def settle_order_for_credited_topup(cls, topup):
        """Attempt the linked order after credit; keep the credit if the order is no longer payable."""
        topup = WalletTopUp.objects.select_related('wallet__user', 'order').get(pk=topup.pk)
        if (
            topup.status != WalletTopUp.STATUS_CREDITED
            or topup.order_id is None
            or topup.wallet.user_id is None
        ):
            return None
        try:
            return cls.pay_order(
                topup.wallet.user,
                topup.order,
                metadata={'source': 'verified_order_topup'},
            )
        except (InsufficientFunds, InvalidAmount, OrderNotPayable, DuplicateIdempotencyKey) as exc:
            logger.warning(
                "wallet.topup credited but linked order was not settled topup=%s order=%s error=%s",
                topup.public_id,
                topup.order.order_id,
                exc,
            )
            return None

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
            wallet = Wallet.objects.select_for_update().get(pk=original_entry.wallet_id)
            existing = cls._find_existing_entry(
                idempotency_key=key,
                related_entry=original_entry,
            )
            if existing:
                return cls._validate_existing_entry(
                    existing,
                    wallet=wallet,
                    entry_type=WalletEntry.TYPE_REFUND,
                    direction=WalletEntry.DIRECTION_CREDIT,
                    amount=_as_money(original_entry.amount),
                    order=original_entry.order,
                    related_entry=original_entry,
                    reason=reason,
                    created_by=actor,
                )

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
