"""
FawryPay adapter skeleton. Requires merchant onboarding (FAWRY_MERCHANT_CODE,
FAWRY_SECURITY_KEY) via https://developer.fawrystaging.com. Implements the
provider interface shape only — real request signing / charge-request
payloads must be wired against Fawry's current official API reference once
sandbox credentials are available.
"""
from django.conf import settings

from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class FawryPaymentProvider(PaymentProvider):
    code = "fawry"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["FAWRY"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "Fawry is not enabled. Set FAWRY_ENABLED=True plus MERCHANT_CODE/SECURITY_KEY once you have "
                "verified Fawry's current official API documentation and sandbox credentials."
            )

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError("Fawry create_payment requires verified API integration with real credentials.")

    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError("Fawry authorize requires verified API integration with real credentials.")

    def capture(self, *, payment, amount=None) -> CaptureResult:
        raise NotImplementedError("Fawry capture requires verified API integration with real credentials.")

    def cancel(self, *, payment) -> bool:
        raise NotImplementedError("Fawry cancel requires verified API integration with real credentials.")

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        raise NotImplementedError("Fawry refund requires verified API integration with real credentials.")

    def get_status(self, *, payment) -> StatusResult:
        raise NotImplementedError("Fawry get_status requires verified API integration with real credentials.")

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        raise NotImplementedError("Fawry webhook signature verification requires the official specification.")

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("Fawry webhook parsing requires verified API integration.")
