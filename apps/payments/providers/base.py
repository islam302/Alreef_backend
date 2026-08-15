"""
Payment provider abstraction (spec §41). Every Egyptian gateway (Paymob,
Fawry, valU, ...) plugs in by implementing this interface; Order/checkout
code only ever talks to `PaymentProvider`, never a specific vendor SDK.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CreatePaymentResult:
    success: bool
    provider_reference: str = ""
    status: str = "pending"
    redirect_url: str = ""
    iframe_url: str = ""
    metadata: dict = field(default_factory=dict)
    raw: Any = None
    error: str = ""


@dataclass
class CaptureResult:
    success: bool
    provider_transaction_id: str = ""
    raw: Any = None
    error: str = ""


@dataclass
class RefundResult:
    success: bool
    provider_refund_id: str = ""
    raw: Any = None
    error: str = ""


@dataclass
class StatusResult:
    status: str
    raw: Any = None


class PaymentProvider(ABC):
    code: str = "base"

    @abstractmethod
    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError

    @abstractmethod
    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError

    @abstractmethod
    def capture(self, *, payment, amount=None) -> CaptureResult:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, *, payment) -> bool:
        raise NotImplementedError

    @abstractmethod
    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        raise NotImplementedError

    @abstractmethod
    def get_status(self, *, payment) -> StatusResult:
        raise NotImplementedError

    @abstractmethod
    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        """Returns {"event_id": str, "event_type": str, "provider_reference": str,
        "status": str, "payload": dict, "amount": str | Decimal | None}.
        Include "amount" whenever the gateway's payload states one — services.process_webhook
        rejects the webhook if it disagrees with the server's authoritative payment.amount."""
        raise NotImplementedError
