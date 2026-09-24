from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel
from apps.orders.models import PaymentMethodSnapshot as PaymentMethod


class PaymentProviderCode(models.TextChoices):
    """
    The *settlement route* that holds the payment — not the method the customer
    picked at checkout.

    Three of them, and only one is a gateway:

    * `paymob` — the one real gateway. Serves card and mobile wallet, and will
      serve InstaPay when Paymob enables it, at which point it is a settings
      change and not a code change.
    * `instapay` — a bank transfer the customer makes themselves and a staff
      member confirms. InstaPay publishes no merchant API (see
      docs/payments/instapay.md), so there is nothing to call; this is a real
      route, not a placeholder.
    * `cod` — cash to the courier, confirmed on delivery.

    `manual` covers a payment taken entirely outside the system (store credit, a
    bank transfer outside InstaPay) and has no adapter.
    """

    PAYMOB = "paymob", "Paymob"
    INSTAPAY = "instapay", "InstaPay transfer"
    COD = "cod", "Cash on delivery"
    MANUAL = "manual", "Manual / store credit"


class PaymentStatus(models.TextChoices):
    """
    `CAPTURED` is this system's name for *paid*. The transitions between these
    values are defined once, in apps/payments/state.py, and nothing may write
    `Payment.status` without going through it.
    """

    PENDING = "pending", "Pending"
    # The customer has somewhere to go — a 3-D Secure page, a Paymob iframe, a
    # InstaPay transfer to make from their own banking app — but has not paid yet.
    REQUIRES_ACTION = "requires_action", "Requires customer action"
    AUTHORIZED = "authorized", "Authorized"
    CAPTURED = "captured", "Captured"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    # A refund has been sent to the provider but not yet confirmed.
    REFUND_PENDING = "refund_pending", "Refund pending"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"


class Payment(BaseModel):
    """One Payment per checkout attempt for an Order. See docs/architecture/payments.md."""

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=16, choices=PaymentProviderCode.choices)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)

    provider_reference = models.CharField(max_length=150, blank=True, db_index=True)
    # The reference *we* issued (Paymob's special_reference; the order number a
    # customer quotes on an InstaPay transfer). Webhook handling checks the
    # provider quoted this back unchanged, so a correctly-signed event for
    # somebody else's order cannot settle ours.
    merchant_reference = models.CharField(max_length=64, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=128, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Why the last attempt failed, in the provider's words, for the dashboard
    # and for support. Never a raw provider payload.
    failure_reason = models.CharField(max_length=500, blank=True)

    # Non-sensitive redirect/iframe/reference data only — never card numbers, CVV, or secrets.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["provider_reference"]),
            models.Index(fields=["idempotency_key"]),
            models.Index(fields=["merchant_reference"]),
        ]
        constraints = [
            # One payment per checkout idempotency key. The cache-backed
            # mixin in apps/core/idempotency.py is the fast path; this is the
            # one that still holds when the cache has been flushed, which is
            # exactly when a retry would otherwise double-charge.
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_payment_idempotency_key",
            ),
        ]

    def __str__(self):
        return f"Payment<{self.id}> {self.provider}/{self.status} for {self.order_id}"


class PaymentAttempt(BaseModel):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=PaymentStatus.choices)
    error_message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["attempt_number"]
        unique_together = [("payment", "attempt_number")]


class PaymentTransactionType(models.TextChoices):
    AUTHORIZE = "authorize", "Authorize"
    CAPTURE = "capture", "Capture"
    REFUND = "refund", "Refund"
    VOID = "void", "Void"


class PaymentTransaction(BaseModel):
    """Every provider round-trip is recorded here for reconciliation/audit."""

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="transactions")
    transaction_type = models.CharField(max_length=16, choices=PaymentTransactionType.choices)
    amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    provider_transaction_id = models.CharField(max_length=150, blank=True)
    success = models.BooleanField(default=False)
    raw_response = models.JSONField(default=dict, blank=True, help_text="Sensitive fields are redacted before storage.")

    class Meta:
        indexes = [models.Index(fields=["payment", "transaction_type"])]


class PaymentWebhookEvent(BaseModel):
    """
    Records every inbound webhook. `provider_event_id` is unique so a
    replayed/duplicated webhook is a no-op (spec §42/§89).
    """

    provider = models.CharField(max_length=16, choices=PaymentProviderCode.choices)
    event_type = models.CharField(max_length=100, blank=True)
    provider_event_id = models.CharField(max_length=150, unique=True)
    payload = models.JSONField(default=dict)
    signature_valid = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="webhook_events")

    # Kept even when no Payment matched, so an event we could not attribute is
    # visible to staff instead of silently dropped.
    provider_reference = models.CharField(max_length=150, blank=True)
    # Why a signed, non-duplicate event was still not applied: unknown
    # reference, amount mismatch, unmapped status, out-of-order arrival.
    rejection_reason = models.CharField(max_length=255, blank=True)
    # The provider's own timestamp for the event, when it sends one. Used to
    # drop an event that describes an older state than the one we hold.
    provider_event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider", "processed"]),
            models.Index(fields=["provider_reference"]),
        ]


class RefundStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class RefundDestination(models.TextChoices):
    ORIGINAL_METHOD = "original_method", "Original payment method"
    STORE_CREDIT = "store_credit", "Store credit"


class Refund(BaseModel):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="refunds")
    amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    reason = models.CharField(max_length=255, blank=True)
    destination = models.CharField(max_length=20, choices=RefundDestination.choices, default=RefundDestination.ORIGINAL_METHOD)
    status = models.CharField(max_length=16, choices=RefundStatus.choices, default=RefundStatus.PENDING)
    provider_refund_id = models.CharField(max_length=150, blank=True)
    idempotency_key = models.CharField(max_length=128, blank=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["payment"]), models.Index(fields=["idempotency_key"])]
