"""
Cash on delivery.

THE BUG THIS FILE USED TO BE HALF OF. Checkout called
`mark_payment_captured(payment)` immediately after creating a COD order, so
every COD payment was "captured" the moment the customer clicked confirm —
before a courier had been anywhere near them. The books said the money was
ours; it was not. Worse, `captured` is the state a refund is issued from, so a
cancelled COD order could be "refunded" cash nobody had ever handed over.

COD now behaves like what it is: a promise to pay later. The payment stays
`pending` from checkout until somebody records that the courier collected, and
the order is confirmed independently of the payment (reserved stock is still
consumed, the confirmation email still goes out) because a COD order is a real
order before it is a paid one.

There is no external API here and there never will be. The "gateway" is a
person with a bag.
"""
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
            # Not `captured`. Nothing has been collected yet.
            status="pending",
            metadata={
                "instructions_ar": "تدفع قيمة الطلب كاش للمندوب وقت التسليم.",
                "collect_amount": str(payment.amount.amount),
                "currency": str(payment.amount.currency),
            },
        )

    def authorize(self, *, payment) -> CaptureResult:
        # Accepting a COD order *is* the authorization: we have the customer's
        # word and their address. Nothing to call.
        return CaptureResult(success=True, provider_transaction_id=payment.provider_reference)

    def capture(self, *, payment, amount=None) -> CaptureResult:
        """
        The courier collected the cash.

        Reached from `services.record_cod_collection`, which staff call when an
        order is delivered — never automatically from checkout.
        """
        return CaptureResult(success=True, provider_transaction_id=payment.provider_reference)

    def cancel(self, *, payment) -> bool:
        # Nobody has paid, so there is nothing to undo.
        return True

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        """
        Record a refund that a human performs.

        Returns success because the *record* succeeded — the cash handback or
        store credit is the staff member's job, and the dashboard says so
        (`config.refund_available` returns that wording). The MANUAL- prefix
        makes it obvious in reconciliation that no gateway was involved.
        """
        return RefundResult(
            success=True,
            provider_refund_id=f"MANUAL-COD-{uuid.uuid4().hex[:10].upper()}",
            raw={"manual": True, "note": "Cash returned or store credit issued by staff."},
        )

    def get_status(self, *, payment) -> StatusResult:
        # There is nothing to reconcile against. Our record is the only record,
        # so echoing it back is honest rather than lazy: a caller polling this
        # learns nothing new, which is the truth.
        return StatusResult(status=payment.status, raw={"source": "local"})

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        # COD has no webhooks. Returning False means any POST to
        # /payments/webhooks/cod/ is rejected as an invalid signature, which is
        # the correct answer to a request that can only be hostile.
        return False

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("COD does not receive webhooks.")
