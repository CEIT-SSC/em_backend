"""Production-grade Zarinpal payment provider — REST API v4 with Rial amounts."""

import logging
import re

import requests

from .base import PaymentProvider
from .redaction import sanitize_provider_data
from .types import (
    ConfigurationValidationResult,
    InquiryResult,
    NormalizedProviderError,
    PaymentInquiryStatus,
    PaymentRequestResult,
    PaymentRequestStatus,
    ProviderErrorCategory,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderRuntimeOptions,
    ReversalResult,
    ReversalStatus,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger("payments")

# ── Zarinpal response codes ──────────────────────────────────────────────────
_CODE_SUCCESS = 100
_CODE_ALREADY_VERIFIED = 101

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

    def create_payment(self, request):
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

        body = self._post(self._request_url, payload)
        if body is None:
            return self._last_transport_result

        data = body.get("data") or {}
        code = data.get("code")

        if code == _CODE_SUCCESS and data.get("authority"):
            authority = str(data["authority"])
            return PaymentRequestResult(
                PaymentRequestStatus.CREATED,
                authority=authority,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        return PaymentRequestResult(
            PaymentRequestStatus.REJECTED,
            error=self._normalize_error(code, "Payment request rejected by Zarinpal."),
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def generate_redirect_url(self, authority: str) -> str:
        return f"{self._redirect_base}/{authority}"

    def verify_payment(self, request):
        payload = {
            "merchant_id": self._merchant_id,
            "amount": request.expected_amount_rial,
            "authority": request.authority,
        }

        body = self._post(self._verify_url, payload)
        if body is None:
            return self._last_transport_result

        data = body.get("data") or {}
        code = data.get("code")
        ref_id = str(data["ref_id"]) if data.get("ref_id") is not None else None

        if code == _CODE_SUCCESS:
            return VerificationResult(
                VerificationStatus.VERIFIED,
                transaction_reference=ref_id,
                verified_amount_rial=request.expected_amount_rial,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        if code == _CODE_ALREADY_VERIFIED:
            return VerificationResult(
                VerificationStatus.ALREADY_VERIFIED,
                transaction_reference=ref_id,
                verified_amount_rial=request.expected_amount_rial,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        error = self._normalize_error(code, "Payment verification failed.")
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

    def inquire_payment(self, request):
        payload = {
            "merchant_id": self._merchant_id,
            "authority": request.authority,
        }

        body = self._post(self._inquiry_url, payload)
        if body is None:
            return self._last_transport_result

        data = body.get("data") or {}
        code = data.get("code")
        ref_id = str(data["ref_id"]) if data.get("ref_id") is not None else None
        raw_amount = data.get("amount")
        amount = int(raw_amount) if raw_amount is not None and str(raw_amount).isdigit() else None

        if code in (_CODE_SUCCESS, _CODE_ALREADY_VERIFIED):
            return InquiryResult(
                PaymentInquiryStatus.VERIFIED,
                transaction_reference=ref_id,
                amount_rial=amount,
                sanitized_provider_data=sanitize_provider_data(body),
            )

        error = self._normalize_error(code, "Zarinpal inquiry failed.")
        if error.category == ProviderErrorCategory.NOT_FOUND:
            return InquiryResult(
                PaymentInquiryStatus.NOT_FOUND, error=error,
                sanitized_provider_data=sanitize_provider_data(body),
            )
        return InquiryResult(
            PaymentInquiryStatus.FAILED,
            amount_rial=amount,
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def reverse_payment(self, request):
        payload = {
            "merchant_id": self._merchant_id,
            "authority": request.authority,
        }

        body = self._post(self._reverse_url, payload)
        if body is None:
            return self._last_transport_result

        data = body.get("data") or {}
        code = data.get("code")

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
            error=self._normalize_error(code, "Zarinpal reversal failed."),
            sanitized_provider_data=sanitize_provider_data(body),
        )

    def validate_configuration(self):
        errors = []
        if not self._merchant_id:
            errors.append("Zarinpal merchant ID is not set.")
        elif not _UUID_RE.match(self._merchant_id):
            errors.append(f"Zarinpal merchant ID is not a valid UUID.")
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

    def _post(self, url, payload):
        """POST JSON to Zarinpal, return parsed body or None on transport error.

        On transport failure ``self._last_transport_result`` is set to a
        pre-built result object the caller can return directly.
        """
        self._last_transport_result = None
        try:
            resp = requests.post(
                url, json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=self._timeout(),
            )
            return resp.json()
        except requests.Timeout:
            err = NormalizedProviderError(
                "provider_timeout", "Zarinpal request timed out.",
                ProviderErrorCategory.TIMEOUT, retryable=True,
            )
        except requests.RequestException as exc:
            err = NormalizedProviderError(
                "provider_network_error", f"Network error communicating with Zarinpal: {exc}",
                ProviderErrorCategory.NETWORK, retryable=True,
            )
        except (ValueError, KeyError):
            err = NormalizedProviderError(
                "malformed_response", "Zarinpal returned invalid JSON.",
                ProviderErrorCategory.MALFORMED_RESPONSE,
            )
        # Build a generic error result that the caller's return type can use.
        # The caller inspects ``self._last_transport_result`` when we return None.
        self._last_transport_result = self._transport_error_result(url, err)
        logger.warning(
            "zarinpal.transport_error url=%s error=%s", url, err.code,
            extra={"payment_intent_id": "", "payment_attempt_id": ""},
        )
        return None

    def _transport_error_result(self, url, error):
        """Return the correct result dataclass for the endpoint that failed."""
        if "request.json" in url:
            return PaymentRequestResult(PaymentRequestStatus.ERROR, error=error)
        if "verify.json" in url:
            return VerificationResult(VerificationStatus.ERROR, error=error)
        if "inquiry.json" in url:
            return InquiryResult(PaymentInquiryStatus.ERROR, error=error)
        if "reverse.json" in url:
            return ReversalResult(ReversalStatus.ERROR, error=error)
        return PaymentRequestResult(PaymentRequestStatus.ERROR, error=error)

    @staticmethod
    def _normalize_error(code, fallback_message="Zarinpal returned an error."):
        if code in _ZARINPAL_ERROR_MAP:
            err_code, message, category, retryable = _ZARINPAL_ERROR_MAP[code]
            return NormalizedProviderError(
                err_code, message, category, retryable=retryable, provider_code=code,
            )
        return NormalizedProviderError(
            "unknown_zarinpal_error",
            f"{fallback_message} (code={code})",
            ProviderErrorCategory.UNKNOWN,
            provider_code=code,
        )
