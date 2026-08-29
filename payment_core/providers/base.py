from abc import ABC, abstractmethod

from .types import (
    ConfigurationValidationResult,
    CreatePaymentRequest,
    InquiryRequest,
    InquiryResult,
    PaymentRequestResult,
    ProviderHealthResult,
    ReversalRequest,
    ReversalResult,
    VerificationRequest,
    VerificationResult,
)


class PaymentProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def create_payment(self, request: CreatePaymentRequest) -> PaymentRequestResult:
        raise NotImplementedError

    @abstractmethod
    def generate_redirect_url(self, authority: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify_payment(self, request: VerificationRequest) -> VerificationResult:
        raise NotImplementedError

    @abstractmethod
    def inquire_payment(self, request: InquiryRequest) -> InquiryResult:
        raise NotImplementedError

    @abstractmethod
    def reverse_payment(self, request: ReversalRequest) -> ReversalResult:
        raise NotImplementedError

    @abstractmethod
    def validate_configuration(self) -> ConfigurationValidationResult:
        raise NotImplementedError

    @abstractmethod
    def check_health(self) -> ProviderHealthResult:
        raise NotImplementedError
