from django.core.validators import MinValueValidator
from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class InventoryItem(BaseModel):
    """One row per ProductVariant. On-hand stock is the sum of its active Batches."""

    variant = models.OneToOneField("catalog.ProductVariant", on_delete=models.CASCADE, related_name="inventory_item")
    track_inventory = models.BooleanField(default=True)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    allow_backorder = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["variant"])]

    def __str__(self):
        return f"Inventory<{self.variant.sku}>"

    @property
    def quantity_on_hand(self) -> int:
        return self.batches.filter(is_active=True).aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def reserved_quantity(self) -> int:
        return self.reservations.filter(status=StockReservationStatus.ACTIVE).aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def available_quantity(self) -> int:
        return max(self.quantity_on_hand - self.reserved_quantity, 0)

    @property
    def is_low_stock(self) -> bool:
        return 0 < self.available_quantity <= self.low_stock_threshold

    @property
    def is_out_of_stock(self) -> bool:
        return self.available_quantity <= 0 and not self.allow_backorder


class Batch(BaseModel):
    """
    A received lot of stock. `expiration_date` is null for non-perishable
    goods. FEFO fulfillment (spec §22) always consumes the batch with the
    earliest non-null expiration_date first.
    """

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="batches")
    batch_number = models.CharField(max_length=100)
    production_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True, db_index=True)
    initial_quantity = models.PositiveIntegerField()
    quantity = models.IntegerField(validators=[MinValueValidator(0)])
    cost_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["expiration_date", "received_at"]
        indexes = [models.Index(fields=["item", "is_active"]), models.Index(fields=["expiration_date"])]
        unique_together = [("item", "batch_number")]

    def __str__(self):
        return f"{self.item.variant.sku} batch {self.batch_number}"


class InventoryMovementType(models.TextChoices):
    PURCHASE = "purchase", "Purchase"
    SALE = "sale", "Sale"
    RETURN = "return", "Return"
    ADJUSTMENT = "adjustment", "Adjustment"
    DAMAGE = "damage", "Damage"
    EXPIRED = "expired", "Expired"
    TRANSFER = "transfer", "Transfer"
    RESERVATION = "reservation", "Reservation"
    RELEASE = "release", "Release"


class InventoryMovement(BaseModel):
    """Append-only audit trail — every stock change must be traceable (spec §23)."""

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="movements")
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements")
    movement_type = models.CharField(max_length=16, choices=InventoryMovementType.choices)
    quantity_delta = models.IntegerField(help_text="Positive for stock in, negative for stock out")
    reference = models.CharField(max_length=100, blank=True, help_text="Order number, adjustment reason, etc.")
    notes = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["item", "movement_type"]), models.Index(fields=["reference"])]


class StockReservationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CONSUMED = "consumed", "Consumed"
    RELEASED = "released", "Released"
    EXPIRED = "expired", "Expired"


class StockReservation(BaseModel):
    """
    Temporary hold placed during checkout so two customers cannot both buy
    the last unit (spec §93 concurrency). Reservations expire automatically
    if checkout is abandoned.
    """

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="reservations")
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True, related_name="reservations")
    quantity = models.PositiveIntegerField()
    cart_id = models.UUIDField(null=True, blank=True, db_index=True)
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_reservations")
    status = models.CharField(max_length=16, choices=StockReservationStatus.choices, default=StockReservationStatus.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["item", "status"]), models.Index(fields=["cart_id"]), models.Index(fields=["expires_at"])]
