"""
Query-count budgets for the admin API.

These exist because "the dashboard is slow" turned out to be three copies of one
mistake. Stock figures on `InventoryItem` are sums over child tables exposed as
Python properties, and reading one costs a query. The inventory list rendered
five of them per row, the product list summed two per variant, and the overview
counted low stock by walking every row — so a 20-row page issued ~150 queries
and every page got slower as the shop grew.

A query count is the right thing to assert rather than a duration: wall time on
CI says more about the machine than the code, while "this endpoint must not
issue more queries for 20 rows than for 1" is exactly the property that broke.
`prefetch_related` is deliberately not trusted here — the original code had it,
and it bought nothing, because `aggregate()` runs its own query and never looks
at prefetched rows. Only a test that counts queries catches that.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.inventory.models import (
    Batch,
    InventoryItem,
    StockReservation,
    StockReservationStatus,
)

pytestmark = pytest.mark.django_db


def queries_for(client, url, params):
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url, params)
    assert response.status_code == 200, f"{url} -> {response.status_code}"
    return len(ctx.captured_queries)


@pytest.fixture
def stocked_variants(product_with_variant_and_stock):
    """
    Several inventory items, each with more than one batch and reservation.

    More than one of each matters: a single batch and a single reservation would
    still look correct under an implementation that joins both child tables and
    double-counts, so a fixture with one of each cannot catch the bug this
    guards against.
    """
    items = list(InventoryItem.objects.all()[:5])
    if not items:
        pytest.skip("fixture produced no inventory items")
    for n, item in enumerate(items):
        Batch.objects.create(
            item=item, batch_number=f"perf-{n}-a", initial_quantity=40, quantity=40, is_active=True
        )
        Batch.objects.create(
            item=item, batch_number=f"perf-{n}-b", initial_quantity=20, quantity=20, is_active=True
        )
        StockReservation.objects.create(item=item, quantity=3, status=StockReservationStatus.ACTIVE)
        StockReservation.objects.create(item=item, quantity=4, status=StockReservationStatus.ACTIVE)
    return items


def test_inventory_list_does_not_query_per_row(staff_client, stocked_variants):
    """
    The list must cost the same whether it shows one row or twenty.

    This is the regression that mattered most: the endpoint was issuing eight
    aggregate queries per row, so the page staff use to find low stock was the
    slowest page in the dashboard precisely when there was most stock to look at.
    """
    one = queries_for(staff_client, "/api/v1/admin-api/inventory/", {"page_size": 1})
    twenty = queries_for(staff_client, "/api/v1/admin-api/inventory/", {"page_size": 20})

    assert twenty <= one + 1, (
        f"inventory list scales with page size: {one} queries for 1 row, {twenty} for 20. "
        "Something is reading a stock property off an unannotated queryset."
    )


def test_product_list_does_not_query_per_variant(staff_client, stocked_variants):
    """The stock badge sums every variant — via annotations, not a query each."""
    one = queries_for(staff_client, "/api/v1/admin-api/products/", {"page_size": 1})
    twenty = queries_for(staff_client, "/api/v1/admin-api/products/", {"page_size": 20})

    assert twenty <= one + 1, (
        f"product list scales with page size: {one} queries for 1 row, {twenty} for 20."
    )


def test_inventory_state_filter_is_done_in_sql(staff_client, stocked_variants):
    """
    Filtering by state must not cost more than not filtering.

    It used to load the entire table and test each row in Python, so viewing one
    filtered page scaled with the size of the whole catalogue rather than the
    page.
    """
    unfiltered = queries_for(staff_client, "/api/v1/admin-api/inventory/", {"page_size": 10})
    for state in ("low", "out", "in"):
        filtered = queries_for(
            staff_client, "/api/v1/admin-api/inventory/", {"page_size": 10, "state": state}
        )
        assert filtered <= unfiltered + 1, (
            f"state={state} costs {filtered} queries against {unfiltered} unfiltered"
        )


def test_overview_low_stock_count_is_one_query(staff_client, stocked_variants):
    """
    The landing page counted low stock by iterating every inventory row.

    That is two queries per item on the first screen staff see, so the dashboard
    felt slowest at the moment it was opened.
    """
    with CaptureQueriesContext(connection) as ctx:
        response = staff_client.get("/api/v1/admin-api/overview/")
    assert response.status_code == 200

    stock_sums = [
        q for q in ctx.captured_queries
        if "SUM" in q["sql"] and ("inventory_batch" in q["sql"] or "inventory_stockreservation" in q["sql"])
    ]
    assert len(stock_sums) <= 2, (
        f"overview issued {len(stock_sums)} per-item stock sums; it should aggregate in SQL"
    )


def test_with_stock_matches_the_property_it_replaces(stocked_variants):
    """
    The annotated totals and the fallback properties must agree exactly.

    Two `Sum()` annotations over two reverse foreign keys in one query join both
    child tables, repeating every batch once per reservation and inflating both
    totals — silently, and only for items that have several of each. That is why
    the fixture gives every item two batches and two reservations, and why this
    compares against the un-annotated path rather than against fixed numbers.
    """
    ids = [item.pk for item in stocked_variants]
    annotated = {i.pk: i for i in InventoryItem.objects.with_stock().filter(pk__in=ids)}

    for pk in ids:
        plain = InventoryItem.objects.get(pk=pk)       # falls back to aggregate()
        ann = annotated[pk]
        # Compared against the un-annotated path rather than fixed numbers: the
        # invariant is that the two agree, and hard-coding totals would only
        # assert what the fixture happens to create today.
        assert ann.quantity_on_hand == plain.quantity_on_hand
        assert ann.reserved_quantity == plain.reserved_quantity
        assert ann.available_quantity == plain.available_quantity
        assert ann.is_low_stock == plain.is_low_stock
        assert ann.is_out_of_stock == plain.is_out_of_stock
        # And that the sums are the real ones, not inflated by the join between
        # batches and reservations that a two-Sum annotation would produce.
        assert ann.reserved_quantity == 7, "reservations were double-counted"
        assert ann.available_quantity == max(ann.quantity_on_hand - 7, 0)


def test_with_stock_is_idempotent(stocked_variants):
    """Chaining the helpers must not raise by annotating the same alias twice."""
    qs = InventoryItem.objects.with_stock().with_stock().low_stock()
    list(qs)  # executes without an alias conflict


def test_stock_states_partition_the_catalogue(stocked_variants):
    """
    Every item is low, out, or healthy — exactly one of the three.

    The dashboard presents these as a filter, so an item falling in two buckets
    or none is a row an operator can never reconcile against the total.
    """
    total = InventoryItem.objects.count()
    low = set(InventoryItem.objects.low_stock().values_list("pk", flat=True))
    out = set(InventoryItem.objects.out_of_stock().values_list("pk", flat=True))
    healthy = set(InventoryItem.objects.healthy_stock().values_list("pk", flat=True))

    assert not (low & out) and not (low & healthy) and not (out & healthy)
    assert len(low) + len(out) + len(healthy) == total


def test_inactive_batches_and_settled_reservations_are_excluded(stocked_variants):
    """A released reservation is not a hold, and an inactive batch is not stock."""
    item = stocked_variants[0]
    before = InventoryItem.objects.with_stock().get(pk=item.pk)
    on_hand_before, reserved_before = before.quantity_on_hand, before.reserved_quantity

    Batch.objects.create(
        item=item, batch_number="ignored", initial_quantity=500, quantity=500, is_active=False
    )
    StockReservation.objects.create(
        item=item, quantity=99, status=StockReservationStatus.RELEASED
    )

    after = InventoryItem.objects.with_stock().get(pk=item.pk)
    assert after.quantity_on_hand == on_hand_before, "an inactive batch was counted as stock"
    assert after.reserved_quantity == reserved_before, "a released reservation was counted as a hold"
