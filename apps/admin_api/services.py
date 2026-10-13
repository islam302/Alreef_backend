"""
Service functions that only the staff API needs.

Anything a customer flow also uses stays in the owning app's services module
(`apps.orders.services`, `apps.inventory.services`, ...) and is called from
here rather than reimplemented.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, DecimalField, F, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.inventory.models import (
    Batch,
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
)


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
@transaction.atomic
def adjust_stock(*, item: InventoryItem, quantity_delta: int, reason: str, notes: str = "",
                 batch_number: str = "", expiration_date=None, actor=None) -> InventoryMovement:
    """
    Apply a manual stock correction and leave an audit trail.

    Positive deltas land in a named batch (created when it does not exist yet)
    so expiry tracking keeps working. Negative deltas are drawn FEFO, matching
    how `apps.inventory.services.reserve_stock` consumes stock, so manual
    write-offs remove the same units a sale would have.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item.pk)

    if quantity_delta > 0:
        label = batch_number or f"ADJ-{timezone.now():%Y%m%d%H%M%S}"
        batch, created = Batch.objects.get_or_create(
            item=item,
            batch_number=label,
            defaults={
                "initial_quantity": quantity_delta,
                "quantity": quantity_delta,
                "expiration_date": expiration_date,
                "is_active": True,
            },
        )
        if not created:
            batch.quantity = F("quantity") + quantity_delta
            batch.initial_quantity = F("initial_quantity") + quantity_delta
            if expiration_date:
                batch.expiration_date = expiration_date
            batch.is_active = True
            batch.save(update_fields=["quantity", "initial_quantity", "expiration_date", "is_active"])
            batch.refresh_from_db()
        target_batch = batch
    else:
        needed = abs(quantity_delta)
        if item.available_quantity < needed:
            raise ApplicationError(
                ErrorCodes.INSUFFICIENT_STOCK,
                f"Cannot remove {needed} unit(s); only {item.available_quantity} available.",
                details={"available": item.available_quantity, "requested": needed},
            )
        batches = (
            Batch.objects.select_for_update()
            .filter(item=item, is_active=True, quantity__gt=0)
            .order_by(F("expiration_date").asc(nulls_last=True), "received_at")
        )
        remaining, target_batch = needed, None
        for batch in batches:
            if remaining <= 0:
                break
            take = min(batch.quantity, remaining)
            batch.quantity -= take
            batch.save(update_fields=["quantity"])
            remaining -= take
            target_batch = target_batch or batch
        if remaining > 0:
            raise ApplicationError(ErrorCodes.INSUFFICIENT_STOCK, "Stock changed while adjusting; retry.")

    movement = InventoryMovement.objects.create(
        item=item,
        batch=target_batch,
        movement_type=InventoryMovementType.ADJUSTMENT,
        quantity_delta=quantity_delta,
        reference=reason,
        notes=notes,
        created_by=actor,
    )
    return movement


# --------------------------------------------------------------------------
# Dashboard analytics
# --------------------------------------------------------------------------
def _money_sum(queryset, field: str) -> Decimal:
    total = queryset.aggregate(
        total=Coalesce(Sum(field), Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2))
    )["total"]
    return total or Decimal("0")


def get_sales_timeseries(days: int = 30) -> dict:
    """Daily revenue and order count for the dashboard chart."""
    from apps.orders.models import Order, OrderStatus

    days = max(1, min(days, 365))
    start = (timezone.now() - timedelta(days=days - 1)).date()

    rows = (
        Order.objects.exclude(status=OrderStatus.CANCELLED)
        .filter(created_at__date__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            revenue=Coalesce(Sum("grand_total"), Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2)),
            orders=Count("id"),
        )
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}

    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day)
        series.append({
            "date": day.isoformat(),
            "revenue": str(row["revenue"] if row else Decimal("0")),
            "orders": row["orders"] if row else 0,
        })
    return {"days": days, "series": series}


def get_status_breakdown() -> list:
    from apps.orders.models import Order

    rows = Order.objects.values("status").annotate(count=Count("id")).order_by("-count")
    return [{"status": row["status"], "count": row["count"]} for row in rows]


def get_payment_method_breakdown(days: int = 30) -> list:
    from apps.orders.models import Order, OrderStatus

    start = timezone.now() - timedelta(days=days)
    rows = (
        Order.objects.exclude(status=OrderStatus.CANCELLED)
        .filter(created_at__gte=start)
        .values("payment_method")
        .annotate(
            count=Count("id"),
            revenue=Coalesce(Sum("grand_total"), Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2)),
        )
        .order_by("-count")
    )
    return [
        {"payment_method": row["payment_method"], "count": row["count"], "revenue": str(row["revenue"])}
        for row in rows
    ]


def get_overview(days: int = 30) -> dict:
    """
    Extends `apps.analytics.services.get_admin_dashboard_summary` with the
    period totals and lifetime counters the dashboard home needs. The
    original summary is included untouched so both callers stay in sync.
    """
    from apps.analytics.services import get_admin_dashboard_summary
    from apps.catalog.models import Product, ProductStatus
    from apps.orders.models import Order, OrderStatus
    from apps.reviews.models import Review
    from apps.orders.models import ReturnRequest

    summary = get_admin_dashboard_summary()
    start = timezone.now() - timedelta(days=days)

    period_orders = Order.objects.filter(created_at__gte=start).exclude(status=OrderStatus.CANCELLED)
    previous_start = start - timedelta(days=days)
    previous_orders = Order.objects.filter(
        created_at__gte=previous_start, created_at__lt=start
    ).exclude(status=OrderStatus.CANCELLED)

    period_revenue = _money_sum(period_orders, "grand_total")
    previous_revenue = _money_sum(previous_orders, "grand_total")

    def _delta(current: Decimal, previous: Decimal):
        if previous == 0:
            return None
        return round(float((current - previous) / previous * 100), 1)

    summary.update({
        "period_days": days,
        "period_revenue": str(period_revenue),
        "period_order_count": period_orders.count(),
        "revenue_change_percent": _delta(period_revenue, previous_revenue),
        "order_change_percent": _delta(Decimal(period_orders.count()), Decimal(previous_orders.count())),
        "period_average_order_value": str(
            period_orders.aggregate(avg=Avg("grand_total"))["avg"] or Decimal("0")
        ),
        "lifetime_revenue": str(_money_sum(Order.objects.exclude(status=OrderStatus.CANCELLED), "grand_total")),
        "total_orders": Order.objects.count(),
        "completed_orders": Order.objects.filter(status=OrderStatus.DELIVERED).count(),
        "cancelled_orders": Order.objects.filter(status=OrderStatus.CANCELLED).count(),
        "processing_orders": Order.objects.filter(
            status__in=[OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.PACKED]
        ).count(),
        "shipped_orders": Order.objects.filter(
            status__in=[OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY]
        ).count(),
        "open_returns": ReturnRequest.objects.exclude(status__in=["closed", "refunded", "rejected"]).count(),
        "total_products": Product.objects.count(),
        "published_products": Product.objects.filter(published=True, status=ProductStatus.ACTIVE).count(),
        "draft_products": Product.objects.filter(status=ProductStatus.DRAFT).count(),
        "pending_reviews": Review.objects.filter(approved=False).count(),
    })
    return summary


# --------------------------------------------------------------------------
# Offers
# --------------------------------------------------------------------------
def headline_variant(product):
    """The size a product's cards and the offers band show: the default one
    while it is active, otherwise the first active size."""
    # A fresh query, not .all(): a product loaded with its sizes prefetched
    # would not see one added or changed a moment ago.
    variants = list(product.variants.filter(is_active=True))
    variants.sort(key=lambda v: (not v.is_default, v.sort_order, v.weight_grams or 0, v.pk))
    return variants[0] if variants else None


def sync_product_price(product):
    """
    Prices are set per size. The product's own price fields — what cards, the
    offers band and search read — follow its headline size, so they never
    disagree with what the cart charges. A product with no active size keeps
    whatever it had (it is not sellable, and the storefront shows no price).
    """
    variant = headline_variant(product)
    if variant is None:
        return
    fields = ("price", "compare_at_price", "cost_price")
    changed = [f for f in fields if getattr(product, f) != getattr(variant, f)]
    for f in changed:
        setattr(product, f, getattr(variant, f))
    if changed:
        product.save(update_fields=[*changed, "updated_at"])


@transaction.atomic
def set_offer(*, product, variant_prices: list[dict], price=None, compare_at_price=None) -> dict:
    """
    Put a product on offer, change its offer, or end it — in one step.

    A customer is charged the price of the size they buy, while cards and the
    homepage offers band read the product's own headline price. Changing one
    without the other would show one price and charge another, so this sets
    each size's price and "was" price, then copies the headline size's pair
    onto the product. A product with no sizes takes `price` /
    `compare_at_price` directly.

    `compare_at_price` must be above `price` (a real cut) or empty (no offer).
    Returns what changed, for the audit log.
    """
    by_id = {v.pk: v for v in product.variants.all()}
    changed = {}
    for row in variant_prices:
        variant = by_id.get(row["id"])
        if variant is None:
            raise ApplicationError(ErrorCodes.NOT_FOUND, f"Size {row['id']} is not part of this product.")
        variant.price = row["price"]
        variant.compare_at_price = row.get("compare_at_price")
        variant.save(update_fields=["price", "compare_at_price", "updated_at"])
        changed[variant.sku] = [str(row["price"]), str(row.get("compare_at_price") or "")]

    headline = headline_variant(product)
    if headline is not None:
        headline.refresh_from_db()
        product.price = headline.price
        product.compare_at_price = headline.compare_at_price
    elif price is not None:
        product.price = price
        product.compare_at_price = compare_at_price
    product.save(update_fields=["price", "compare_at_price", "updated_at"])
    changed["product"] = [str(product.price.amount), str(product.compare_at_price.amount if product.compare_at_price else "")]
    return changed
