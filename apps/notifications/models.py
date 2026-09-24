from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class NotificationEvent(models.TextChoices):
    REGISTRATION = "registration", "Registration"
    EMAIL_VERIFICATION = "email_verification", "Email verification"
    PHONE_VERIFICATION = "phone_verification", "Phone verification"
    PASSWORD_RESET = "password_reset", "Password reset"
    ORDER_CREATED = "order_created", "Order created"
    PAYMENT_SUCCESS = "payment_success", "Payment success"
    PAYMENT_FAILED = "payment_failed", "Payment failed"
    ORDER_SHIPPED = "order_shipped", "Order shipped"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    ORDER_DELIVERED = "order_delivered", "Order delivered"
    REFUND_PROCESSED = "refund_processed", "Refund processed"
    LOW_STOCK = "low_stock", "Low stock"
    ABANDONED_CART = "abandoned_cart", "Abandoned cart"
    PROMOTION = "promotion", "Promotion"
    EXPIRATION_ALERT = "expiration_alert", "Expiration alert"


class NotificationChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"
    PUSH = "push", "Push"
    IN_APP = "in_app", "In-app"


class Notification(BaseModel):
    """In-app notification shown to a user (e.g. bell icon feed)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    event = models.CharField(max_length=32, choices=NotificationEvent.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return f"{self.event} -> {self.user_id}"


class NotificationLog(BaseModel):
    """Delivery audit trail for every outbound email/SMS/WhatsApp/push send."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="notification_logs"
    )
    event = models.CharField(max_length=32, choices=NotificationEvent.choices)
    channel = models.CharField(max_length=16, choices=NotificationChannel.choices)
    recipient = models.CharField(max_length=255)
    success = models.BooleanField(default=False)
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["event", "channel"]), models.Index(fields=["recipient"])]


class NotificationPreference(BaseModel):
    """Per-user opt-in/opt-out per channel for non-transactional (marketing) events."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preference")
    marketing_email = models.BooleanField(default=True)
    marketing_sms = models.BooleanField(default=False)
    marketing_whatsapp = models.BooleanField(default=False)
    marketing_push = models.BooleanField(default=True)
