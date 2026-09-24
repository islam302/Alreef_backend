from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.inventory.models import (
    Batch,
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
    StockReservation,
    StockReservationStatus,
)

DEFAULT_RESERVATION_TTL_MINUTES = 30


def get_or_create_item(variant) -> InventoryItem:
    item, _ = InventoryItem.objects.get_or_create(variant=variant)
    return item


@transaction.atomic
def reserve_stock(*, variant, quantity: int, cart_id=None, order=None, ttl_minutes: int = DEFAULT_RESERVATION_TTL_MINUTES) -> list[StockReservation]:
    """
    Locks the variant's batches (FEFO order — earliest expiration first,
    nulls last) and creates one or more StockReservation rows covering
    `quantity`. Raises INSUFFICIENT_STOCK if not enough is available.
    """
    item = InventoryItem.objects.select_for_update().get(variant=variant)

    if not item.track_inventory:
        return []

    available = item.available_quantity
    if available < quantity and not item.allow_backorder:
        raise ApplicationError(
            ErrorCodes.INSUFFICIENT_STOCK,
            f"Only {available} unit(s) of '{variant}' are available.",
            details={"variant_id": variant.id, "available": available, "requested": quantity},
        )

    batches = (
        Batch.objects.select_for_update()
        .filter(item=item, is_active=True, quantity__gt=0)
        .order_by(F("expiration_date").asc(nulls_last=True), "received_at")
    )

    remaining = quantity
    reservations = []
    expires_at = timezone.now() + timedelta(minutes=ttl_minutes) if (cart_id and not order) else None

    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        if take <= 0:
            continue
        reservation = StockReservation.objects.create(
            item=item, batch=batch, quantity=take, cart_id=cart_id, order=order, expires_at=expires_at
        )
        reservations.append(reservation)
        InventoryMovement.objects.create(
            item=item, batch=batch, movement_type=InventoryMovementType.RESERVATION,
            quantity_delta=-take, reference=str(order.order_number) if order else str(cart_id or ""),
        )
        remaining -= take

    if remaining > 0:
        if item.allow_backorder:
            reservation = StockReservation.objects.create(
                item=item, batch=None, quantity=remaining, cart_id=cart_id, order=order, expires_at=expires_at
            )
            reservations.append(reservation)
        else:
            transaction.set_rollback(True)
            raise ApplicationError(ErrorCodes.INSUFFICIENT_STOCK, f"Only {quantity - remaining} unit(s) of '{variant}' could be reserved.")

    _maybe_trigger_low_stock_alert(item)
    return reservations


@transaction.atomic
def release_reservation(reservation: StockReservation):
    if reservation.status != StockReservationStatus.ACTIVE:
        return
    reservation.status = StockReservationStatus.RELEASED
    reservation.save(update_fields=["status"])
    InventoryMovement.objects.create(
        item=reservation.item, batch=reservation.batch, movement_type=InventoryMovementType.RELEASE,
        quantity_delta=reservation.quantity, reference=str(reservation.order_id or reservation.cart_id or ""),
    )


@transaction.atomic
def consume_reservation(reservation: StockReservation):
    """Called on order confirmation/payment success: permanently deducts the batch."""
    if reservation.status != StockReservationStatus.ACTIVE:
        return
    if reservation.batch:
        batch = Batch.objects.select_for_update().get(pk=reservation.batch_id)
        if batch.expiration_date and batch.expiration_date <= timezone.now().date():
            raise ApplicationError(ErrorCodes.NO_VALID_BATCH, "Cannot sell an expired batch.")
        batch.quantity -= reservation.quantity
        batch.save(update_fields=["quantity"])
    reservation.status = StockReservationStatus.CONSUMED
    reservation.save(update_fields=["status"])
    InventoryMovement.objects.create(
        item=reservation.item, batch=reservation.batch, movement_type=InventoryMovementType.SALE,
        quantity_delta=-reservation.quantity, reference=str(reservation.order_id or ""),
    )


@transaction.atomic
def release_expired_reservations():
    expired = StockReservation.objects.select_for_update().filter(
        status=StockReservationStatus.ACTIVE, expires_at__isnull=False, expires_at__lt=timezone.now()
    )
    count = 0
    for reservation in expired:
        release_reservation(reservation)
        count += 1
    return count


def _maybe_trigger_low_stock_alert(item: InventoryItem):
    if item.is_low_stock or item.is_out_of_stock:
        from apps.notifications.tasks import notify_low_stock_task

        notify_low_stock_task.delay(item.variant_id, item.available_quantity)


def get_batches_nearing_expiration(thresholds_days=None):
    from django.conf import settings

    thresholds_days = thresholds_days or settings.EXPIRATION_ALERT_DAYS
    today = timezone.now().date()
    result = {}
    for days in thresholds_days:
        target_date = today + timedelta(days=days)
        result[days] = Batch.objects.filter(is_active=True, quantity__gt=0, expiration_date=target_date).select_related("item__variant")
    return result


def get_expired_batches():
    return Batch.objects.filter(is_active=True, quantity__gt=0, expiration_date__lt=timezone.now().date())
