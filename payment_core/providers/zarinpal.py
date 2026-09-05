"""Production-grade Zarinpal payment provider — REST API v4 with Rial amounts."""

import logging
import re
from collections.abc import Mapping
from urllib.parse import quote

import requests

from .base import PaymentProvider
from .redaction import sanitize_provider_data
from .types import (
    ConfigurationValidationResult,
    CreatePaymentRequest,
    InquiryRequest,
    InquiryResult,
    NormalizedProviderError,
    PaymentInquiryStatus,
    PaymentRequestResult,
    PaymentRequestStatus,
    ProviderErrorCategory,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderRuntimeOptions,
    ReversalRequest,
    ReversalResult,
    ReversalStatus,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger("payments")

# ── Zarinpal response codes ──────────────────────────────────────────────────
_CODE_SUCCESS = 100
_CODE_ALREADY_VERIFIED = 101
_MINIMUM_AMOUNT_RIAL = 1_000

_INQUIRY_STATUS_MAP = {
    "SUCCESS": PaymentInquiryStatus.VERIFIED,
    "VERIFIED": PaymentInquiryStatus.VERIFIED,
    "PAID": PaymentInquiryStatus.PENDING,
    "IN_BANK": PaymentInquiryStatus.PENDING,
    "FAILED": PaymentInquiryStatus.FAILED,
    "REVERSED": PaymentInquiryStatus.REVERSED,
}

# code → (error_code, message, category, retryable)
_ZARINPAL_ERROR_MAP: dict[int, tuple[str, str, ProviderErrorCategory, bool]] = {
    -9:  ("validation_error",   "Validation error: invalid request fields.",                ProviderErrorCategory.REJECTED,          False),
    -10: ("terminal_invalid",   "Terminal is invalid or deactivated.",                       ProviderErrorCategory.CONFIGURATION,     False),
    -11: ("merchant_invalid",   "Merchant ID is invalid or inactive.",                       ProviderErrorCategory.CONFIGURATION,     False),
    -12: ("rate_limited",       "Too many payment attempts — try again later.",              ProviderErrorCategory.REJECTED,          True),
    -14: ("callback_mismatch",  "Callback URL domain does not match merchant config.",       ProviderErrorCategory.CONFIGURATION,     False),
    -15: ("terminal_suspended", "Terminal is suspended.",                                     ProviderErrorCategory.CONFIGURATION,     False),
    -16: ("access_level",       "Terminal access level is insufficient.",                     ProviderErrorCategory.CONFIGURATION,     False),
    -21: ("no_financial_op",    "No financial operation found for this transaction.",         ProviderErrorCategory.NOT_FOUND,         False),
    -22: ("transaction_failed", "Transaction was unsuccessful.",                              ProviderErrorCategory.REJECTED,          False),
    -33: ("amount_mismatch",    "Transaction amount does not match the paid amount.",         ProviderErrorCategory.AMOUNT_MISMATCH,   False),
    -34: ("split_error",        "Transaction split amount exceeds the total.",                ProviderErrorCategory.REJECTED,          False),
    -40: ("access_denied",      "Access to this method is denied.",                           ProviderErrorCategory.CONFIGURATION,     False),
    -50: ("amount_mismatch",    "Amount mismatch between request and verification.",          ProviderErrorCategory.AMOUNT_MISMATCH,   False),
    -51: ("payment_failed",     "Payment was unsuccessful or session expired.",               ProviderErrorCategory.REJECTED,          False),
    -52: ("payment_error",      "Payment error occurred during processing.",                  ProviderErrorCategory.REJECTED,          True),
    -53: ("authority_mismatch", "Authority belongs to another merchant.",                     ProviderErrorCategory.NOT_FOUND,         False),
    -54: ("invalid_authority",  "Invalid or expired authority.",                              ProviderErrorCategory.NOT_FOUND,         False),
    -55: ("not_found",          "Requested resource not found.",                              ProviderErrorCategory.NOT_FOUND,         False),
    -60: ("reversal_error",     "Unable to reverse the payment.",                             ProviderErrorCategory.REJECTED,          False),
    -61: ("already_reversed",   "Transaction already reversed.",                              ProviderErrorCategory.REJECTED,          False),
    -62: ("reversal_error",     "Reversal failed: insufficient balance.",                     ProviderErrorCategory.REJECTED,          False),
    -63: ("reversal_error",     "Reversal time window has expired.",                          ProviderErrorCategory.REJECTED,          False),
}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ZarinpalProvider(PaymentProvider):
    """Zarinpal REST API v4 — sends Rial amounts, normalises every response."""

    _PRODUCTION_BASE = "https://payment.zarinpal.com"
    _SANDBOX_BASE = "https://sandbox.zarinpal.com"

    def __init__(self, *, merchant_id: str, runtime_options: ProviderRuntimeOptions | None = None):
        self._merchant_id = (merchant_id or "").strip()
        self._options = runtime_options or ProviderRuntimeOptions()
        base = self._SANDBOX_BASE if self._options.sandbox else self._PRODUCTION_BASE
        self._request_url = f"{base}/pg/v4/payment/request.json"
        self._verify_url = f"{base}/pg/v4/payment/verify.json"
        self._inquiry_url = f"{base}/pg/v4/payment/inquiry.json"
        self._reverse_url = f"{base}/pg/v4/payment/reverse.json"
        self._redirect_base = f"{base}/pg/StartPay"

    # ── PaymentProvider contract ─────────────────────────────────────────

    @property
    def name(self) -> str:
        return "zarinpal"

    def create_payment(self, request: CreatePaymentRequest) -> PaymentRequestResult:
        configuration_error = self._configuration_error()
        if configuration_error:
            return PaymentRequestResult(PaymentRequestStatus.ERROR, error=configuration_error)
        if request.amount_rial < _MINIMUM_AMOUNT_RIAL:
            return PaymentRequestResult(
                PaymentRequestStatus.REJECTED,
                error=self._minimum_amount_error(),
            )

        payload = {
            "merchant_id": self._merchant_id,
            "amount": request.amount_rial,
            "currency": "IRR",
            "description": request.description,
            "callback_url": request.callback_url,
            "metadata": {},
        }
        if request.customer:
            if request.customer.mobile:
                payload["metadata"]["mobile"] = request.customer.mobile
            if request.customer.email:
                payload["metadata"]["email"] = request.customer.email
        if request.client_reference:
            payload["metadata"]["order_id"] = request.client_reference

        body, transport_error = self._post(self._request_url, payload)
        if transport_error:
            return PaymentRequestResult(PaymentRequestStatus.ERROR, error=transport_error)

        data, code, provider_message = self._response_parts(body)

        if code == _CODE_SUCCESS:
            authority = data.get("authority")
            if not isinstance(authority, str) or not authority.strip():
                return PaymentRequestResult(
                    PaymentRequestStatus.ERROR,
                    error=self._malformed_error("Zarinpal omitted the payment authority."),
                    sanitized_provider_data=sanitize_provider_data(body),
                )
            return PaymentRequestResult(
                PaymentRequestStatus.CREATED,
                authority=authority.strip(),
                sanitized_provider_data=sanitize_provider_data(body),
            )

        return PaymentRequestResult(
            PaymentRequestStatus.REJECTED,
            error=self._normalize_error(
                code, "Payment request rejected by Zarinpal.", provider_message,
            ),
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def generate_redirect_url(self, authority: str) -> str:
        if not isinstance(authority, str) or not authority.strip():
            raise ValueError("Zarinpal authority must be a non-empty string.")
        return f"{self._redirect_base}/{quote(authority.strip(), safe='')}"

    def verify_payment(self, request: VerificationRequest) -> VerificationResult:
        configuration_error = self._configuration_error()
        if configuration_error:
            return VerificationResult(VerificationStatus.ERROR, error=configuration_error)

        payload = {
            "merchant_id": self._merchant_id,
            "amount": request.expected_amount_rial,
            "authority": request.authority,
        }

        body, transport_error = self._post(self._verify_url, payload)
        if transport_error:
            return VerificationResult(VerificationStatus.ERROR, error=transport_error)

        data, code, provider_message = self._response_parts(body)
        ref_id = str(data["ref_id"]) if data.get("ref_id") is not None else None

        if code in {_CODE_SUCCESS, _CODE_ALREADY_VERIFIED}:
            if not ref_id:
                return VerificationResult(
                    VerificationStatus.ERROR,
                    error=self._malformed_error("Zarinpal omitted the transaction reference."),
                    sanitized_provider_data=sanitize_provider_data(body),
                )
            return VerificationResult(
                VerificationStatus.VERIFIED if code == _CODE_SUCCESS else VerificationStatus.ALREADY_VERIFIED,
                transaction_reference=ref_id,
                verified_amount_rial=request.expected_amount_rial,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        error = self._normalize_error(code, "Payment verification failed.", provider_message)
        if error.category == ProviderErrorCategory.AMOUNT_MISMATCH:
            status = VerificationStatus.AMOUNT_MISMATCH
        elif error.category == ProviderErrorCategory.NOT_FOUND:
            status = VerificationStatus.NOT_FOUND
        else:
            status = VerificationStatus.NOT_VERIFIED
        return VerificationResult(
            status, error=error,
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def inquire_payment(self, request: InquiryRequest) -> InquiryResult:
        configuration_error = self._configuration_error()
        if configuration_error:
            return InquiryResult(PaymentInquiryStatus.ERROR, error=configuration_error)

        payload = {
            "merchant_id": self._merchant_id,
            "authority": request.authority,
        }

        body, transport_error = self._post(self._inquiry_url, payload)
        if transport_error:
            return InquiryResult(PaymentInquiryStatus.ERROR, error=transport_error)

        data, code, provider_message = self._response_parts(body)
        ref_id = str(data["ref_id"]) if data.get("ref_id") is not None else None
        amount = self._positive_int(data.get("amount"))

        if code in (_CODE_SUCCESS, _CODE_ALREADY_VERIFIED):
            if request.expected_amount_rial is not None and amount is not None and amount != request.expected_amount_rial:
                error = NormalizedProviderError(
                    "amount_mismatch",
                    "Inquired amount does not match the expected payment amount.",
                    ProviderErrorCategory.AMOUNT_MISMATCH,
                    provider_code=code,
                )
                return InquiryResult(
                    PaymentInquiryStatus.FAILED,
                    transaction_reference=ref_id,
                    amount_rial=amount,
                    error=error,
                    sanitized_provider_data=sanitize_provider_data(body),
                )
            provider_status = str(data.get("status") or "").strip().upper()
            return InquiryResult(
                _INQUIRY_STATUS_MAP.get(provider_status, PaymentInquiryStatus.UNKNOWN),
                transaction_reference=ref_id,
                amount_rial=amount,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        error = self._normalize_error(code, "Zarinpal inquiry failed.", provider_message)
        if error.category == ProviderErrorCategory.NOT_FOUND:
            return InquiryResult(
                PaymentInquiryStatus.NOT_FOUND, error=error,
                sanitized_provider_data=sanitize_provider_data(body),
            )
        return InquiryResult(
            PaymentInquiryStatus.FAILED,
            amount_rial=amount,
            error=error,
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def reverse_payment(self, request: ReversalRequest) -> ReversalResult:
        configuration_error = self._configuration_error()
        if configuration_error:
            return ReversalResult(ReversalStatus.ERROR, error=configuration_error)

        payload = {
            "merchant_id": self._merchant_id,
            "authority": request.authority,
        }

        body, transport_error = self._post(self._reverse_url, payload)
        if transport_error:
            return ReversalResult(ReversalStatus.ERROR, error=transport_error)

        _data, code, provider_message = self._response_parts(body)

        if code == _CODE_SUCCESS:
            return ReversalResult(
                ReversalStatus.REVERSED,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        if code == -61:
            return ReversalResult(
                ReversalStatus.ALREADY_REVERSED,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        return ReversalResult(
            ReversalStatus.ERROR,
            error=self._normalize_error(code, "Zarinpal reversal failed.", provider_message),
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def validate_configuration(self):
        errors = []
        if not self._merchant_id:
            errors.append("Zarinpal merchant ID is not set.")
        elif not _UUID_RE.match(self._merchant_id):
            errors.append("Zarinpal merchant ID is not a valid UUID.")
        if errors:
            return ConfigurationValidationResult(False, errors=tuple(errors))
        return ConfigurationValidationResult(True)

    def check_health(self):
        config = self.validate_configuration()
        if not config.valid:
            return ProviderHealthResult(
                ProviderHealthStatus.UNAVAILABLE,
                message="Zarinpal is not configured.",
                error=NormalizedProviderError(
                    "configuration_error", "; ".join(config.errors),
                    ProviderErrorCategory.CONFIGURATION,
                ),
            )
        return ProviderHealthResult(ProviderHealthStatus.HEALTHY, message="Zarinpal is configured.")

    # ── Internal helpers ─────────────────────────────────────────────────

    def _timeout(self):
        return (self._options.connect_timeout_seconds, self._options.read_timeout_seconds)

    def _configuration_error(self):
        configuration = self.validate_configuration()
        if configuration.valid:
            return None
        return NormalizedProviderError(
            "configuration_error",
            "; ".join(configuration.errors),
            ProviderErrorCategory.CONFIGURATION,
        )

    @staticmethod
    def _minimum_amount_error():
        return NormalizedProviderError(
            "amount_below_minimum",
            f"Zarinpal requires an amount of at least {_MINIMUM_AMOUNT_RIAL} Rials.",
            ProviderErrorCategory.REJECTED,
        )

    def _post(self, url, payload):
        """POST JSON and return ``(body, error)`` without shared mutable state."""
        try:
            resp = requests.post(
                url, json=payload,
                headers={
                    "User-Agent": "EM-Backend-Zarinpal/1.0",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=self._timeout(),
            )
        except requests.Timeout:
            err = NormalizedProviderError(
                "provider_timeout", "Zarinpal request timed out.",
                ProviderErrorCategory.TIMEOUT, retryable=True,
            )
        except requests.RequestException:
            err = NormalizedProviderError(
                "provider_network_error", "Network error communicating with Zarinpal.",
                ProviderErrorCategory.NETWORK, retryable=True,
            )
        else:
            try:
                body = resp.json()
            except (ValueError, KeyError):
                err = self._malformed_error("Zarinpal returned invalid JSON.")
            else:
                if not isinstance(body, Mapping):
                    err = self._malformed_error("Zarinpal returned a non-object JSON response.")
                else:
                    _data, provider_code, _message = self._response_parts(body)
                    status_code = getattr(resp, "status_code", 200)
                    if isinstance(status_code, int) and status_code >= 400 and provider_code is None:
                        retryable = status_code == 429 or status_code >= 500
                        err = NormalizedProviderError(
                            "provider_http_error",
                            f"Zarinpal returned HTTP {status_code}.",
                            ProviderErrorCategory.NETWORK if retryable else ProviderErrorCategory.REJECTED,
                            retryable=retryable,
                            provider_code=status_code,
                        )
                    else:
                        return body, None

        logger.warning(
            "zarinpal.transport_error url=%s error=%s", url, err.code,
            extra={"payment_intent_id": "", "payment_attempt_id": ""},
        )
        return None, err

    @staticmethod
    def _malformed_error(message):
        return NormalizedProviderError(
            "malformed_response", message,
            ProviderErrorCategory.MALFORMED_RESPONSE,
        )

    @staticmethod
    def _positive_int(value):
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _response_parts(cls, body):
        data = body.get("data")
        data = data if isinstance(data, Mapping) else {}
        errors = body.get("errors")
        error_data = errors if isinstance(errors, Mapping) else {}

        code = data.get("code")
        message = data.get("message")
        if code is None:
            code = error_data.get("code")
            message = error_data.get("message") or message

        if isinstance(code, str):
            try:
                code = int(code)
            except ValueError:
                pass
        return data, code, str(message) if message else None

    @staticmethod
    def _normalize_error(code, fallback_message="Zarinpal returned an error.", provider_message=None):
        if isinstance(code, str):
            try:
                code = int(code)
            except ValueError:
                pass
        if code in _ZARINPAL_ERROR_MAP:
            err_code, message, category, retryable = _ZARINPAL_ERROR_MAP[code]
            return NormalizedProviderError(
                err_code, message, category, retryable=retryable, provider_code=code,
            )
        detail = provider_message or fallback_message
        return NormalizedProviderError(
            "unknown_zarinpal_error",
            f"{detail} (code={code})",
            ProviderErrorCategory.UNKNOWN,
            provider_code=code,
        )
