import logging
from collections.abc import Mapping

from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import (
    AttemptNotFound,
    DuplicateGatewayIdentifier,
    IdempotencyConflict,
    InvalidPaymentAmount,
    InvalidStateTransition,
    PaymentNotFound,
    ProviderFailure,
)
from .models import PaymentAttempt, PaymentIntent, PaymentSettlement
from .providers import (
    CreatePaymentRequest,
    NormalizedProviderError,
    PaymentRequestStatus,
    PaymentRequestResult,
    ProviderErrorCategory,
    ProviderNotRegistered,
    ReversalRequest,
    ReversalResult,
    ReversalStatus,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    provider_registry,
)
from .providers.redaction import sanitize_provider_data
from .settlement import ConfiguredSettlementCoordinator

logger = logging.getLogger("payments")


def _plain(value):
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _log(level, message, *, intent=None, attempt=None, **values):
    extra = {
        "payment_intent_id": str(intent.id if intent else attempt.intent_id if attempt else ""),
        "payment_attempt_id": str(attempt.id if attempt else ""),
        **values,
    }
    getattr(logger, level)(message, extra=extra)


def _normalized_exception(exc):
    if isinstance(exc, TimeoutError):
        return NormalizedProviderError(
            "provider_timeout", "Payment provider timed out.", ProviderErrorCategory.TIMEOUT, retryable=True,
        )
    if isinstance(exc, OSError):
        return NormalizedProviderError(
            "provider_network_error", "Payment provider could not be reached.",
            ProviderErrorCategory.NETWORK, retryable=True,
        )
    return NormalizedProviderError(
        "provider_error", "Payment provider returned an unexpected error.", ProviderErrorCategory.UNKNOWN,
    )


def _same_intent(intent, *, user, amount_rial, purpose, reference_id, description, metadata):
    return (
        intent.user_id == getattr(user, "pk", None)
        and intent.amount_rial == amount_rial
        and intent.purpose == purpose
        and intent.reference_id == reference_id
        and intent.description == description
        and intent.metadata == metadata
    )


def create_intent(
    *, user, amount_rial, purpose, reference_id, description, idempotency_key, metadata=None,
):
    """Create what must be paid, returning the original object for an identical retry."""
    if isinstance(amount_rial, bool) or not isinstance(amount_rial, int) or amount_rial <= 0:
        raise InvalidPaymentAmount("Amount must be a positive integer number of Rials.")
    metadata = dict(metadata or {})
    values = {
        "user": user,
        "amount_rial": amount_rial,
        "purpose": purpose,
        "reference_id": str(reference_id),
        "description": description,
        "idempotency_key": idempotency_key,
        "metadata": metadata,
    }
    existing = PaymentIntent.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if not _same_intent(existing, **{k: values[k] for k in (
            "user", "amount_rial", "purpose", "reference_id", "description", "metadata"
        )}):
            raise IdempotencyConflict("This idempotency key was already used for another payment intent.")
        return existing, False

    try:
        with transaction.atomic():
            intent = PaymentIntent(**values)
            intent.full_clean()
            intent.save(force_insert=True)
    except IntegrityError:
        existing = PaymentIntent.objects.filter(idempotency_key=idempotency_key).first()
        if existing and _same_intent(existing, **{k: values[k] for k in (
            "user", "amount_rial", "purpose", "reference_id", "description", "metadata"
        )}):
            return existing, False
        raise IdempotencyConflict("This idempotency key was already used for another payment intent.")

    _log("info", "payment.intent_created", intent=intent, purpose=purpose)
    return intent, True


def _get_provider(name, registry, adapter=None):
    if adapter is not None:
        if adapter.name.strip().lower() != name.strip().lower():
            raise ProviderFailure("The supplied payment provider does not match the requested provider.")
        return adapter
    try:
        return registry.get(name)
    except ProviderNotRegistered as exc:
        raise ProviderFailure(str(exc)) from exc


def start_payment_attempt(
    *, intent, provider, idempotency_key, callback_url, registry=provider_registry, adapter=None,
    customer=None,
):
    """Create one provider attempt and request its redirect authority."""
    provider_name = provider.strip().lower()
    payment_provider = _get_provider(provider_name, registry, adapter)
    intent_id = getattr(intent, "pk", intent)

    existing = PaymentAttempt.objects.select_related("intent").filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.intent_id != intent_id or existing.provider != provider_name:
            raise IdempotencyConflict("This idempotency key was already used for another payment attempt.")
        return existing, False

    with transaction.atomic():
        locked_intent = PaymentIntent.objects.select_for_update().get(pk=intent_id)
        if locked_intent.status in {PaymentIntent.STATUS_SUCCEEDED, PaymentIntent.STATUS_REVERSED}:
            raise InvalidStateTransition(f"Cannot start an attempt for a '{locked_intent.status}' intent.")
        try:
            with transaction.atomic():
                attempt = PaymentAttempt.objects.create(
                    intent=locked_intent, provider=provider_name, idempotency_key=idempotency_key,
                )
        except IntegrityError:
            existing = PaymentAttempt.objects.filter(idempotency_key=idempotency_key).first()
            if existing and existing.intent_id == intent_id and existing.provider == provider_name:
                return existing, False
            raise IdempotencyConflict("This idempotency key was already used for another payment attempt.")
        locked_intent.transition_to(PaymentIntent.STATUS_PROCESSING)
        locked_intent.save(update_fields=["status", "updated_at"])

    request = CreatePaymentRequest(
        amount_rial=attempt.intent.amount_rial,
        callback_url=callback_url,
        description=attempt.intent.description,
        client_reference=str(attempt.id),
        customer=customer,
        metadata={"intent_id": str(attempt.intent_id), "purpose": attempt.intent.purpose},
    )
    try:
        result = payment_provider.create_payment(request)
    except Exception as exc:
        error = _normalized_exception(exc)
        record_failure(attempt=attempt, error=error)
        raise ProviderFailure(error.message, provider_error=error) from exc

    if not isinstance(result, PaymentRequestResult):
        error = NormalizedProviderError(
            "malformed_provider_response", "Payment provider returned an invalid response.",
            ProviderErrorCategory.MALFORMED_RESPONSE,
        )
        record_failure(attempt=attempt, error=error)
        raise ProviderFailure(error.message, provider_error=error)
    if result.status != PaymentRequestStatus.CREATED:
        record_failure(
            attempt=attempt,
            error=result.error,
            provider_data=result.sanitized_provider_data,
        )
        raise ProviderFailure(result.error.message, provider_error=result.error)

    try:
        payment_url = payment_provider.generate_redirect_url(result.authority)
    except Exception as exc:
        error = _normalized_exception(exc)
        record_failure(attempt=attempt, error=error)
        raise ProviderFailure(error.message, provider_error=error) from exc

    try:
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().select_related("intent").get(pk=attempt.pk)
            attempt.gateway_authority = result.authority
            attempt.payment_url = payment_url
            attempt.provider_data = _plain(result.sanitized_provider_data)
            attempt.transition_to(PaymentAttempt.STATUS_PENDING)
            attempt.save(update_fields=[
                "gateway_authority", "payment_url", "provider_data", "status", "updated_at",
            ])
    except IntegrityError as exc:
        error = NormalizedProviderError(
            "duplicate_gateway_authority", "Provider returned an authority already in use.",
            ProviderErrorCategory.MALFORMED_RESPONSE,
        )
        record_failure(attempt=attempt, error=error)
        raise DuplicateGatewayIdentifier(error.message) from exc

    _log("info", "payment.attempt_started", attempt=attempt, provider=provider_name)
    return attempt, True


def record_failure(*, attempt, error, provider_data=None):
    """Normalize and persist provider failure without downgrading a verified payment."""
    attempt_id = getattr(attempt, "pk", attempt)
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().select_related("intent").get(pk=attempt_id)
        intent = PaymentIntent.objects.select_for_update().get(pk=attempt.intent_id)
        if attempt.status in {PaymentAttempt.STATUS_VERIFIED, PaymentAttempt.STATUS_REVERSED}:
            return attempt
        if attempt.status != PaymentAttempt.STATUS_FAILED:
            attempt.transition_to(PaymentAttempt.STATUS_FAILED)
        attempt.error_code = error.code if error else "provider_failure"
        attempt.error_message = error.message if error else "Payment provider rejected the operation."
        if provider_data is not None:
            attempt.provider_data = _plain(sanitize_provider_data(provider_data))
        attempt.save(update_fields=[
            "status", "error_code", "error_message", "provider_data", "failed_at", "updated_at",
        ])
        has_live_attempt = intent.attempts.exclude(pk=attempt.pk).filter(
            status__in=[PaymentAttempt.STATUS_CREATED, PaymentAttempt.STATUS_PENDING]
        ).exists()
        if not has_live_attempt and intent.status not in {
            PaymentIntent.STATUS_SUCCEEDED, PaymentIntent.STATUS_REVERSED, PaymentIntent.STATUS_FAILED,
        }:
            intent.transition_to(PaymentIntent.STATUS_FAILED)
            intent.save(update_fields=["status", "updated_at"])
    _log("warning", "payment.attempt_failed", attempt=attempt, error_code=attempt.error_code)
    return attempt


def _coordinate_settlement(intent, coordinator):
    key = f"payment-settlement:{intent.id}"
    settlement, _ = PaymentSettlement.objects.select_for_update().get_or_create(
        intent=intent, defaults={"idempotency_key": key},
    )
    if settlement.status in {PaymentSettlement.STATUS_SUCCEEDED, PaymentSettlement.STATUS_REVERSED}:
        return settlement
    settlement.status = PaymentSettlement.STATUS_PENDING
    settlement.error_message = ""
    settlement.save(update_fields=["status", "error_message", "updated_at"])
    try:
        with transaction.atomic():
            coordinator.settle(intent, key)
    except Exception as exc:
        settlement.status = PaymentSettlement.STATUS_FAILED
        settlement.error_message = str(exc)[:500]
        settlement.save(update_fields=["status", "error_message", "updated_at"])
        _log("exception", "payment.settlement_failed", intent=intent)
    else:
        settlement.status = PaymentSettlement.STATUS_SUCCEEDED
        settlement.settled_at = timezone.now()
        settlement.save(update_fields=["status", "settled_at", "updated_at"])
        _log("info", "payment.settlement_succeeded", intent=intent)
    return settlement


def record_verification(*, attempt, result, settlement_coordinator=None):
    """Persist server-side verification and settle the purpose exactly once."""
    if result.status not in {VerificationStatus.VERIFIED, VerificationStatus.ALREADY_VERIFIED}:
        failed = record_failure(
            attempt=attempt, error=result.error, provider_data=result.sanitized_provider_data,
        )
        return failed, None
    attempt_id = getattr(attempt, "pk", attempt)
    coordinator = settlement_coordinator or ConfiguredSettlementCoordinator()
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().select_related("intent").get(pk=attempt_id)
        intent = PaymentIntent.objects.select_for_update().get(pk=attempt.intent_id)
        if result.verified_amount_rial is not None and result.verified_amount_rial != intent.amount_rial:
            error = NormalizedProviderError(
                "amount_mismatch", "Verified amount does not match the payment intent.",
                ProviderErrorCategory.AMOUNT_MISMATCH,
            )
            # Locks are re-entrant only at the database level; persist directly here.
            if attempt.status not in {PaymentAttempt.STATUS_VERIFIED, PaymentAttempt.STATUS_REVERSED}:
                attempt.transition_to(PaymentAttempt.STATUS_FAILED)
                attempt.error_code = error.code
                attempt.error_message = error.message
                attempt.save(update_fields=[
                    "status", "error_code", "error_message", "failed_at", "updated_at",
                ])
                if intent.status not in {
                    PaymentIntent.STATUS_SUCCEEDED, PaymentIntent.STATUS_REVERSED, PaymentIntent.STATUS_FAILED,
                }:
                    intent.transition_to(PaymentIntent.STATUS_FAILED)
                    intent.save(update_fields=["status", "updated_at"])
            attempt.intent = intent
            return attempt, None
        if intent.status == PaymentIntent.STATUS_REVERSED:
            raise InvalidStateTransition("A reversed payment intent cannot be verified again.")
        if attempt.status != PaymentAttempt.STATUS_VERIFIED:
            attempt.transition_to(PaymentAttempt.STATUS_VERIFIED)
        if result.transaction_reference:
            attempt.gateway_reference_id = result.transaction_reference
        attempt.error_code = ""
        attempt.error_message = ""
        attempt.provider_data = _plain(result.sanitized_provider_data)
        try:
            with transaction.atomic():
                attempt.save(update_fields=[
                    "status", "gateway_reference_id", "error_code", "error_message", "provider_data",
                    "verified_at", "updated_at",
                ])
        except IntegrityError as exc:
            raise DuplicateGatewayIdentifier("Provider returned a reference ID already in use.") from exc
        if intent.status != PaymentIntent.STATUS_SUCCEEDED:
            intent.transition_to(PaymentIntent.STATUS_SUCCEEDED)
            intent.save(update_fields=["status", "succeeded_at", "updated_at"])
        settlement = _coordinate_settlement(intent, coordinator)
        attempt.intent = intent
    _log("info", "payment.attempt_verified", attempt=attempt)
    return attempt, settlement


def verify_callback(
    *, provider, authority, registry=provider_registry, adapter=None, settlement_coordinator=None,
):
    """Locate by authority, then trust only the provider's server-side verification result."""
    provider_name = provider.strip().lower()
    try:
        attempt = PaymentAttempt.objects.select_related("intent").get(
            provider=provider_name, gateway_authority=authority,
        )
    except PaymentAttempt.DoesNotExist as exc:
        raise AttemptNotFound("No payment attempt matches this provider authority.") from exc
    payment_provider = _get_provider(provider_name, registry, adapter)
    try:
        result = payment_provider.verify_payment(VerificationRequest(
            authority=attempt.gateway_authority,
            expected_amount_rial=attempt.intent.amount_rial,
        ))
    except Exception as exc:
        error = _normalized_exception(exc)
        record_failure(attempt=attempt, error=error)
        raise ProviderFailure(error.message, provider_error=error) from exc
    if not isinstance(result, VerificationResult):
        error = NormalizedProviderError(
            "malformed_provider_response", "Payment provider returned an invalid verification response.",
            ProviderErrorCategory.MALFORMED_RESPONSE,
        )
        record_failure(attempt=attempt, error=error)
        raise ProviderFailure(error.message, provider_error=error)
    return record_verification(
        attempt=attempt, result=result, settlement_coordinator=settlement_coordinator,
    )


def record_reversal(*, attempt, result, settlement_coordinator=None):
    if result.status not in {ReversalStatus.REVERSED, ReversalStatus.ALREADY_REVERSED}:
        raise ProviderFailure(result.error.message, provider_error=result.error)
    attempt_id = getattr(attempt, "pk", attempt)
    coordinator = settlement_coordinator or ConfiguredSettlementCoordinator()
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().select_related("intent").get(pk=attempt_id)
        intent = PaymentIntent.objects.select_for_update().get(pk=attempt.intent_id)
        if attempt.status != PaymentAttempt.STATUS_REVERSED:
            attempt.transition_to(PaymentAttempt.STATUS_REVERSED)
            attempt.save(update_fields=["status", "reversed_at", "updated_at"])
        if intent.status != PaymentIntent.STATUS_REVERSED:
            intent.transition_to(PaymentIntent.STATUS_REVERSED)
            intent.save(update_fields=["status", "reversed_at", "updated_at"])
        settlement = PaymentSettlement.objects.select_for_update().filter(intent=intent).first()
        if settlement and settlement.status != PaymentSettlement.STATUS_REVERSED:
            reversal_key = f"payment-reversal:{intent.id}"
            with transaction.atomic():
                coordinator.reverse(intent, reversal_key)
            settlement.status = PaymentSettlement.STATUS_REVERSED
            settlement.reversed_at = timezone.now()
            settlement.save(update_fields=["status", "reversed_at", "updated_at"])
    _log("info", "payment.attempt_reversed", attempt=attempt)
    return attempt


def reverse_payment(*, attempt, registry=provider_registry, adapter=None, settlement_coordinator=None):
    attempt_id = getattr(attempt, "pk", attempt)
    try:
        attempt = PaymentAttempt.objects.select_related("intent").get(pk=attempt_id)
    except PaymentAttempt.DoesNotExist as exc:
        raise AttemptNotFound("Payment attempt was not found.") from exc
    payment_provider = _get_provider(attempt.provider, registry, adapter)
    try:
        result = payment_provider.reverse_payment(ReversalRequest(
            authority=attempt.gateway_authority,
            expected_amount_rial=attempt.intent.amount_rial,
            transaction_reference=attempt.gateway_reference_id,
        ))
    except Exception as exc:
        error = _normalized_exception(exc)
        raise ProviderFailure(error.message, provider_error=error) from exc
    if not isinstance(result, ReversalResult):
        error = NormalizedProviderError(
            "malformed_provider_response", "Payment provider returned an invalid reversal response.",
            ProviderErrorCategory.MALFORMED_RESPONSE,
        )
        raise ProviderFailure(error.message, provider_error=error)
    return record_reversal(
        attempt=attempt, result=result, settlement_coordinator=settlement_coordinator,
    )


def get_payment_status(*, intent_id, user=None):
    queryset = PaymentIntent.objects.prefetch_related("attempts")
    if user is not None and not getattr(user, "is_staff", False):
        queryset = queryset.filter(user=user)
    try:
        return queryset.get(pk=intent_id)
    except PaymentIntent.DoesNotExist as exc:
        raise PaymentNotFound("Payment intent was not found.") from exc


query_payment_status = get_payment_status


class PaymentService:
    """Discoverable application-service facade; functions remain easy to inject and test."""

    create_intent = staticmethod(create_intent)
    start_payment_attempt = staticmethod(start_payment_attempt)
    record_verification = staticmethod(record_verification)
    record_failure = staticmethod(record_failure)
    record_reversal = staticmethod(record_reversal)
    verify_callback = staticmethod(verify_callback)
    reverse_payment = staticmethod(reverse_payment)
    query_payment_status = staticmethod(query_payment_status)
