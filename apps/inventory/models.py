from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class InventoryItemQuerySet(models.QuerySet):
    """
    Stock totals computed in SQL instead of once per row in Python.

    Every stock number on this model is a sum over a child table, and reading
    one as a property costs a query. The admin inventory list shows five of
    them per row, so a 20-row page was issuing ~150 queries and the "low stock"
    filter walked the whole table in Python to do it. `with_stock()` moves the
    arithmetic into the query, which makes the page cost one query regardless of
    how much stock the shop carries.

    The totals are deliberately correlated subqueries rather than two `Sum()`
    annotations. Summing across two reverse foreign keys in a single query joins
    both child tables together, so every batch row is repeated once per
    reservation row and both totals come back inflated — silently, and only for
    items that happen to have both. A subquery per total cannot multiply.
    """

    def with_stock(self):
        """Annotate `on_hand_total`, `reserved_total` and `available_total`."""
        # Idempotent: annotating twice is an error, and callers legitimately
        # chain these helpers without knowing what came before.
        if "available_total" in self.query.annotations:
            return self

        on_hand = (
            Batch.objects.filter(item=OuterRef("pk"), is_active=True)
            .values("item")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )
        reserved = (
            StockReservation.objects.filter(
                item=OuterRef("pk"), status=StockReservationStatus.ACTIVE
            )
            .values("item")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )
        return self.annotate(
            on_hand_total=Coalesce(Subquery(on_hand, output_field=models.IntegerField()), Value(0)),
            reserved_total=Coalesce(Subquery(reserved, output_field=models.IntegerField()), Value(0)),
        ).annotate(
            # Mirrors `available_quantity`: a negative figure is not meaningful
            # to show, and clamping in SQL keeps the filters below honest.
            available_total=Greatest(F("on_hand_total") - F("reserved_total"), Value(0)),
        )

    # The three states the dashboard filters by, as SQL rather than a Python
    # scan. They must agree exactly with `is_low_stock` / `is_out_of_stock`.
    def _low_q(self):
        return Q(available_total__gt=0, available_total__lte=F("low_stock_threshold"))

    def _out_q(self):
        return Q(available_total__lte=0, allow_backorder=False)

    def low_stock(self):
        return self.with_stock().filter(self._low_q())

    def out_of_stock(self):
        return self.with_stock().filter(self._out_q())

    def healthy_stock(self):
        """Neither low nor out — the complement, so the three states partition."""
        qs = self.with_stock()
        return qs.exclude(qs._low_q()).exclude(qs._out_q())

    def for_state(self, state):
        """Map the dashboard's `?state=` value onto the filters above."""
        if state == "low":
            return self.low_stock()
        if state == "out":
            return self.out_of_stock()
        if state == "in":
            return self.healthy_stock()
        return self


class InventoryItem(BaseModel):
    """One row per ProductVariant. On-hand stock is the sum of its active Batches."""

    variant = models.OneToOneField("catalog.ProductVariant", on_delete=models.CASCADE, related_name="inventory_item")
    track_inventory = models.BooleanField(default=True)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    allow_backorder = models.BooleanField(default=False)

    objects = InventoryItemQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["variant"])]

    def __str__(self):
        return f"Inventory<{self.variant.sku}>"

    # Each of these prefers the value `InventoryItemQuerySet.with_stock()` put
    # on the instance, and falls back to its own query when the object was
    # loaded without it. That keeps a single `InventoryItem.objects.get(...)`
    # in checkout correct while making list pages cost nothing per row.
    @property
    def quantity_on_hand(self) -> int:
        annotated = getattr(self, "on_hand_total", None)
        if annotated is not None:
            return annotated
        return self.batches.filter(is_active=True).aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def reserved_quantity(self) -> int:
        annotated = getattr(self, "reserved_total", None)
        if annotated is not None:
            return annotated
        return self.reservations.filter(status=StockReservationStatus.ACTIVE).aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def available_quantity(self) -> int:
        annotated = getattr(self, "available_total", None)
        if annotated is not None:
            return annotated
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
