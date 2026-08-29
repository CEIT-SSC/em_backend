import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from wallet.exceptions import ImmutableLedgerError


class WalletQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if 'balance' in kwargs:
            raise ImmutableLedgerError("Wallet balance can only be changed through WalletService.")
        return super().update(**kwargs)


class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wallet',
        verbose_name="User",
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Cached balance",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WalletQuerySet.as_manager()

    class Meta:
        verbose_name = "Wallet"
        verbose_name_plural = "Wallets"
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name='wallet_balance_non_negative'),
        ]

    def __str__(self):
        owner = self.user if self.user_id else f"deleted user (wallet {self.pk})"
        return f"Wallet for {owner} ({self.balance})"

    def save(self, *args, **kwargs):
        allow_balance_write = kwargs.pop('allow_balance_write', False)
        if self.pk and not allow_balance_write:
            current = Wallet.objects.filter(pk=self.pk).values_list('balance', flat=True).first()
            if current is not None and current != self.balance:
                raise ImmutableLedgerError("Wallet balance can only be changed through WalletService.")
        super().save(*args, **kwargs)


class WalletTopUp(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_AWAITING_GATEWAY = 'awaiting_gateway'
    STATUS_CREDITED = 'credited'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_AWAITING_GATEWAY, "Awaiting Gateway"),
        (STATUS_CREDITED, "Credited"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='topups')
    order = models.ForeignKey(
        'shop.Order',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='wallet_topups',
        help_text="Order to pay after this top-up is verified, if any.",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    gateway_authority = models.CharField(max_length=50, blank=True, null=True, unique=True)
    gateway_ref_id = models.CharField(max_length=100, blank=True, null=True)
    payment_url = models.URLField(max_length=500, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    credited_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Wallet Top-up"
        verbose_name_plural = "Wallet Top-ups"
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='wallet_topup_amount_positive'),
        ]

    def __str__(self):
        return f"Top-up {self.public_id} ({self.status})"


class WalletEntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableLedgerError("Ledger entries cannot be edited.")

    def delete(self):
        raise ImmutableLedgerError("Ledger entries cannot be deleted.")


class WalletEntry(models.Model):
    TYPE_TOPUP = 'topup'
    TYPE_PURCHASE = 'purchase'
    TYPE_REFUND = 'refund'
    TYPE_ADJUSTMENT = 'adjustment'
    TYPE_CHOICES = [
        (TYPE_TOPUP, "Top-up"),
        (TYPE_PURCHASE, "Purchase"),
        (TYPE_REFUND, "Refund"),
        (TYPE_ADJUSTMENT, "Adjustment"),
    ]

    DIRECTION_CREDIT = 'credit'
    DIRECTION_DEBIT = 'debit'
    DIRECTION_CHOICES = [
        (DIRECTION_CREDIT, "Credit"),
        (DIRECTION_DEBIT, "Debit"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='entries')
    entry_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    idempotency_key = models.CharField(max_length=128, unique=True)

    order = models.ForeignKey(
        'shop.Order',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='wallet_entries',
    )
    topup = models.ForeignKey(
        WalletTopUp,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='entries',
    )
    related_entry = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='compensating_entries',
        help_text="Original debit that this refund compensates.",
    )
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wallet_entries_created',
        help_text="Actor for administrative adjustments and refunds.",
    )

    objects = WalletEntryQuerySet.as_manager()

    class Meta:
        verbose_name = "Wallet Ledger Entry"
        verbose_name_plural = "Wallet Ledger Entries"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['wallet', '-created_at']),
            models.Index(fields=['entry_type', 'order']),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='wallet_entry_amount_positive'),
            models.UniqueConstraint(
                fields=['order'],
                condition=Q(entry_type='purchase', direction='debit', order__isnull=False),
                name='unique_wallet_purchase_per_order',
            ),
            models.UniqueConstraint(
                fields=['related_entry'],
                condition=Q(entry_type='refund', related_entry__isnull=False),
                name='unique_wallet_refund_per_entry',
            ),
            models.UniqueConstraint(
                fields=['topup'],
                condition=Q(entry_type='topup', topup__isnull=False),
                name='unique_wallet_credit_per_topup',
            ),
        ]

    def __str__(self):
        sign = '+' if self.direction == self.DIRECTION_CREDIT else '-'
        return f"{self.entry_type} {sign}{self.amount} ({self.idempotency_key})"

    @property
    def signed_amount(self):
        if self.direction == self.DIRECTION_CREDIT:
            return self.amount
        return -self.amount

    def save(self, *args, **kwargs):
        if self.pk and WalletEntry.objects.filter(pk=self.pk).exists():
            raise ImmutableLedgerError("Ledger entries cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError("Ledger entries cannot be deleted.")

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Ledger amount must be positive.")
        if self.entry_type == self.TYPE_ADJUSTMENT and not (self.reason or '').strip():
            raise ValidationError("Administrative adjustments require a reason.")
        if self.entry_type == self.TYPE_PURCHASE and self.direction != self.DIRECTION_DEBIT:
            raise ValidationError("Purchases must be debit entries.")
        if self.entry_type == self.TYPE_REFUND and self.direction != self.DIRECTION_CREDIT:
            raise ValidationError("Refunds must be credit entries.")
        if self.entry_type == self.TYPE_TOPUP and self.direction != self.DIRECTION_CREDIT:
            raise ValidationError("Top-ups must be credit entries.")
