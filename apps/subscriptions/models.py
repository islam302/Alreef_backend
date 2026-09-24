from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import BaseModel
from apps.orders.models import PaymentMethodSnapshot


class SubscriptionFrequency(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    CUSTOM = "custom", "Custom interval"


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    CANCELLED = "cancelled", "Cancelled"


class Subscription(BaseModel):
    """Recurring purchase (spec §20), e.g. 'Monthly Honey Box'. Payment
    behavior is never hard-coded to one gateway — `payment_method` reuses
    the same provider abstraction as regular checkout."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="subscriptions")
    name = models.CharField(max_length=150)
    address = models.ForeignKey("users.Address", on_delete=models.PROTECT, related_name="subscriptions")
    shipping_method = models.ForeignKey("shipping.ShippingMethod", on_delete=models.PROTECT, related_name="subscriptions")
    payment_method = models.CharField(max_length=20, choices=PaymentMethodSnapshot.choices)

    frequency = models.CharField(max_length=16, choices=SubscriptionFrequency.choices, default=SubscriptionFrequency.MONTHLY)
    custom_interval_days = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.ACTIVE)

    next_order_date = models.DateField()
    last_order_date = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"]), models.Index(fields=["next_order_date", "status"])]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    @property
    def interval_days(self) -> int:
        return {
            SubscriptionFrequency.WEEKLY: 7,
            SubscriptionFrequency.MONTHLY: 30,
            SubscriptionFrequency.QUARTERLY: 90,
        }.get(self.frequency, self.custom_interval_days or 30)


class SubscriptionItem(BaseModel):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="subscription_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        unique_together = [("subscription", "variant")]


class SubscriptionScheduleStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class SubscriptionSchedule(BaseModel):
    """One row per billing cycle — the audit trail of what happened each cycle."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="schedule_entries")
    scheduled_date = models.DateField()
    status = models.CharField(max_length=16, choices=SubscriptionScheduleStatus.choices, default=SubscriptionScheduleStatus.PENDING)
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_schedule_entry")
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-scheduled_date"]


class SubscriptionPayment(BaseModel):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="payments")
    schedule_entry = models.ForeignKey(SubscriptionSchedule, on_delete=models.CASCADE, related_name="payment")
    payment = models.ForeignKey("payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_payment")

    class Meta:
        indexes = [models.Index(fields=["subscription"])]
