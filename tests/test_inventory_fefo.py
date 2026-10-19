"""
FEFO (first-expired, first-out) inventory allocation tests — the core
guarantee of apps.inventory.services.reserve_stock/consume_reservation.
"""
import datetime

import pytest
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.inventory.models import Batch, InventoryMovement, InventoryMovementType, StockReservationStatus
from apps.inventory.services import consume_reservation, release_expired_reservations, reserve_stock

pytestmark = pytest.mark.django_db


def _make_batch(item, *, batch_number, quantity, expiration_date):
    return Batch.objects.create(
        item=item, batch_number=batch_number, quantity=quantity, initial_quantity=quantity,
        expiration_date=expiration_date, is_active=True,
    )


def test_reserve_stock_uses_earliest_expiring_batch_first(product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    item = product_with_variant_and_stock["item"]
    item.batches.all().delete()

    today = timezone.now().date()
    early_batch = _make_batch(item, batch_number="EARLY", quantity=5, expiration_date=today + datetime.timedelta(days=10))
    _make_batch(item, batch_number="LATE", quantity=10, expiration_date=today + datetime.timedelta(days=100))

    reservations = reserve_stock(variant=variant, quantity=3)

    assert len(reservations) == 1
    assert reservations[0].batch_id == early_batch.id
    assert reservations[0].quantity == 3
    assert reservations[0].status == StockReservationStatus.ACTIVE


def test_reserve_stock_spills_into_next_batch_when_first_is_exhausted(product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    item = product_with_variant_and_stock["item"]
    item.batches.all().delete()

    today = timezone.now().date()
    early_batch = _make_batch(item, batch_number="EARLY", quantity=4, expiration_date=today + datetime.timedelta(days=10))
    late_batch = _make_batch(item, batch_number="LATE", quantity=10, expiration_date=today + datetime.timedelta(days=100))

    reservations = reserve_stock(variant=variant, quantity=6)

    assert len(reservations) == 2
    by_batch = {r.batch_id: r.quantity for r in reservations}
    assert by_batch[early_batch.id] == 4
    assert by_batch[late_batch.id] == 2


def test_batches_without_expiration_are_consumed_last(product_with_variant_and_stock):
    """Batches with expiration_date=NULL sort after dated batches (nulls_last FEFO ordering)."""
    variant = product_with_variant_and_stock["variant"]
    item = product_with_variant_and_stock["item"]
    item.batches.all().delete()

    today = timezone.now().date()
    no_expiry_batch = _make_batch(item, batch_number="NO-EXPIRY", quantity=5, expiration_date=None)
    dated_batch = _make_batch(item, batch_number="DATED", quantity=3, expiration_date=today + datetime.timedelta(days=30))

    reservations = reserve_stock(variant=variant, quantity=3)

    assert len(reservations) == 1
    assert reservations[0].batch_id == dated_batch.id
    assert no_expiry_batch.quantity == 5  # untouched


def test_reserve_stock_insufficient_raises_application_error(product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]  # fixture batch has quantity=10

    with pytest.raises(ApplicationError) as exc_info:
        reserve_stock(variant=variant, quantity=999)

    assert exc_info.value.code == ErrorCodes.INSUFFICIENT_STOCK
    assert exc_info.value.status_code == 409


def test_reserve_stock_does_not_partially_reserve_on_insufficient_stock(product_with_variant_and_stock):
    """A failed reservation must roll back cleanly — no orphaned ACTIVE reservations."""
    from apps.inventory.models import StockReservation

    variant = product_with_variant_and_stock["variant"]

    with pytest.raises(ApplicationError):
        reserve_stock(variant=variant, quantity=999)

    assert not StockReservation.objects.filter(item=product_with_variant_and_stock["item"], status=StockReservationStatus.ACTIVE).exists()


def test_consume_reservation_decrements_batch_and_creates_movement(product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    batch = product_with_variant_and_stock["batch"]

    reservations = reserve_stock(variant=variant, quantity=4)
    reservation = reservations[0]
    assert reservation.batch_id == batch.id

    consume_reservation(reservation)

    batch.refresh_from_db()
    assert batch.quantity == 6  # 10 - 4

    reservation.refresh_from_db()
    assert reservation.status == StockReservationStatus.CONSUMED

    movement = InventoryMovement.objects.filter(
        item=reservation.item, movement_type=InventoryMovementType.SALE,
    ).order_by("-created_at").first()
    assert movement is not None
    assert movement.quantity_delta == -4
    assert movement.batch_id == batch.id


def test_consume_reservation_is_idempotent_for_non_active_reservation(product_with_variant_and_stock):
    """Calling consume_reservation twice must not double-decrement the batch."""
    variant = product_with_variant_and_stock["variant"]
    batch = product_with_variant_and_stock["batch"]

    reservation = reserve_stock(variant=variant, quantity=2)[0]
    consume_reservation(reservation)
    batch.refresh_from_db()
    assert batch.quantity == 8

    consume_reservation(reservation)  # already CONSUMED -> no-op
    batch.refresh_from_db()
    assert batch.quantity == 8


def test_consume_reservation_is_idempotent_for_stale_instances(product_with_variant_and_stock):
    """Two workers holding old ACTIVE instances must not deduct the batch twice."""
    from apps.inventory.models import StockReservation

    variant = product_with_variant_and_stock["variant"]
    batch = product_with_variant_and_stock["batch"]
    reservation = reserve_stock(variant=variant, quantity=2)[0]
    first_copy = StockReservation.objects.get(pk=reservation.pk)
    stale_copy = StockReservation.objects.get(pk=reservation.pk)

    consume_reservation(first_copy)
    consume_reservation(stale_copy)

    batch.refresh_from_db()
    assert batch.quantity == 8
    assert InventoryMovement.objects.filter(
        item=reservation.item,
        movement_type=InventoryMovementType.SALE,
    ).count() == 1


def test_release_reservation_frees_up_available_quantity(product_with_variant_and_stock):
    from apps.inventory.services import get_or_create_item, release_reservation

    variant = product_with_variant_and_stock["variant"]
    item = product_with_variant_and_stock["item"]

    reservation = reserve_stock(variant=variant, quantity=10)[0]
    item.refresh_from_db()
    assert get_or_create_item(variant).available_quantity == 0

    release_reservation(reservation)

    assert get_or_create_item(variant).available_quantity == 10
    reservation.refresh_from_db()
    assert reservation.status == StockReservationStatus.RELEASED


def test_expired_cart_reservation_is_released_once_and_marked_expired(product_with_variant_and_stock):
    from apps.inventory.models import StockReservation

    variant = product_with_variant_and_stock["variant"]
    item = product_with_variant_and_stock["item"]
    reservation = reserve_stock(variant=variant, quantity=3, cart_id="00000000-0000-0000-0000-000000000001", ttl_minutes=-1)[0]

    assert release_expired_reservations() == 1
    assert release_expired_reservations() == 0

    reservation.refresh_from_db()
    assert reservation.status == StockReservationStatus.EXPIRED
    assert item.available_quantity == 10
    assert StockReservation.objects.filter(pk=reservation.pk, status=StockReservationStatus.EXPIRED).exists()
    assert InventoryMovement.objects.filter(
        item=item,
        movement_type=InventoryMovementType.RELEASE,
        reference="00000000-0000-0000-0000-000000000001",
    ).count() == 1
