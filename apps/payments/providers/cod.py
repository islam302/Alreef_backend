"""Cash on delivery — a first-class payment method (spec §43). No external
API calls: the "capture" happens physically when the courier collects cash,
triggered by staff marking the order delivered."""
import uuid

from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class CODPaymentProvider(PaymentProvider):
    code = "cod"

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        return CreatePaymentResult(
            success=True,
            provider_reference=f"COD-{uuid.uuid4().hex[:12].upper()}",
            status="pending",
            metadata={"instructions": "Pay in cash to the courier upon delivery."},
        )

    def authorize(self, *, payment) -> CaptureResult:
        return CaptureResult(success=True, provider_transaction_id=payment.provider_reference)

    def capture(self, *, payment, amount=None) -> CaptureResult:
        """Called when the order is marked DELIVERED — cash was collected."""
        return CaptureResult(success=True, provider_transaction_id=payment.provider_reference)

    def cancel(self, *, payment) -> bool:
        return True

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        # COD refunds are manual (cash handback / store credit) — recorded
        # for audit but never auto-processed against a gateway.
        return RefundResult(success=True, provider_refund_id=f"COD-REFUND-{uuid.uuid4().hex[:10].upper()}")

    def get_status(self, *, payment) -> StatusResult:
        return StatusResult(status=payment.status)

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        return False  # COD has no webhooks.

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("COD does not receive webhooks.")
