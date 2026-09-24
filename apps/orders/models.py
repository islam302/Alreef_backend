from django.core.validators import MinValueValidator
from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    PROCESSING = "processing", "Processing"
    PACKED = "packed", "Packed"
    SHIPPED = "shipped", "Shipped"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    RETURN_REQUESTED = "return_requested", "Return requested"
    RETURNED = "returned", "Returned"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"


# Strict state machine (spec §38) — only these transitions are permitted.
ORDER_STATUS_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED, OrderStatus.RETURN_REQUESTED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.RETURN_REQUESTED},
    OrderStatus.DELIVERED: {OrderStatus.RETURN_REQUESTED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.RETURN_REQUESTED: {OrderStatus.RETURNED, OrderStatus.DELIVERED},
    OrderStatus.RETURNED: {OrderStatus.REFUNDED, OrderStatus.PARTIALLY_REFUNDED},
    OrderStatus.REFUNDED: set(),
    OrderStatus.PARTIALLY_REFUNDED: {OrderStatus.REFUNDED},
}


class PaymentMethodSnapshot(models.TextChoices):
    """
    The payment methods this shop accepts.

    Deliberately short. Fawry, valU, Sympl and Aman were here and were removed:
    the business does not want them, and carrying a method nobody can use means
    carrying an adapter nobody tests. What remains is what ELREEF actually sells
    with — see docs/payments/PROVIDERS.md.
    """

    CARD = "card", "Card (Visa / Mastercard / Meeza)"
    MOBILE_WALLET = "mobile_wallet", "Mobile wallet (Vodafone Cash etc.)"
    INSTAPAY = "instapay", "InstaPay bank transfer"
    COD = "cod", "Cash on delivery"


class Order(BaseModel):
    """
    Orders store immutable snapshots (spec §37) of product/pricing/address
    data at the time of purchase — later changes to products, addresses, or
    prices must never alter historical orders.
    """

    order_number = models.CharField(max_length=30, unique=True, db_index=True)
    user = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    currency = models.CharField(max_length=3, default="EGP")

    # Snapshots — never re-derived from live data after creation.
    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=32)
    shipping_address_snapshot = models.JSONField(default=dict)
    shipping_method_name = models.CharField(max_length=100, blank=True)

    payment_method = models.CharField(max_length=20, choices=PaymentMethodSnapshot.choices)

    subtotal = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    coupon_code = models.CharField(max_length=50, blank=True)
    coupon_discount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    promotion_discount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    loyalty_discount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    gift_card_amount_used = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    store_credit_amount_used = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    tax_total = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    shipping_total = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    cod_fee = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    grand_total = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")

    notes = models.CharField(max_length=500, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    confirmed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order_number"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.order_number

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in ORDER_STATUS_TRANSITIONS.get(self.status, set())

    def can_cancel(self) -> bool:
        return self.can_transition_to(OrderStatus.CANCELLED)


class OrderItem(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items")
    product = models.ForeignKey("catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items")

    # Snapshots so historical orders survive catalog changes (spec §37).
    product_name_snapshot = models.CharField(max_length=255)
    variant_name_snapshot = models.CharField(max_length=150, blank=True)
    sku_snapshot = models.CharField(max_length=64)

    unit_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    discount_amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    tax_amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)
    line_total = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")

    class Meta:
        indexes = [models.Index(fields=["order"]), models.Index(fields=["product"])]

    def __str__(self):
        return f"{self.quantity} x {self.product_name_snapshot}"


class OrderStatusHistory(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]


class ReturnReason(models.TextChoices):
    DAMAGED = "damaged", "Damaged"
    WRONG_PRODUCT = "wrong_product", "Wrong product"
    MISSING_PRODUCT = "missing_product", "Missing product"
    EXPIRED_PRODUCT = "expired_product", "Expired product"
    QUALITY_ISSUE = "quality_issue", "Quality issue"
    CUSTOMER_CHANGE_OF_MIND = "customer_change_of_mind", "Change of mind"


class ReturnRequestStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    ITEM_RECEIVED = "item_received", "Item received"
    REFUNDED = "refunded", "Refunded"
    CLOSED = "closed", "Closed"


class ReturnRequest(BaseModel):
    """
    Return/refund request against a delivered order (spec §39). Return
    policy windows/eligibility are enforced in apps.orders.services rather
    than hard-coded here, so legal/business rules stay configurable.
    """

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="return_requests")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="return_requests")
    reason = models.CharField(max_length=32, choices=ReturnReason.choices)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ReturnRequestStatus.choices, default=ReturnRequestStatus.REQUESTED)
    resolution_note = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["order"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"Return<{self.id}> for {self.order.order_number}"


class ReturnItem(BaseModel):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="return_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        unique_together = [("return_request", "order_item")]


class ReturnPolicySettings(BaseModel):
    """Admin-configurable return policy (spec §39) — never hard-code legal rules in code."""

    return_window_days = models.PositiveIntegerField(default=14)
    allow_change_of_mind_returns = models.BooleanField(default=True)
    require_approval = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Return policy settings"
        verbose_name_plural = "Return policy settings"

    def __str__(self):
        return "Return policy settings"

    @classmethod
    def get_solo(cls) -> "ReturnPolicySettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
