"""
Paymob (Accept) adapter skeleton. Paymob requires a live merchant account,
API key, and integration IDs (card / wallet) obtained from
https://accept.paymob.com — this module defines the correct shape of the
integration (auth -> order registration -> payment key -> iframe/redirect,
HMAC webhook verification) but intentionally does NOT fabricate request/
response field names. Wire the real HTTP calls against Paymob's official,
current API reference once real sandbox credentials are available; until
then all methods raise NotImplementedError so nothing pretends to work.
"""
from django.conf import settings

from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class PaymobPaymentProvider(PaymentProvider):
    code = "paymob"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["PAYMOB"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "Paymob is not enabled. Set PAYMOB_ENABLED=True plus API_KEY/INTEGRATION_ID/HMAC_SECRET "
                "once you have verified Paymob's current official API documentation and sandbox credentials."
            )

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError("Paymob create_payment requires verified API integration with real credentials.")

    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError("Paymob authorize requires verified API integration with real credentials.")

    def capture(self, *, payment, amount=None) -> CaptureResult:
        raise NotImplementedError("Paymob capture requires verified API integration with real credentials.")

    def cancel(self, *, payment) -> bool:
        raise NotImplementedError("Paymob cancel requires verified API integration with real credentials.")

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        raise NotImplementedError("Paymob refund requires verified API integration with real credentials.")

    def get_status(self, *, payment) -> StatusResult:
        raise NotImplementedError("Paymob get_status requires verified API integration with real credentials.")

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        """
        Paymob signs callbacks with an HMAC over a documented, ordered set of
        transaction fields (see Paymob's official 'HMAC Calculation' guide).
        Implement the exact field order from that guide before enabling —
        an incorrect order silently accepts forged webhooks.
        """
        raise NotImplementedError("Paymob HMAC verification requires the official field-order specification.")

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("Paymob webhook parsing requires verified API integration.")
