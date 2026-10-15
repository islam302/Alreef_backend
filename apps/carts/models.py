from django.core.validators import MinValueValidator
from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import TimeStampedModel, UUIDPrimaryKeyModel


class CartStatus(models.TextChoices):
    OPEN = "open", "Open"
    MERGED = "merged", "Merged into another cart"
    CONVERTED = "converted", "Converted to order"
    ABANDONED = "abandoned", "Abandoned"


class Cart(UUIDPrimaryKeyModel, TimeStampedModel):
    """Supports both guest (session_key) and authenticated (user) carts (spec §24)."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, null=True, blank=True, related_name="carts")
    session_key = models.CharField(max_length=40, null=True, blank=True, db_index=True)
    status = models.CharField(max_length=16, choices=CartStatus.choices, default=CartStatus.OPEN)
    currency = models.CharField(max_length=3, default="EGP")

    coupon_code = models.CharField(max_length=50, blank=True)

    abandoned_notified_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["session_key", "status"]),
            models.Index(fields=["last_activity_at"]),
        ]

    def __str__(self):
        return f"Cart<{self.id}>"


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price_snapshot = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")

    class Meta:
        unique_together = [("cart", "variant")]
        # The order lines were added in, always. Without it Postgres hands rows
        # back in storage order, and an UPDATE moves a row: changing a line's
        # quantity made it jump to the bottom of the cart under the shopper's thumb.
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.quantity} x {self.variant} in {self.cart_id}"

    @property
    def line_total(self):
        return self.unit_price_snapshot * self.quantity
