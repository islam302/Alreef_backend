from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel
from apps.orders.models import PaymentMethodSnapshot as PaymentMethod


class PaymentProviderCode(models.TextChoices):
    PAYMOB = "paymob", "Paymob"
    FAWRY = "fawry", "Fawry"
    VALU = "valu", "valU"
    COD = "cod", "Cash on delivery"
    MANUAL = "manual", "Manual / store credit"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    AUTHORIZED = "authorized", "Authorized"
    CAPTURED = "captured", "Captured"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
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
    idempotency_key = models.CharField(max_length=128, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Non-sensitive redirect/iframe/reference data only — never card numbers, CVV, or secrets.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["provider_reference"]),
            models.Index(fields=["idempotency_key"]),
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

    class Meta:
        indexes = [models.Index(fields=["provider", "processed"])]


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
