from django.db import models

from apps.core.models import BaseModel


class AnalyticsEventType(models.TextChoices):
    PRODUCT_VIEW = "product_view", "Product view"
    SEARCH = "search", "Search"
    ADD_TO_CART = "add_to_cart", "Add to cart"
    REMOVE_FROM_CART = "remove_from_cart", "Remove from cart"
    CHECKOUT_STARTED = "checkout_started", "Checkout started"
    PAYMENT_STARTED = "payment_started", "Payment started"
    PAYMENT_SUCCESS = "payment_success", "Payment success"
    PAYMENT_FAILED = "payment_failed", "Payment failed"
    ORDER_CREATED = "order_created", "Order created"
    ORDER_DELIVERED = "order_delivered", "Order delivered"
    REFUND_CREATED = "refund_created", "Refund created"
    COUPON_USED = "coupon_used", "Coupon used"
    REVIEW_CREATED = "review_created", "Review created"


class AnalyticsEvent(BaseModel):
    """
    Raw event stream (spec §54). Written fast (indexed by type + time only);
    heavier aggregation happens asynchronously via apps.analytics.tasks or
    ad-hoc admin dashboard queries — never synchronously in the request path.
    """

    event_type = models.CharField(max_length=32, choices=AnalyticsEventType.choices, db_index=True)
    user = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="analytics_events")
    session_key = models.CharField(max_length=40, blank=True)
    product = models.ForeignKey("catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["event_type", "created_at"]), models.Index(fields=["user", "event_type"])]
