"""
"Two customers buying the last unit" race, exercised sequentially against
the same select_for_update()-guarded item row. The second reservation
attempt must observe the first reservation's effect on available_quantity
and fail cleanly rather than oversell.
"""
import pytest

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.inventory.services import get_or_create_item, reserve_stock

pytestmark = pytest.mark.django_db


def test_second_reservation_for_last_unit_raises_insufficient_stock(product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    batch = product_with_variant_and_stock["batch"]
    batch.quantity = 1
    batch.initial_quantity = 1
    batch.save(update_fields=["quantity", "initial_quantity"])

    assert get_or_create_item(variant).available_quantity == 1

    first_reservations = reserve_stock(variant=variant, quantity=1, cart_id=None, order=None)
    assert len(first_reservations) == 1
    assert get_or_create_item(variant).available_quantity == 0

    with pytest.raises(ApplicationError) as exc_info:
        reserve_stock(variant=variant, quantity=1, cart_id=None, order=None)

    assert exc_info.value.code == ErrorCodes.INSUFFICIENT_STOCK


def test_reservation_exactly_matching_available_stock_succeeds_then_next_fails(product_with_variant_and_stock):
    """Sanity check that the boundary (available == requested) still succeeds once."""
    variant = product_with_variant_and_stock["variant"]  # fixture stock = 10

    reservations = reserve_stock(variant=variant, quantity=10, cart_id=None, order=None)
    assert sum(r.quantity for r in reservations) == 10
    assert get_or_create_item(variant).available_quantity == 0

    with pytest.raises(ApplicationError) as exc_info:
        reserve_stock(variant=variant, quantity=1, cart_id=None, order=None)
    assert exc_info.value.code == ErrorCodes.INSUFFICIENT_STOCK
