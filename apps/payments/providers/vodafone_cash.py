"""
Vodafone Cash — a wallet transfer the customer makes, and a staff member confirms.

THE TWO ROUTES FOR A MOBILE WALLET. The customer-facing method is "mobile wallet"
and it can be settled two ways:

1. **Through Paymob**, when the shop's Paymob account has a wallet integration
   (`PAYMOB_INTEGRATION_ID_WALLET`). The customer gets a payment request on their
   phone and Paymob tells us, signed, when it is paid. Fully automatic, with
   Paymob's fee. `apps/payments/config.py` prefers this route whenever it is
   configured.
2. **This one.** The customer sends the amount from their own Vodafone Cash wallet
   to the shop's wallet number, writes the order number in the transfer, and
   somebody at the shop confirms it arrived. No gateway, no fee, and it works the
   day the shop has a wallet. Vodafone offers no merchant acceptance API to small
   shops, which is why the automatic route goes through a gateway at all.

WHAT THIS MEANS FOR RISK. Exactly InstaPay's: nothing here verifies that money
arrived; a person does. The payment stays `requires_action`, the stock stays
reserved but not consumed, and the order is not confirmed — nor dispatched —
until staff record the transfer as received.
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


def wallet_number(config) -> str:
    """The configured number, digits only, so it reads and copies cleanly."""
    return "".join(ch for ch in str(config.get("NUMBER") or "") if ch.isdigit())


class VodafoneCashPaymentProvider(PaymentProvider):
    code = "vodafone_cash"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["VODAFONE_CASH"]
        if not self.config.get("ENABLED"):
            raise NotImplementedError(
                "Vodafone Cash transfers are disabled. Set VODAFONE_CASH_ENABLED=True and "
                "VODAFONE_CASH_NUMBER to the shop's wallet number. "
                "See docs/payments/vodafone-cash.md."
            )
        if not wallet_number(self.config):
            raise NotImplementedError(
                "Vodafone Cash is enabled but VODAFONE_CASH_NUMBER is empty — the customer "
                "would have nowhere to send the money."
            )

    # -- provider interface ------------------------------------------------

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        """
        Hand the customer what they need to send the money.

        The order number as the transfer's note is what lets staff match an
        incoming transfer to an order; the amount and the sender's number are the
        fallback when a customer forgets to write it.
        """
        number = wallet_number(self.config)
        account_name = str(self.config.get("ACCOUNT_NAME") or "").strip()
        window_hours = int(self.config.get("CONFIRM_WINDOW_HOURS") or 24)

        instructions = (
            f"حوّل {payment.amount.amount:.2f} ج.م فودافون كاش على الرقم {number}"
            + (f" (باسم {account_name})" if account_name else "")
            + f"، واكتب رقم الطلب {order.order_number} في رسالة التحويل."
        )

        return CreatePaymentResult(
            success=True,
            # Our own handle: there is no provider to give us one.
            provider_reference=f"VFC-{uuid.uuid4().hex[:12].upper()}",
            status="requires_action",
            metadata={
                # All published to the customer; a wallet number to pay into is
                # not a secret.
                "wallet_number": number,
                "wallet_account_name": account_name,
                "transfer_amount": f"{payment.amount.amount:.2f}",
                "currency": str(payment.amount.currency),
                "transfer_reference": order.order_number,
                "confirm_window_hours": window_hours,
                "instructions_ar": instructions,
            },
        )

    def authorize(self, *, payment) -> CaptureResult:
        raise NotImplementedError(
            "A Vodafone Cash transfer has no authorization step — the customer sends it, or not."
        )

    def capture(self, *, payment, amount=None) -> CaptureResult:
        """
        The transfer arrived and somebody at the shop confirmed it.

        Reached only through `services.record_manual_receipt`, which requires a
        staff actor and audits them by name: for a manually confirmed payment that
        person is the only evidence there is.
        """
        return CaptureResult(success=True, provider_transaction_id=payment.provider_reference)

    def cancel(self, *, payment) -> bool:
        # No transfer was made, so there is nothing to reverse.
        return True

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        """
        Record a refund a person performs: a transfer back from the shop's wallet.
        """
        return RefundResult(
            success=True,
            provider_refund_id=f"MANUAL-VFC-{uuid.uuid4().hex[:10].upper()}",
            raw={"manual": True, "note": "Sent back to the customer's wallet by staff."},
        )

    def get_status(self, *, payment) -> StatusResult:
        # Our record is the only record.
        return StatusResult(status=payment.status, raw={"source": "local"})

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        # No webhooks exist for this route; any POST claiming to be one is refused.
        return False

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        raise NotImplementedError("Vodafone Cash transfers do not receive webhooks.")
