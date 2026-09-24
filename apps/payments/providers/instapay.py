"""
InstaPay — a bank transfer the customer makes, and a staff member confirms.

WHY THERE IS NO API CALL HERE, AND WHY THAT IS NOT A SHORTCUT.

InstaPay is Egypt's Instant Payment Network, run by the Egyptian Banks Company
under the Central Bank of Egypt. It moves money between bank accounts through
banking apps. It is **not a payment gateway**, and it does not publish a merchant
acceptance API: InstaPay's own FAQ describes person-to-person and
person-to-merchant transfers made inside the app, with no developer
documentation, no merchant onboarding for online acceptance and no webhooks.
Paymob — the only gateway this project integrates — lists InstaPay as *coming
soon*.

So there are exactly two honest ways to take an InstaPay payment today, and this
adapter is the one that works now:

1. **This one.** Show the customer the shop's InstaPay address and the order
   reference, they transfer from their own banking app, and somebody at the shop
   confirms the money arrived. That is how a large number of Egyptian businesses
   are paid, it needs no API, and it is genuinely production-ready. The shape is
   the same as cash on delivery: a payment a human confirms out of band.
2. **Through Paymob, when Paymob enables it.** At that point InstaPay becomes an
   integration ID like card and wallet, and switching to it is setting
   `PAYMOB_INTEGRATION_ID_INSTAPAY` — a configuration change, not a code change.
   `apps/payments/config.py` already routes the method that way when the
   integration ID is present, in preference to this manual route.

WHAT THIS MEANS FOR RISK. Nothing here verifies that money arrived; a person
does. So the payment stays `requires_action` until somebody at the shop checks
their InstaPay app and confirms, and the order is **not** dispatched before that.
An InstaPay order is not fulfilled on a promise the way a COD order is — that is
the whole difference between the two, and `docs/payments/instapay.md` says so in
the words an operator needs.
"""
import uuid

from django.conf import settings

from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)


class InstaPayPaymentProvider(PaymentProvider):
    code = "instapay"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["INSTAPAY"]
        if not self.config.get("ENABLED"):
            raise NotImplementedError(
                "InstaPay is disabled. Set INSTAPAY_ENABLED=True and INSTAPAY_ADDRESS "
                "to the shop's InstaPay address (e.g. alreef@instapay). "
                "See docs/payments/instapay.md."
            )
        # An InstaPay method with no address to transfer to is not a payment
        # method, it is a dead end the customer discovers after checkout.
        if not str(self.config.get("ADDRESS") or "").strip():
            raise NotImplementedError(
                "InstaPay is enabled but INSTAPAY_ADDRESS is empty — the customer would "
                "have nowhere to send the transfer."
            )

    # -- provider interface ------------------------------------------------

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        """
        Hand the customer everything they need to make the transfer.

        The reference is the important part: it is what lets a staff member match
        an incoming transfer to an order. Customers do not always type it, which
        is why the amount and the order number are both shown too — but asking
        for it costs nothing and saves the ambiguous cases.
        """
        address = str(self.config["ADDRESS"]).strip()
        account_name = str(self.config.get("ACCOUNT_NAME") or "").strip()
        window_hours = int(self.config.get("CONFIRM_WINDOW_HOURS") or 24)

        instructions = (
            f"احوّل {payment.amount.amount:.2f} ج.م على إنستاباي للعنوان: {address}"
            + (f" (باسم {account_name})" if account_name else "")
            + f". واكتب رقم الطلب {order.order_number} في خانة الملاحظات."
        )

        return CreatePaymentResult(
            success=True,
            # Our own handle. There is no provider reference because there is no
            # provider to give us one.
            provider_reference=f"IPN-{uuid.uuid4().hex[:12].upper()}",
            # The customer has something to do, and has not done it yet.
            status="requires_action",
            metadata={
                # All non-secret, all shown to the customer: this is a published
                # payment address, not a credential.
                "instapay_address": address,
                "instapay_account_name": account_name,
                "transfer_amount": f"{payment.amount.amount:.2f}",
                "currency": str(payment.amount.currency),
                "transfer_reference": order.order_number,
                "confirm_window_hours": window_hours,
                "instructions_ar": instructions,
            },
        )

    def authorize(self, *, payment) -> CaptureResult:
        # Nothing to authorise. The customer either transfers or does not.
        raise NotImplementedError(
            "InstaPay has no authorization step — the customer transfers, or they do not."
        )

    def capture(self, *, payment, amount=None) -> CaptureResult:
        """
        The transfer arrived and somebody at the shop confirmed it.

        Reached only from `services.record_manual_receipt`, which requires a
        staff actor and writes an audit entry naming them — because for a
        manually confirmed payment that actor is the only evidence there is.
        """
        return CaptureResult(success=True, provider_transaction_id=payment.provider_reference)

    def cancel(self, *, payment) -> bool:
        # No transfer was made, so there is nothing to reverse.
        return True

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        """
        Record a refund a person performs.

        An InstaPay refund is a transfer back, made from the shop's own banking
        app. Recording it here is what makes the order reconcile; the MANUAL-
        prefix makes it obvious that no gateway moved the money.
        """
        return RefundResult(
            success=True,
            provider_refund_id=f"MANUAL-IPN-{uuid.uuid4().hex[:10].upper()}",
            raw={"manual": True, "note": "Transferred back to the customer by staff."},
        )

    def get_status(self, *, payment) -> StatusResult:
        # There is no external record to reconcile against — our own is the only
        # one. Echoing it back is the honest answer, and it means the background
        # reconciliation sweep can call this without special-casing.
        return StatusResult(status=payment.status, raw={"source": "local"})

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        # InstaPay sends no webhooks. False means any POST to an InstaPay webhook
        # URL is refused — which is the right answer to a request that could only
        # be hostile. (The route does not exist either: `instapay` is not in
        # WEBHOOK_PROVIDERS.)
        return False

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("InstaPay does not receive webhooks.")
