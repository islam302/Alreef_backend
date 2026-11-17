from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.inventory.models import (
    Batch,
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
    StockReservation,
    StockReservationStatus,
    sellable_batch_q,
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

    # A batch expiring today is not sellable: consume_reservation refuses it, so
    # reserving it only failed the order later, at confirmation.
    batches = (
        Batch.objects.select_for_update()
        .filter(item=item, is_active=True, quantity__gt=0)
        .filter(sellable_batch_q())
        .order_by(F("expiration_date").asc(nulls_last=True), "received_at")
    )

    # A reservation does not take units out of `batch.quantity` (consuming it
    # does), so a batch's free units are its quantity minus its active holds.
    # Taking `batch.quantity` alone booked the same jars to two orders, and the
    # second confirmation drove the batch negative.
    held = dict(
        StockReservation.objects.filter(item=item, status=StockReservationStatus.ACTIVE, batch__isnull=False)
        .values("batch_id")
        .annotate(total=Sum("quantity"))
        .values_list("batch_id", "total")
    )

    remaining = quantity
    reservations = []
    expires_at = timezone.now() + timedelta(minutes=ttl_minutes) if (cart_id and not order) else None

    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity - held.get(batch.pk, 0), remaining)
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
def release_reservation(
    reservation: StockReservation,
    *,
    final_status: str = StockReservationStatus.RELEASED,
):
    """Release an active hold exactly once.

    Callers often hold model instances read before another request finishes.
    Lock and re-read the reservation so a concurrent consume/release cannot
    create a second movement or overwrite the settled status.
    """
    if final_status not in {StockReservationStatus.RELEASED, StockReservationStatus.EXPIRED}:
        raise ValueError("A released reservation must end as released or expired.")

    locked = StockReservation.objects.select_for_update().get(pk=reservation.pk)
    if locked.status != StockReservationStatus.ACTIVE:
        return
    locked.status = final_status
    locked.save(update_fields=["status"])
    InventoryMovement.objects.create(
        item=locked.item, batch=locked.batch, movement_type=InventoryMovementType.RELEASE,
        quantity_delta=locked.quantity, reference=str(locked.order_id or locked.cart_id or ""),
    )


@transaction.atomic
def consume_reservation(reservation: StockReservation):
    """Called on order confirmation/payment success: permanently deducts the batch."""
    locked = StockReservation.objects.select_for_update().get(pk=reservation.pk)
    if locked.status != StockReservationStatus.ACTIVE:
        return
    if locked.batch:
        batch = Batch.objects.select_for_update().get(pk=locked.batch_id)
        if batch.expiration_date and batch.expiration_date <= timezone.localdate():
            raise ApplicationError(ErrorCodes.NO_VALID_BATCH, "Cannot sell an expired batch.")
        batch.quantity -= locked.quantity
        batch.save(update_fields=["quantity"])
    locked.status = StockReservationStatus.CONSUMED
    locked.save(update_fields=["status"])
    InventoryMovement.objects.create(
        item=locked.item, batch=locked.batch, movement_type=InventoryMovementType.SALE,
        quantity_delta=-locked.quantity, reference=str(locked.order_id or ""),
    )


def consume_for_confirmation(reservation: StockReservation) -> bool:
    """
    Consume a confirmed order's hold without ever failing the confirmation.

    Reached when the money has arrived (a gateway capture, a recorded
    transfer). If the reserved batch hit its expiry date while the customer was
    paying, consume_reservation refuses it — and that refusal used to roll the
    whole capture back inside the webhook: the payment was never recorded and
    the gateway retried forever. The hold is moved to another sellable batch
    with free units; when none has room it stays active and is reported for a
    person to resolve (an active hold on a confirmed order is what the
    invariants check looks for). Returns True when the units were consumed.
    """
    try:
        consume_reservation(reservation)
        return True
    except ApplicationError as exc:
        if exc.code != ErrorCodes.NO_VALID_BATCH:
            raise

    with transaction.atomic():
        locked = StockReservation.objects.select_for_update().get(pk=reservation.pk)
        InventoryItem.objects.select_for_update().get(pk=locked.item_id)
        held = dict(
            StockReservation.objects.filter(item_id=locked.item_id, status=StockReservationStatus.ACTIVE, batch__isnull=False)
            .exclude(pk=locked.pk)
            .values("batch_id")
            .annotate(total=Sum("quantity"))
            .values_list("batch_id", "total")
        )
        candidates = (
            Batch.objects.select_for_update()
            .filter(item_id=locked.item_id, is_active=True, quantity__gt=0)
            .filter(sellable_batch_q())
            .exclude(pk=locked.batch_id)
            .order_by(F("expiration_date").asc(nulls_last=True), "received_at")
        )
        replacement = next(
            (b for b in candidates if b.quantity - held.get(b.pk, 0) >= locked.quantity), None
        )
        if replacement is not None:
            locked.batch = replacement
            locked.save(update_fields=["batch"])
    if replacement is not None:
        consume_reservation(locked)
        return True

    import logging

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    logging.getLogger(__name__).error(
        "order %s: reservation %s is on an expired batch and no other batch has %s free unit(s); "
        "left active for staff", reservation.order_id, reservation.pk, reservation.quantity,
    )
    record(
        actor=None, action=AuditAction.STATUS_CHANGE, model_name="StockReservation", object_id=reservation.pk,
        changes={"needs_attention": True, "reason": "Reserved batch expired before the order was paid; no replacement batch."},
    )
    return False


@transaction.atomic
def release_expired_reservations():
    expired = StockReservation.objects.select_for_update().filter(
        status=StockReservationStatus.ACTIVE, expires_at__isnull=False, expires_at__lt=timezone.now()
    )
    count = 0
    for reservation in expired:
        release_reservation(reservation, final_status=StockReservationStatus.EXPIRED)
        count += 1
    return count


def _maybe_trigger_low_stock_alert(item: InventoryItem):
    if item.is_low_stock or item.is_out_of_stock:
        from apps.notifications.tasks import notify_low_stock_task

        notify_low_stock_task.delay(item.variant_id, item.available_quantity)


def get_batches_nearing_expiration(thresholds_days=None):
    from django.conf import settings

    thresholds_days = thresholds_days or settings.EXPIRATION_ALERT_DAYS
    today = timezone.localdate()
    result = {}
    for days in thresholds_days:
        target_date = today + timedelta(days=days)
        result[days] = Batch.objects.filter(is_active=True, quantity__gt=0, expiration_date=target_date).select_related("item__variant")
    return result


def get_expired_batches():
    # `lte`, matching sellable_batch_q: a batch is no longer sold on its expiry
    # date, so the sweep takes it off sale that day rather than the next.
    return Batch.objects.filter(is_active=True, quantity__gt=0, expiration_date__lte=timezone.localdate())
