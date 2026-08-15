"""
valU BNPL adapter skeleton. Requires a merchant agreement and API
credentials from valU directly — no public sandbox is documented here.
Interface shape only; do not enable without verified official docs.
Sympl and Aman (also referenced in spec §40) follow the same pattern and
should be added as sibling modules once their official APIs are verified.
"""
from django.conf import settings

from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class ValuPaymentProvider(PaymentProvider):
    code = "valu"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["VALU"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "valU is not enabled. Set VALU_ENABLED=True plus API_KEY once you have verified valU's "
                "official API documentation and merchant credentials."
            )

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError("valU create_payment requires verified API integration with real credentials.")

    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError("valU authorize requires verified API integration with real credentials.")

    def capture(self, *, payment, amount=None) -> CaptureResult:
        raise NotImplementedError("valU capture requires verified API integration with real credentials.")

    def cancel(self, *, payment) -> bool:
        raise NotImplementedError("valU cancel requires verified API integration with real credentials.")

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        raise NotImplementedError("valU refund requires verified API integration with real credentials.")

    def get_status(self, *, payment) -> StatusResult:
        raise NotImplementedError("valU get_status requires verified API integration with real credentials.")

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        raise NotImplementedError("valU webhook signature verification requires the official specification.")

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("valU webhook parsing requires verified API integration.")
