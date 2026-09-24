from django.db import models

from apps.core.models import BaseModel


class AuditAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"
    STATUS_CHANGE = "status_change", "Status change"
    REFUND = "refund", "Refund"
    LOGIN = "login", "Login"
    API_MUTATION = "api_mutation", "API mutation"


class AuditLog(BaseModel):
    """
    Tracks admin actions and sensitive service-layer operations (spec §96):
    product/inventory/order/payment/coupon changes, user management. Django
    admin's built-in LogEntry already covers most /admin/ edits; this table
    additionally captures API-driven mutations and business-critical
    transitions (order status, refunds) that don't go through /admin/.
    """

    actor = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=20, choices=AuditAction.choices)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"{self.action} {self.model_name}#{self.object_id} by {self.actor_id}"
