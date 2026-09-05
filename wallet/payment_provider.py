"""Zarinpal edge adapter for the provider-independent payment contract."""

from decimal import Decimal

from payment_core.providers import (
    ConfigurationValidationResult,
    InquiryResult,
    NormalizedProviderError,
    PaymentInquiryStatus,
    PaymentProvider,
    PaymentRequestResult,
    PaymentRequestStatus,
    ProviderErrorCategory,
    ProviderHealthResult,
    ProviderHealthStatus,
    ReversalResult,
    ReversalStatus,
    VerificationResult,
    VerificationStatus,
    sanitize_provider_data,
)

from .payments import ZarrinPal


class ZarinpalPaymentProvider(PaymentProvider):
    """Translates legacy Zarinpal responses into payment-core results."""

    name = "zarinpal"

    def __init__(self, client=None):
        self.client = client or ZarrinPal()
        self._redirect_urls = {}

    @staticmethod
    def _toman_amount(amount_rial):
        return Decimal(amount_rial) / Decimal("10")

    @staticmethod
    def _error(code, message, category=ProviderErrorCategory.REJECTED, *, retryable=False):
        return NormalizedProviderError(code, str(message), category, retryable=retryable)

    def create_payment(self, request):
        customer = request.customer
        result = self.client.create_payment(
            amount=self._toman_amount(request.amount_rial),
            mobile=customer.mobile if customer and customer.mobile else "",
            email=customer.email if customer and customer.email else "",
            order_id=request.client_reference,
            callback_url=request.callback_url,
        )
        if not isinstance(result, dict):
            return PaymentRequestResult(
                PaymentRequestStatus.ERROR,
                error=self._error(
                    "malformed_provider_response", "Zarinpal returned an invalid response.",
                    ProviderErrorCategory.MALFORMED_RESPONSE,
                ),
            )
        if result.get("status") == "success" and result.get("authority") and result.get("link"):
            authority = str(result["authority"])
            self._redirect_urls[authority] = str(result["link"])
            return PaymentRequestResult(
                PaymentRequestStatus.CREATED,
                authority=authority,
                sanitized_provider_data=sanitize_provider_data({"status": result.get("status")}),
            )
        provider_status = result.get("status")
        is_transport_error = provider_status == "error"
        return PaymentRequestResult(
            PaymentRequestStatus.ERROR if is_transport_error else PaymentRequestStatus.REJECTED,
            error=self._error(
                "payment_request_rejected", result.get("error") or "Zarinpal rejected the payment request.",
                ProviderErrorCategory.NETWORK if is_transport_error else ProviderErrorCategory.REJECTED,
                retryable=is_transport_error,
            ),
            sanitized_provider_data=sanitize_provider_data(result),
        )

    def generate_redirect_url(self, authority):
        if authority in self._redirect_urls:
            return self._redirect_urls[authority]
        template = getattr(self.client, "start_pay_url", "")
        if template:
            return template.format(authority=authority)
        raise ValueError("Zarinpal did not provide a redirect URL.")

    def verify_payment(self, request):
        result = self.client.verify_payment(
            authority=request.authority,
            amount=self._toman_amount(request.expected_amount_rial),
        )
        if not isinstance(result, dict):
            return VerificationResult(
                VerificationStatus.ERROR,
                error=self._error(
                    "malformed_provider_response", "Zarinpal returned an invalid verification response.",
                    ProviderErrorCategory.MALFORMED_RESPONSE,
                ),
            )
        if result.get("status") == "success":
            reference = result.get("ref_id")
            return VerificationResult(
                VerificationStatus.VERIFIED,
                transaction_reference=str(reference) if reference is not None else None,
                verified_amount_rial=request.expected_amount_rial,
                sanitized_provider_data=sanitize_provider_data({
                    "status": "success",
                    "card_pan": result.get("card_pan"),
                }),
            )
        status = result.get("status")
        category = ProviderErrorCategory.NETWORK if status == "unexpected" else ProviderErrorCategory.REJECTED
        return VerificationResult(
            VerificationStatus.ERROR if status == "unexpected" else VerificationStatus.NOT_VERIFIED,
            error=self._error(
                "payment_verification_failed",
                result.get("error") or "Zarinpal could not verify the payment.",
                category,
                retryable=status == "unexpected",
            ),
            sanitized_provider_data=sanitize_provider_data(result),
        )

    def inquire_payment(self, request):
        return InquiryResult(
            PaymentInquiryStatus.ERROR,
            error=self._error(
                "inquiry_not_supported", "Zarinpal inquiry is not configured.",
                ProviderErrorCategory.NOT_SUPPORTED,
            ),
        )

    def reverse_payment(self, request):
        return ReversalResult(
            ReversalStatus.NOT_REVERSIBLE,
            error=self._error(
                "reversal_not_supported", "Zarinpal reversal is not configured.",
                ProviderErrorCategory.NOT_SUPPORTED,
            ),
        )

    def validate_configuration(self):
        if getattr(self.client, "merchant_id", ""):
            return ConfigurationValidationResult(True)
        return ConfigurationValidationResult(False, errors=("Zarinpal merchant ID is missing.",))

    def check_health(self):
        return ProviderHealthResult(ProviderHealthStatus.HEALTHY)
