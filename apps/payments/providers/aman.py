"""
Aman (installments) adapter skeleton. Requires a merchant agreement and API
credentials from Aman directly. Interface shape only — do not enable
without verified official docs. Mirrors apps/payments/providers/valu.py.
"""
from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class AmanPaymentProvider(PaymentProvider):
    code = "aman"

    def __init__(self):
        raise NotImplementedError(
            "Aman is not configured. This adapter is a structural placeholder — wire it up against "
            "Aman's official API documentation and real merchant credentials before enabling it."
        )

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError("Aman create_payment requires verified API integration with real credentials.")

    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError("Aman authorize requires verified API integration with real credentials.")

    def capture(self, *, payment, amount=None) -> CaptureResult:
        raise NotImplementedError("Aman capture requires verified API integration with real credentials.")

    def cancel(self, *, payment) -> bool:
        raise NotImplementedError("Aman cancel requires verified API integration with real credentials.")

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        raise NotImplementedError("Aman refund requires verified API integration with real credentials.")

    def get_status(self, *, payment) -> StatusResult:
        raise NotImplementedError("Aman get_status requires verified API integration with real credentials.")

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        raise NotImplementedError("Aman webhook signature verification requires the official specification.")

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("Aman webhook parsing requires verified API integration.")
