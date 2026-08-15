"""
Sympl BNPL adapter skeleton. Requires a merchant agreement and API
credentials from Sympl directly. Interface shape only — do not enable
without verified official docs. Mirrors apps/payments/providers/valu.py.
"""
from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class SymplPaymentProvider(PaymentProvider):
    code = "sympl"

    def __init__(self):
        raise NotImplementedError(
            "Sympl is not configured. This adapter is a structural placeholder — wire it up against "
            "Sympl's official API documentation and real merchant credentials before enabling it."
        )

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError("Sympl create_payment requires verified API integration with real credentials.")

    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError("Sympl authorize requires verified API integration with real credentials.")

    def capture(self, *, payment, amount=None) -> CaptureResult:
        raise NotImplementedError("Sympl capture requires verified API integration with real credentials.")

    def cancel(self, *, payment) -> bool:
        raise NotImplementedError("Sympl cancel requires verified API integration with real credentials.")

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        raise NotImplementedError("Sympl refund requires verified API integration with real credentials.")

    def get_status(self, *, payment) -> StatusResult:
        raise NotImplementedError("Sympl get_status requires verified API integration with real credentials.")

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        raise NotImplementedError("Sympl webhook signature verification requires the official specification.")

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("Sympl webhook parsing requires verified API integration.")
