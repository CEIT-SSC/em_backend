import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .exceptions import InvalidStateTransition


class PaymentIntent(models.Model):
    PURPOSE_ORDER = "order"
    PURPOSE_WALLET_TOP_UP = "wallet_top_up"
    PURPOSE_CHOICES = [
        (PURPOSE_ORDER, "Order"),
        (PURPOSE_WALLET_TOP_UP, "Wallet top-up"),
    ]

    STATUS_REQUIRES_PAYMENT = "requires_payment"
    STATUS_PROCESSING = "processing"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [
        (STATUS_REQUIRES_PAYMENT, "Requires payment"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REVERSED, "Reversed"),
    ]
    ALLOWED_TRANSITIONS = {
        STATUS_REQUIRES_PAYMENT: {STATUS_PROCESSING, STATUS_FAILED, STATUS_SUCCEEDED},
        STATUS_PROCESSING: {STATUS_FAILED, STATUS_SUCCEEDED},
        STATUS_FAILED: {STATUS_PROCESSING, STATUS_SUCCEEDED},
        STATUS_SUCCEEDED: {STATUS_REVERSED},
        STATUS_REVERSED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payment_intents",
    )
    amount_rial = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="IRR", editable=False)
    purpose = models.CharField(max_length=32, choices=PURPOSE_CHOICES, db_index=True)
    reference_id = models.CharField(max_length=128, db_index=True)
    description = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(
        max_length=24, choices=STATUS_CHOICES, default=STATUS_REQUIRES_PAYMENT, db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["purpose", "reference_id"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount_rial__gt=0), name="payment_intent_amount_positive"),
        ]

    def __str__(self):
        return f"{self.id} {self.amount_rial} IRR ({self.status})"

    def clean(self):
        if isinstance(self.amount_rial, bool) or not isinstance(self.amount_rial, int) or self.amount_rial <= 0:
            raise ValidationError({"amount_rial": "Amount must be a positive integer number of Rials."})
        if self.currency != "IRR":
            raise ValidationError({"currency": "Payment intents use IRR."})
        if not (self.reference_id or "").strip():
            raise ValidationError({"reference_id": "A purpose reference is required."})
        if not (self.idempotency_key or "").strip():
            raise ValidationError({"idempotency_key": "An idempotency key is required."})

    def transition_to(self, new_status):
        if new_status == self.status:
            return False
        if new_status not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise InvalidStateTransition(
                f"Payment intent cannot move from '{self.status}' to '{new_status}'."
            )
        self.status = new_status
        if new_status == self.STATUS_SUCCEEDED and self.succeeded_at is None:
            self.succeeded_at = timezone.now()
        if new_status == self.STATUS_REVERSED and self.reversed_at is None:
            self.reversed_at = timezone.now()
        return True

    def save(self, *args, **kwargs):
        if self.pk:
            stored_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if (
                stored_status is not None
                and stored_status != self.status
                and self.status not in self.ALLOWED_TRANSITIONS.get(stored_status, set())
            ):
                raise InvalidStateTransition(
                    f"Payment intent cannot move from '{stored_status}' to '{self.status}'."
                )
        return super().save(*args, **kwargs)


class PaymentAttempt(models.Model):
    STATUS_CREATED = "created"
    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_FAILED = "failed"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_PENDING, "Pending provider verification"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REVERSED, "Reversed"),
    ]
    ALLOWED_TRANSITIONS = {
        STATUS_CREATED: {STATUS_PENDING, STATUS_FAILED, STATUS_VERIFIED},
        STATUS_PENDING: {STATUS_FAILED, STATUS_VERIFIED},
        STATUS_FAILED: {STATUS_VERIFIED},  # a provider may report a late success
        STATUS_VERIFIED: {STATUS_REVERSED},
        STATUS_REVERSED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey(PaymentIntent, on_delete=models.PROTECT, related_name="attempts")
    provider = models.CharField(max_length=64, db_index=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_CREATED, db_index=True)
    gateway_authority = models.CharField(max_length=128, null=True, blank=True, unique=True)
    gateway_reference_id = models.CharField(max_length=128, null=True, blank=True, unique=True)
    payment_url = models.URLField(max_length=500, null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    provider_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["intent", "-created_at"]),
            models.Index(fields=["provider", "gateway_authority"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.id} ({self.status})"

    def transition_to(self, new_status):
        if new_status == self.status:
            return False
        if new_status not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise InvalidStateTransition(
                f"Payment attempt cannot move from '{self.status}' to '{new_status}'."
            )
        self.status = new_status
        now = timezone.now()
        if new_status == self.STATUS_VERIFIED and self.verified_at is None:
            self.verified_at = now
        elif new_status == self.STATUS_FAILED and self.failed_at is None:
            self.failed_at = now
        elif new_status == self.STATUS_REVERSED and self.reversed_at is None:
            self.reversed_at = now
        return True

    def save(self, *args, **kwargs):
        if self.pk:
            stored_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if (
                stored_status is not None
                and stored_status != self.status
                and self.status not in self.ALLOWED_TRANSITIONS.get(stored_status, set())
            ):
                raise InvalidStateTransition(
                    f"Payment attempt cannot move from '{stored_status}' to '{self.status}'."
                )
        return super().save(*args, **kwargs)


class PaymentSettlement(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REVERSED, "Reversed"),
    ]

    intent = models.OneToOneField(PaymentIntent, primary_key=True, on_delete=models.PROTECT, related_name="settlement")
    idempotency_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Settlement for {self.intent_id} ({self.status})"
