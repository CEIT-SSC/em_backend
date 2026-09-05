"""Immutable, provider-neutral request and result values."""

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlparse


class ProviderContractError(ValueError):
    pass


class ProviderErrorCategory(str, Enum):
    CONFIGURATION = "configuration"
    TIMEOUT = "timeout"
    NETWORK = "network"
    MALFORMED_RESPONSE = "malformed_response"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    AMOUNT_MISMATCH = "amount_mismatch"
    NOT_SUPPORTED = "not_supported"
    UNKNOWN = "unknown"


class PaymentRequestStatus(str, Enum):
    CREATED = "created"
    REJECTED = "rejected"
    ERROR = "error"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    ALREADY_VERIFIED = "already_verified"
    NOT_VERIFIED = "not_verified"
    AMOUNT_MISMATCH = "amount_mismatch"
    NOT_FOUND = "not_found"
    ERROR = "error"


class PaymentInquiryStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    REVERSED = "reversed"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"
    ERROR = "error"


class ReversalStatus(str, Enum):
    REVERSED = "reversed"
    ALREADY_REVERSED = "already_reversed"
    NOT_REVERSIBLE = "not_reversible"
    NOT_FOUND = "not_found"
    ERROR = "error"


class ProviderHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


def _require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ProviderContractError(f"{name} must be a non-empty string.")
    return value.strip()


def _require_rial_amount(value, name, *, optional=False):
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderContractError(f"{name} must be an integer Rial amount.")
    if value <= 0:
        raise ProviderContractError(f"{name} must be greater than zero.")
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ProviderRuntimeOptions:
    sandbox: bool = False
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 10.0

    def __post_init__(self):
        for name in ("connect_timeout_seconds", "read_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ProviderContractError(f"{name} must be a positive number.")


@dataclass(frozen=True)
class CustomerData:
    email: str | None = None
    mobile: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "email", self.email.strip() if self.email and self.email.strip() else None)
        object.__setattr__(self, "mobile", self.mobile.strip() if self.mobile and self.mobile.strip() else None)


@dataclass(frozen=True)
class CreatePaymentRequest:
    amount_rial: int
    callback_url: str
    description: str
    client_reference: str
    customer: CustomerData | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        _require_rial_amount(self.amount_rial, "amount_rial")
        parsed = urlparse(self.callback_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderContractError("callback_url must be an absolute HTTP(S) URL.")
        _require_text(self.description, "description")
        _require_text(self.client_reference, "client_reference")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class VerificationRequest:
    authority: str
    expected_amount_rial: int

    def __post_init__(self):
        _require_text(self.authority, "authority")
        _require_rial_amount(self.expected_amount_rial, "expected_amount_rial")


@dataclass(frozen=True)
class InquiryRequest:
    authority: str
    expected_amount_rial: int | None = None

    def __post_init__(self):
        _require_text(self.authority, "authority")
        _require_rial_amount(self.expected_amount_rial, "expected_amount_rial", optional=True)


@dataclass(frozen=True)
class ReversalRequest:
    authority: str
    expected_amount_rial: int
    transaction_reference: str | None = None

    def __post_init__(self):
        _require_text(self.authority, "authority")
        _require_rial_amount(self.expected_amount_rial, "expected_amount_rial")
        if self.transaction_reference is not None:
            _require_text(self.transaction_reference, "transaction_reference")


@dataclass(frozen=True)
class NormalizedProviderError:
    code: str
    message: str
    category: ProviderErrorCategory
    retryable: bool = False
    provider_code: str | int | None = None

    def __post_init__(self):
        _require_text(self.code, "error.code")
        _require_text(self.message, "error.message")
        if not isinstance(self.category, ProviderErrorCategory):
            raise ProviderContractError("error.category must be a ProviderErrorCategory.")


@dataclass(frozen=True)
class PaymentRequestResult:
    status: PaymentRequestStatus
    authority: str | None = None
    error: NormalizedProviderError | None = None
    sanitized_provider_data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        if self.status == PaymentRequestStatus.CREATED:
            _require_text(self.authority, "authority")
        elif self.error is None:
            raise ProviderContractError("An unsuccessful payment request must include an error.")
        object.__setattr__(self, "sanitized_provider_data", _freeze(self.sanitized_provider_data))


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    transaction_reference: str | None = None
    verified_amount_rial: int | None = None
    error: NormalizedProviderError | None = None
    sanitized_provider_data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        if self.transaction_reference is not None:
            _require_text(self.transaction_reference, "transaction_reference")
        _require_rial_amount(self.verified_amount_rial, "verified_amount_rial", optional=True)
        if self.status not in {VerificationStatus.VERIFIED, VerificationStatus.ALREADY_VERIFIED} and self.error is None:
            raise ProviderContractError("An unsuccessful verification must include an error.")
        object.__setattr__(self, "sanitized_provider_data", _freeze(self.sanitized_provider_data))


@dataclass(frozen=True)
class InquiryResult:
    status: PaymentInquiryStatus
    transaction_reference: str | None = None
    amount_rial: int | None = None
    error: NormalizedProviderError | None = None
    sanitized_provider_data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        if self.transaction_reference is not None:
            _require_text(self.transaction_reference, "transaction_reference")
        _require_rial_amount(self.amount_rial, "amount_rial", optional=True)
        if self.status in {PaymentInquiryStatus.NOT_FOUND, PaymentInquiryStatus.ERROR} and self.error is None:
            raise ProviderContractError("A failed inquiry must include an error.")
        object.__setattr__(self, "sanitized_provider_data", _freeze(self.sanitized_provider_data))


@dataclass(frozen=True)
class ReversalResult:
    status: ReversalStatus
    transaction_reference: str | None = None
    error: NormalizedProviderError | None = None
    sanitized_provider_data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        if self.transaction_reference is not None:
            _require_text(self.transaction_reference, "transaction_reference")
        if self.status not in {ReversalStatus.REVERSED, ReversalStatus.ALREADY_REVERSED} and self.error is None:
            raise ProviderContractError("An unsuccessful reversal must include an error.")
        object.__setattr__(self, "sanitized_provider_data", _freeze(self.sanitized_provider_data))


@dataclass(frozen=True)
class ConfigurationValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.valid and not self.errors:
            raise ProviderContractError("An invalid configuration must include at least one error.")


@dataclass(frozen=True)
class ProviderHealthResult:
    status: ProviderHealthStatus
    message: str = ""
    error: NormalizedProviderError | None = None
