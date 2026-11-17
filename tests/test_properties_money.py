"""
Property-based checks on money and stock (Hypothesis).

1. Random carts — one to six lines of random price, quantity and VAT status —
   with a random coupon and random automatic promotions, random shipping and
   payment route: every total is a non-negative whole number of piastres, the
   parts add up to the grand total exactly, /checkout/summary and the created
   order agree to the piastre, each part matches an independent oracle, and a
   COD deposit plus what the courier collects is the total.

2. Random sequences of orders placed, paid, and cancelled against one variant's
   batches: no batch ever goes negative or above what it was received with, no
   batch is ever promised (reserved) beyond what it holds, an order succeeds
   exactly when the sellable stock covers it, and every unit is accounted for —
   on the shelf or in a live order.

Each example runs inside a savepoint that is rolled back, so examples never see
each other's rows. ~50 examples per property, no deadline.
"""
import uuid
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from apps.carts.models import Cart, CartItem
from apps.catalog.models import TaxCategory
from apps.coupons.models import CouponDiscountType
from apps.inventory.models import Batch, InventoryItem, StockReservation, StockReservationStatus
from apps.orders.models import Order, OrderStatus
from apps.promotions.models import PromotionType
from apps.users.models import Address
from tests import matrix_world as w

pytestmark = pytest.mark.django_db

PROPERTY_SETTINGS = dict(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow, HealthCheck.data_too_large],
    # No example database: nothing is written into the repository, and a
    # failure is reproduced from the printed "Falsifying example" instead.
    database=None,
)


class _Rollback(Exception):
    pass


def isolated(fn):
    """Run `fn` in a savepoint that is always rolled back, with a clean cache."""
    cache.clear()
    try:
        with transaction.atomic():
            fn()
            raise _Rollback
    except _Rollback:
        pass


@pytest.fixture
def world(db):
    return w.build_world()


# ---------------------------------------------------------------------------
# 1. Random carts
# ---------------------------------------------------------------------------

cents = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
line = st.tuples(cents, st.integers(min_value=1, max_value=10), st.booleans())
coupon = st.one_of(
    st.none(),
    st.tuples(st.just("percentage"), st.integers(min_value=1, max_value=100), st.one_of(st.none(), cents)),
    st.tuples(st.just("fixed_amount"), cents, st.none()),
    st.tuples(st.just("free_shipping"), st.just(0), st.none()),
)
promotion = st.one_of(
    st.tuples(st.just("percentage"), st.integers(min_value=1, max_value=60), st.booleans()),
    st.tuples(st.just("fixed_amount"), cents, st.booleans()),
)
cart_case = st.fixed_dictionaries({
    "lines": st.lists(line, min_size=1, max_size=6),
    "coupon": coupon,
    "promotions": st.lists(promotion, max_size=2),
    "route": st.sampled_from(["cod", "cod_deposit_instapay", "cod_deposit_vodafone", "instapay", "vodafone_cash"]),
    "deposit": cents,
    "ship": st.sampled_from(["standard", "express"]),
    "place": st.sampled_from(["cairo", "giza", "aswan"]),
    "signed_in": st.booleans(),
})


def _oracle_promotions(promotions, subtotal):
    """compute_promotions_for_items for whole-cart promotions, in priority order (all distinct)."""
    total = Decimal("0.00")
    non_stackable_applied = False
    for kind, value, stackable in promotions:  # already in descending priority
        if not stackable and non_stackable_applied:
            continue
        amount = w.q(subtotal * Decimal(value) / 100) if kind == "percentage" else w.q(min(Decimal(value), subtotal))
        if amount > 0:
            total += amount
            if not stackable:
                non_stackable_applied = True
    return total


def _oracle_coupon(spec, subtotal):
    if spec is None:
        return Decimal("0.00")
    kind, value, cap = spec
    if kind == "percentage":
        amount = subtotal * Decimal(value) / 100
    elif kind == "fixed_amount":
        amount = min(Decimal(value), subtotal)
    else:
        return Decimal("0.00")
    if cap is not None and amount > cap:
        amount = cap
    return w.q(amount)


AMOUNT_KEYS = ("subtotal", "coupon_discount", "promotion_discount", "tax_total", "shipping_total", "cod_fee", "grand_total")


@hsettings(**PROPERTY_SETTINGS)
@given(case=cart_case)
def test_random_carts_always_add_up(settings, world, case):
    def run():
        method, deposit_route = w.configure(
            settings, case["route"], cod_max=Decimal("0"),  # no ceiling: the property is about arithmetic
            deposit=case["deposit"] if case["route"].startswith("cod_deposit") else Decimal("0"),
        )
        shopper = w.make_shopper("saved_address" if case["signed_in"] else "guest", world.governorates[case["place"]])
        for n, (price, qty, taxable) in enumerate(case["lines"]):
            variant = w.make_variant(
                world.category, world.brand, sku=f"R{n}-{uuid.uuid4().hex[:6]}", price=str(price),
                tax=TaxCategory.STANDARD if taxable else TaxCategory.EXEMPT, stock=50,
            )
            shopper.add(variant, qty)
        code = ""
        if case["coupon"] is not None:
            kind, value, cap = case["coupon"]
            extra = {"maximum_discount_amount": w.Money(cap, "EGP")} if cap is not None else {}
            w.make_coupon("RND", kind=kind, value=value, **extra)
            code = "rnd"
        for priority, (kind, value, stackable) in zip(range(100, 0, -1), case["promotions"]):
            w.make_promotion(kind=kind, pct=value if kind == "percentage" else None,
                             fixed=value if kind == "fixed_amount" else None, stackable=stackable, priority=priority)

        fee, threshold = world.shipping_rule(case["place"], case["ship"])
        method_id = world.methods[case["ship"]].id
        summary = shopper.summary(shipping_method_id=method_id, payment_method=method, coupon_code=code)
        assert summary.status_code == 200, summary.content
        response = shopper.checkout(shipping_method_id=method_id, payment_method=method, coupon_code=code, deposit_route=deposit_route)
        assert response.status_code == 201, response.content
        data = response.json()["data"]
        order = Order.objects.get(order_number=data["order"]["order_number"])

        shown = {k: w.money(summary.json()["data"][k]) for k in AMOUNT_KEYS}
        placed = {k: w.money(data["order"][k]) for k in AMOUNT_KEYS}
        stored = {k: getattr(order, k).amount for k in AMOUNT_KEYS}
        # What the customer was shown is what they were charged, and what was stored.
        assert shown == placed == stored
        for key, value in stored.items():
            assert value >= 0, key
            assert value == value.quantize(w.CENT), key

        # The parts make the whole (the discounted subtotal never below zero).
        discounted = max(stored["subtotal"] - stored["coupon_discount"] - stored["promotion_discount"], Decimal("0"))
        assert stored["grand_total"] == discounted + stored["tax_total"] + stored["shipping_total"] + stored["cod_fee"]

        # Each part against the oracle.
        subtotal = sum((w.q(price * qty) for price, qty, _ in case["lines"]), Decimal("0.00"))
        assert stored["subtotal"] == subtotal
        coupon_want, promotion_want = w.capped_discounts(
            subtotal, _oracle_coupon(case["coupon"], subtotal), _oracle_promotions(case["promotions"], subtotal),
        )
        assert stored["coupon_discount"] == coupon_want
        assert stored["promotion_discount"] == promotion_want
        assert stored["coupon_discount"] + stored["promotion_discount"] <= subtotal
        assert stored["tax_total"] == w.vat_after_discount(case["lines"], coupon_want + promotion_want)
        # Each line carries its share of the discount and the VAT on the rest.
        assert sum(i.discount_amount.amount for i in order.items.all()) == coupon_want + promotion_want
        assert sum(i.tax_amount.amount for i in order.items.all()) == stored["tax_total"]
        free = (case["coupon"] or (None,))[0] == "free_shipping" or (threshold is not None and subtotal >= threshold)
        assert stored["shipping_total"] == (Decimal("0.00") if free else fee)
        assert stored["cod_fee"] == (w.COD_FEE if method == "cod" else Decimal("0.00"))

        # The payments cover the total exactly, the deposit never more than it.
        paid = order.payments.aggregate(s=Sum("amount"))["s"]
        assert paid == stored["grand_total"]
        if case["route"].startswith("cod_deposit"):
            deposit = order.payments.exclude(provider="cod").get()
            assert deposit.amount.amount == min(w.q(case["deposit"]), stored["grand_total"])
            totals = summary.json()["data"]
            assert w.money(totals["deposit_amount"]) == deposit.amount.amount
            assert w.money(totals["due_on_delivery"]) == stored["grand_total"] - deposit.amount.amount
        for payment in order.payments.all():
            assert payment.amount.amount >= 0

    isolated(run)


# ---------------------------------------------------------------------------
# 2. Random reserve / pay / cancel sequences
# ---------------------------------------------------------------------------

op = st.one_of(
    st.tuples(st.just("place_cod"), st.integers(min_value=1, max_value=12)),
    st.tuples(st.just("place_transfer"), st.integers(min_value=1, max_value=12)),
    st.tuples(st.just("pay"), st.integers(min_value=0, max_value=9)),
    st.tuples(st.just("cancel"), st.integers(min_value=0, max_value=9)),
)
stock_case = st.fixed_dictionaries({
    "batches": st.lists(st.tuples(st.integers(min_value=0, max_value=8), st.integers(min_value=1, max_value=900)),
                        min_size=1, max_size=3),
    "ops": st.lists(op, min_size=1, max_size=7),
})


def _place(world, variant, quantity, method):
    from apps.checkout.services import create_order_from_cart

    cart = Cart.objects.create(session_key=uuid.uuid4().hex)
    CartItem.objects.create(cart=cart, variant=variant, quantity=quantity, unit_price_snapshot=variant.price)
    address = Address(full_name="Prop Buyer", phone="+201001234567", governorate=world.governorates["cairo"],
                      district="D", street="S", building="1")
    return create_order_from_cart(
        user=None, cart=cart, address=address, shipping_method_id=world.methods["standard"].id,
        payment_method=method, idempotency_key=str(uuid.uuid4()),
    )


def _assert_stock_invariants(item, initial_total):
    batches = list(Batch.objects.filter(item=item))
    held = dict(
        StockReservation.objects.filter(item=item, status=StockReservationStatus.ACTIVE)
        .values("batch_id").annotate(n=Sum("quantity")).values_list("batch_id", "n")
    )
    for batch in batches:
        assert 0 <= batch.quantity <= batch.initial_quantity, (batch.batch_number, batch.quantity)
        assert held.get(batch.pk, 0) <= batch.quantity, "a batch is promised beyond what it holds"
    item = InventoryItem.objects.get(pk=item.pk)
    on_shelf = sum(b.quantity for b in batches)
    assert item.available_quantity == on_shelf - sum(held.values()) >= 0
    # Every unit is somewhere: on the shelf, or taken by an order that still stands.
    taken = StockReservation.objects.filter(item=item, status=StockReservationStatus.CONSUMED).exclude(
        order__status=OrderStatus.CANCELLED).aggregate(n=Sum("quantity"))["n"] or 0
    assert on_shelf + taken == initial_total
    return item.available_quantity


@hsettings(**PROPERTY_SETTINGS)
@given(case=stock_case)
def test_random_reserve_and_cancel_sequences_never_break_the_stock(settings, world, case):
    from apps.core.error_codes import ApplicationError
    from apps.orders.services import cancel_order
    from apps.payments.services import record_manual_receipt

    def run():
        w.configure(settings, "cod")
        variant = w.make_variant(world.category, world.brand, sku=f"S-{uuid.uuid4().hex[:6]}", price="50.00",
                                 batches=[(qty, days) for qty, days in case["batches"]])
        item = variant.inventory_item
        initial_total = sum(qty for qty, _ in case["batches"])
        available = _assert_stock_invariants(item, initial_total)
        orders = []
        for name, arg in case["ops"]:
            if name in ("place_cod", "place_transfer"):
                method = "cod" if name == "place_cod" else "instapay"
                try:
                    order, _ = _place(world, variant, arg, method)
                except ApplicationError as exc:
                    assert exc.code == "INSUFFICIENT_STOCK"
                    assert arg > available, "refused although the stock was there"
                else:
                    assert arg <= available, "sold more than was there"
                    orders.append(order)
            elif orders:
                order = Order.objects.get(pk=orders[arg % len(orders)].pk)
                if name == "pay":
                    transfer = order.payments.exclude(provider="cod").first()
                    if transfer is not None and order.status == OrderStatus.PENDING:
                        record_manual_receipt(transfer)
                elif name == "cancel" and order.can_cancel():
                    cancel_order(order, reason="property")
            available = _assert_stock_invariants(item, initial_total)
        # Cancel everything still standing: every unit comes home.
        for order in orders:
            order = Order.objects.get(pk=order.pk)
            if order.can_cancel():
                cancel_order(order, reason="end")
        _assert_stock_invariants(item, initial_total)
        assert sum(Batch.objects.filter(item=item).values_list("quantity", flat=True)) == initial_total
        assert not StockReservation.objects.filter(item=item, status=StockReservationStatus.ACTIVE).exists()

    isolated(run)
