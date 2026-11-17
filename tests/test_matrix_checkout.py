"""
The checkout matrix.

customer   {guest, signed-in with a saved address, signed-in adding an address}
x payment  {cod, cod + deposit by InstaPay, cod + deposit by Vodafone Cash, InstaPay, Vodafone Cash}
x shipping {standard, express}
x place    {Cairo, Giza (same zone), Aswan (another zone)}
x subtotal {a piastre under the free-shipping threshold, exactly at it, over it}
x discount {none, % coupon, fixed coupon, automatic promotion, coupon + promotion}

Every cell places a real order through /cart, /checkout/summary and /checkout
and checks, to the piastre: the totals against an independent oracle and
against the summary the customer was shown; that shipping is free exactly when
the threshold rule says so; the deposit split; which payments exist, for how
much and in what state; the stock held or taken; the coupon use; and what the
response tells the customer about where to pay.

Then the refusal matrix: every malformed or impossible checkout gets its exact
HTTP status and error code, and leaves no order, no payment and no stock moved.
"""
import datetime
import itertools
import os
import uuid
from decimal import Decimal

import pytest
from django.db.models import Sum
from django.utils import timezone

from apps.carts.models import Cart, CartStatus
from apps.coupons.models import CouponDiscountType, CouponUsage
from apps.inventory.models import Batch, StockReservation, StockReservationStatus
from apps.orders.models import Order, OrderStatus
from apps.payments.models import Payment, PaymentStatus
from tests import matrix_world as w

pytestmark = pytest.mark.django_db

SUBTOTAL_LEVELS = ("under", "at", "over")
NOMINAL_THRESHOLD = Decimal("750.00")  # for the one rate that is never free


def _target_subtotal(threshold, level):
    base = threshold if threshold is not None else NOMINAL_THRESHOLD
    return {"under": base - Decimal("0.01"), "at": base, "over": base + Decimal("123.45")}[level]


@pytest.fixture
def world(db):
    return w.build_world()


def _fill_cart(world, shopper, subtotal):
    """Two lines: 2 x 100.00 VAT-able, plus one zero-rated jar priced to land on `subtotal`."""
    price = subtotal - Decimal("200.00")
    world.zero_rated.price = price
    world.zero_rated.save(update_fields=["price"])
    shopper.add(world.taxed, 2)
    shopper.add(world.zero_rated, 1)
    return [(Decimal("100.00"), 2, True), (price, 1, False)]


def _stock(variant):
    return sum(Batch.objects.filter(item__variant=variant).values_list("quantity", flat=True))


def _assert_totals(source, exp, *, summary=False):
    keys = ("subtotal", "coupon_discount", "promotion_discount", "tax_total", "shipping_total", "cod_fee", "grand_total")
    got = {k: w.money(source[k]) for k in keys}
    want = {k: getattr(exp, k) for k in keys}
    assert got == want
    if summary:
        if exp.deposit is None:
            assert source["deposit_amount"] is None and source["due_on_delivery"] is None
        else:
            assert w.money(source["deposit_amount"]) == exp.deposit
            assert w.money(source["due_on_delivery"]) == exp.due_on_delivery


SHIPS = ("standard", "express")
PLACES = ("cairo", "giza", "aswan")
ROUTES = tuple(w.PAYMENT_ROUTES)


def _cases():
    """
    The full cross product is 3 x 5 x 2 x 3 x 3 x 5 = 1350 orders (~7 minutes).
    MATRIX_FULL=1 runs all of it (the nightly job should). The default run is a
    covering subset that still holds every factor against every other:

    A. every customer x payment x discount x subtotal level (225), each on a
       shipping method / place mixed across the six combinations, and
    B. every payment x shipping x place x subtotal level (90) — the cells where
       the threshold rule and the zone fees meet — with customer and discount
       rotated.
    """
    full = list(itertools.product(w.CUSTOMERS, ROUTES, SHIPS, PLACES, SUBTOTAL_LEVELS, w.DISCOUNTS))
    if os.environ.get("MATRIX_FULL"):
        return full
    ship_places = list(itertools.product(SHIPS, PLACES))
    chosen = set()
    for c, p, d, lv in itertools.product(range(3), range(5), range(5), range(3)):
        ship, place = ship_places[(c * 7 + p * 5 + d * 3 + lv) % 6]
        chosen.add((w.CUSTOMERS[c], ROUTES[p], ship, place, SUBTOTAL_LEVELS[lv], w.DISCOUNTS[d]))
    for i, (p, ship, place, lv) in enumerate(itertools.product(ROUTES, SHIPS, PLACES, SUBTOTAL_LEVELS)):
        chosen.add((w.CUSTOMERS[i % 3], p, ship, place, lv, w.DISCOUNTS[(i // 3) % 5]))
    return [case for case in full if case in chosen]


CASES = _cases()


@pytest.mark.parametrize(
    "customer,route,ship,place,level,discount", CASES, ids=["-".join(case) for case in CASES],
)
def test_checkout_matrix(settings, world, customer, route, ship, place, level, discount):
    method, deposit_route = w.configure(settings, route)
    governorate = world.governorates[place]
    fee, threshold = world.shipping_rule(place, ship)
    shopper = w.make_shopper(customer, governorate)
    lines = _fill_cart(world, shopper, _target_subtotal(threshold, level))
    code = w.apply_discount(discount)
    deposit = w.PAYMENT_ROUTES[route][2]
    exp = w.expected_totals(
        lines=lines, shipping_fee=fee, threshold=threshold, payment_method=method, discount=discount, deposit=deposit,
    )
    stock_before = {v.pk: _stock(v) for v in (world.taxed, world.zero_rated)}
    shipping_method_id = world.methods[ship].id

    # --- the summary the customer is shown ---------------------------------
    summary = shopper.summary(shipping_method_id=shipping_method_id, payment_method=method, coupon_code=code)
    assert summary.status_code == 200, summary.content
    _assert_totals(summary.json()["data"], exp, summary=True)

    # --- the order ----------------------------------------------------------
    response = shopper.checkout(
        shipping_method_id=shipping_method_id, payment_method=method, coupon_code=code, deposit_route=deposit_route,
    )
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    order = Order.objects.get(order_number=data["order"]["order_number"])
    _assert_totals(data["order"], exp)

    # Stored to the piastre, and the parts add up to the whole.
    assert order.grand_total.amount == exp.grand_total
    assert order.subtotal.amount - order.coupon_discount.amount - order.promotion_discount.amount \
        + order.tax_total.amount + order.shipping_total.amount + order.cod_fee.amount == order.grand_total.amount
    # Free shipping exactly per the threshold rule (undiscounted subtotal >= threshold).
    assert (order.shipping_total.amount == 0) == (threshold is not None and level in ("at", "over"))

    # Who placed it.
    assert order.user_id == (shopper.user.id if shopper.user else None)
    assert ("access_token" in data) == (shopper.user is None)
    assert order.customer_phone in (w.GUEST_PHONE_E164, "+201001112223")
    assert order.shipping_address_snapshot["governorate_en"] == governorate.name_en
    assert order.shipping_method_name == world.methods[ship].name
    assert order.payment_method == method
    # The cart was spent: exactly one converted, and nothing left in an open one.
    assert Cart.objects.filter(status=CartStatus.CONVERTED).count() == 1
    assert shopper.client.get(f"{w.API}/cart/").json()["data"]["items"] == []

    # Coupon use reserved at checkout, exactly once.
    assert CouponUsage.objects.filter(order=order).count() == (1 if code else 0)
    if code:
        assert order.coupon_code == code

    # --- payments -----------------------------------------------------------
    payments = {p.provider: p for p in order.payments.all()}
    payment = data["payment"]
    if route == "cod":
        assert set(payments) == {"cod"}
        assert payments["cod"].amount.amount == exp.grand_total
        assert payments["cod"].status == PaymentStatus.PENDING  # collected at the door, not before
        assert order.status == OrderStatus.CONFIRMED
        assert payment["provider"] == "cod" and payment["pay_to"] == "" and payment["pay_link"] == ""
    elif route.startswith("cod_deposit"):
        provider = "instapay" if deposit_route == "instapay" else "vodafone_cash"
        assert set(payments) == {"cod", provider}
        assert payments[provider].amount.amount == exp.deposit == min(w.DEPOSIT, exp.grand_total)
        assert payments["cod"].amount.amount == exp.due_on_delivery == exp.grand_total - exp.deposit
        assert payments[provider].status == PaymentStatus.REQUIRES_ACTION
        assert payments["cod"].status == PaymentStatus.PENDING
        assert order.status == OrderStatus.PENDING  # waits for its deposit
        assert payment["id"] == payments[provider].id and payment["role"] == "deposit"
        assert w.money(payment["amount"]) == exp.deposit
        assert w.money(payment["due_on_delivery"]) == exp.due_on_delivery
        assert payment["pay_reference"] == order.order_number
        if provider == "instapay":
            assert payment["pay_to"] == w.INSTAPAY["ADDRESS"]
            assert payment["pay_link"] == w.INSTAPAY["PAY_LINK"]
        else:
            assert payment["pay_to"] == w.WALLET_NUMBER and payment["pay_link"] == ""
        assert f"{exp.deposit:.2f}" in payment["instructions_ar"]
    else:
        provider = route  # instapay / vodafone_cash, the whole order by transfer
        assert set(payments) == {provider}
        assert payments[provider].amount.amount == exp.grand_total
        assert payments[provider].status == PaymentStatus.REQUIRES_ACTION
        assert payments[provider].method == method
        assert order.status == OrderStatus.PENDING
        assert payment["role"] == "" and w.money(payment["amount"]) == exp.grand_total
        assert payment["pay_reference"] == order.order_number
        if provider == "instapay":
            assert payment["pay_to"] == w.INSTAPAY["ADDRESS"] and payment["pay_link"] == w.INSTAPAY["PAY_LINK"]
        else:
            assert payment["pay_to"] == w.WALLET_NUMBER and payment["pay_link"] == ""

    # --- stock ----------------------------------------------------------------
    reservations = StockReservation.objects.filter(order=order)
    assert {r.item.variant_id: r.quantity for r in reservations} == {world.taxed.pk: 2, world.zero_rated.pk: 1}
    taken = order.status == OrderStatus.CONFIRMED
    want_status = StockReservationStatus.CONSUMED if taken else StockReservationStatus.ACTIVE
    assert set(reservations.values_list("status", flat=True)) == {want_status}
    assert _stock(world.taxed) == stock_before[world.taxed.pk] - (2 if taken else 0)
    assert _stock(world.zero_rated) == stock_before[world.zero_rated.pk] - (1 if taken else 0)
    # Either way the units are no longer offered to anybody else.
    assert world.taxed.inventory_item.available_quantity == stock_before[world.taxed.pk] - 2


# ---------------------------------------------------------------------------
# Deposit edges: never more than the order is worth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("deposit_route", ["instapay", "vodafone_cash"])
@pytest.mark.parametrize("deposit", ["0.01", "50.00", "263.00", "263.01", "100000.00"])
def test_the_deposit_is_capped_at_the_order_total(settings, world, customer, deposit_route, deposit):
    w.configure(settings, "cod", deposit=Decimal(deposit))
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 2)  # 200 + 28 VAT + 30 shipping + 15 COD fee = 273.00 ... minus nothing
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod",
                                deposit_route=deposit_route)
    assert response.status_code == 201, response.content
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    assert order.grand_total.amount == Decimal("273.00")
    provider = "instapay" if deposit_route == "instapay" else "vodafone_cash"
    dep = order.payments.get(provider=provider)
    cod = order.payments.get(provider="cod")
    assert dep.amount.amount == min(Decimal(deposit), order.grand_total.amount)
    assert cod.amount.amount == order.grand_total.amount - dep.amount.amount
    assert cod.amount.amount >= 0


def test_blank_deposit_route_picks_the_first_configured_one(settings, world):
    w.configure(settings, "cod_deposit_vodafone")
    shopper = w.make_shopper("guest", world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", deposit_route="")
    assert response.status_code == 201, response.content
    assert response.json()["data"]["payment"]["provider"] == "vodafone_cash"


@pytest.mark.parametrize("route,provider", [("instapay", "instapay"), ("vodafone_cash", "vodafone_cash")])
def test_deposit_route_is_ignored_for_a_transfer_order(settings, world, route, provider):
    method, _ = w.configure(settings, route, deposit=w.DEPOSIT)
    shopper = w.make_shopper("guest", world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method=method,
                                deposit_route="instapay")
    assert response.status_code == 201, response.content
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    assert list(order.payments.values_list("provider", flat=True)) == [provider]
    assert order.payments.get().amount == order.grand_total


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _nothing_happened(world, before):
    assert not Order.objects.exists()
    assert not Payment.objects.exists()
    assert not StockReservation.objects.exists()
    assert not CouponUsage.objects.exists()
    assert {v.pk: _stock(v) for v in (world.taxed, world.zero_rated)} == before


def _error(response):
    return response.status_code, response.json()["errors"].get("code")


@pytest.fixture
def cod_on(settings):
    w.configure(settings, "cod")


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize(
    "quantity,status,code",
    [
        (0, 400, "VALIDATION_ERROR"),
        (-1, 400, "VALIDATION_ERROR"),
        (1.5, 400, "VALIDATION_ERROR"),
        ("1.5", 400, "VALIDATION_ERROR"),
        ("two", 400, "VALIDATION_ERROR"),
        (None, 400, "VALIDATION_ERROR"),
        (101, 400, "VALIDATION_ERROR"),  # over MAX_CART_ITEM_QUANTITY
        (2**31, 400, "VALIDATION_ERROR"),
        (10**30, 400, "VALIDATION_ERROR"),
    ],
)
def test_a_bad_quantity_never_reaches_the_cart(cod_on, world, customer, quantity, status, code):
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    response = shopper.client.post(f"{w.API}/cart/items/", {"variant_id": world.taxed.id, "quantity": quantity}, format="json")
    assert _error(response) == (status, code)
    empty = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert _error(empty) == (400, "CART_EMPTY")
    assert not Order.objects.exists()


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("over_by", [1, 50])
def test_more_than_is_in_stock_is_refused_at_checkout(settings, db, customer, over_by):
    w.configure(settings, "cod")
    world = w.build_world(stock=5)
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 5 + over_by)
    before = {v.pk: _stock(v) for v in (world.taxed, world.zero_rated)}
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert _error(response) == (409, "INSUFFICIENT_STOCK")
    _nothing_happened(world, before)
    # ... and exactly the stock is still sellable.
    shopper.client.patch(
        f"{w.API}/cart/items/{shopper.client.get(f'{w.API}/cart/').json()['data']['items'][0]['id']}/",
        {"quantity": 5}, format="json",
    )
    assert shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod").status_code == 201


def test_a_line_spread_over_two_batches_is_reserved_across_both(settings, db):
    w.configure(settings, "cod")
    world = w.build_world()
    variant = w.make_variant(world.category, world.brand, sku="SPLIT", price="80.00", batches=[(3, 30), (4, 400)])
    shopper = w.make_shopper("guest", world.governorates["cairo"])
    shopper.add(variant, 7)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert response.status_code == 201, response.content
    assert list(Batch.objects.filter(item__variant=variant).order_by("expiration_date").values_list("quantity", flat=True)) == [0, 0]
    shopper2 = w.make_shopper("guest", world.governorates["cairo"])
    shopper2.add(variant, 1)
    assert _error(shopper2.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")) == (409, "INSUFFICIENT_STOCK")


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize(
    "shipping_method_id,status,code",
    [
        (0, 400, "VALIDATION_ERROR"),
        (-1, 400, "VALIDATION_ERROR"),
        ("missing", 400, "VALIDATION_ERROR"),
        ("abc", 400, "VALIDATION_ERROR"),
        (None, 400, "VALIDATION_ERROR"),
        ("unknown", 404, "SHIPPING_METHOD_NOT_FOUND"),
        ("inactive", 404, "SHIPPING_METHOD_NOT_FOUND"),
        ("other_zone_only", 404, "SHIPPING_METHOD_NOT_FOUND"),
    ],
)
def test_a_bad_shipping_method_is_refused(cod_on, world, customer, shipping_method_id, status, code):
    from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone

    if shipping_method_id == "unknown":
        shipping_method_id = 999999
    elif shipping_method_id == "inactive":
        shipping_method_id = world.methods["express"].id
        ShippingMethod.objects.filter(pk=shipping_method_id).update(is_active=False)
    elif shipping_method_id == "other_zone_only":
        # A method that delivers only to Aswan, asked for in Cairo.
        method = ShippingMethod.objects.create(name="Upper Egypt", method_type="standard", is_active=True)
        ShippingRate.objects.create(zone=ShippingZone.objects.get(name="Rest of Egypt"), method=method, base_price=20)
        shipping_method_id = method.id
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    before = {v.pk: _stock(v) for v in (world.taxed, world.zero_rated)}
    body = shopper.checkout_body(shipping_method_id=shipping_method_id, payment_method="cod")
    if shipping_method_id == "missing":
        del body["shipping_method_id"]
    for response in (
        shopper.client.post(f"{w.API}/checkout/summary/", {k: v for k, v in body.items() if k not in ("contact", "address", "deposit_route")}
                            | ({"governorate": world.governorates["cairo"].id} if shopper.user is None else {}), format="json"),
        shopper.checkout(body=body),
    ):
        assert _error(response) == (status, code), response.content
    _nothing_happened(world, before)


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("withdrawn", ["variant_inactive", "product_unpublished", "product_draft", "product_archived"])
def test_an_item_withdrawn_after_it_was_carted_cannot_be_bought(cod_on, world, customer, withdrawn):
    from apps.catalog.models import Product, ProductStatus, ProductVariant

    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    product = world.taxed.product
    if withdrawn == "variant_inactive":
        ProductVariant.objects.filter(pk=world.taxed.pk).update(is_active=False)
    elif withdrawn == "product_unpublished":
        Product.objects.filter(pk=product.pk).update(published=False)
    else:
        status = ProductStatus.DRAFT if withdrawn == "product_draft" else ProductStatus.ARCHIVED
        Product.objects.filter(pk=product.pk).update(status=status)
    before = {v.pk: _stock(v) for v in (world.taxed, world.zero_rated)}
    summary = shopper.summary(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert _error(summary) == (404, "VARIANT_NOT_FOUND")
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert _error(response) == (404, "VARIANT_NOT_FOUND")
    _nothing_happened(world, before)


@pytest.mark.parametrize("customer", w.CUSTOMERS)
def test_an_inactive_variant_cannot_be_added(cod_on, world, customer):
    from apps.catalog.models import ProductVariant

    ProductVariant.objects.filter(pk=world.taxed.pk).update(is_active=False)
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    response = shopper.client.post(f"{w.API}/cart/items/", {"variant_id": world.taxed.id, "quantity": 1}, format="json")
    assert _error(response) == (404, "VARIANT_NOT_FOUND")
    response = shopper.client.post(f"{w.API}/cart/items/", {"variant_id": 999999, "quantity": 1}, format="json")
    assert _error(response) == (404, "VARIANT_NOT_FOUND")


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("why", ["no_batches", "all_sold", "only_expired", "expires_today", "batch_inactive", "reserved_by_another"])
def test_an_out_of_stock_variant_is_refused(settings, db, customer, why):
    from apps.inventory.models import Batch

    w.configure(settings, "cod")
    world = w.build_world()
    days = {"only_expired": -3, "expires_today": 0}.get(why, 200)
    stock = 0 if why in ("no_batches", "all_sold") else 3
    batches = [] if why == "no_batches" else [(stock, days)]
    variant = w.make_variant(world.category, world.brand, sku="SCARCE", price="90.00", batches=batches or [(0, 1)])
    if why == "no_batches":
        Batch.objects.filter(item__variant=variant).delete()
    if why == "batch_inactive":
        Batch.objects.filter(item__variant=variant).update(is_active=False)
    if why == "reserved_by_another":
        rival = w.make_shopper("guest", world.governorates["cairo"])
        rival.add(variant, 3)
        rival_order = rival.checkout(shipping_method_id=world.methods["standard"].id, payment_method="instapay")
        assert rival_order.status_code == 201  # holds all three while the transfer is awaited
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(variant, 1)
    orders_before = Order.objects.count()
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert _error(response) == (409, "INSUFFICIENT_STOCK"), response.content
    assert Order.objects.count() == orders_before


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize(
    "cod_max,cod_min,quantity,expect",
    [
        ("200.00", "0", 2, 400),     # 200 + 28 + 30 = 258 payable > 200
        ("258.00", "0", 2, 201),     # exactly at the ceiling: allowed
        ("257.99", "0", 2, 400),     # a piastre over
        ("0", "0", 90, 201),         # 0 = no ceiling
        ("50000.00", "259.00", 2, 400),  # under the minimum (fee not counted towards it)
        ("50000.00", "258.00", 2, 201),
    ],
)
def test_cod_limits(settings, world, customer, cod_max, cod_min, quantity, expect):
    w.configure(settings, "cod", cod_max=Decimal(cod_max), cod_min=Decimal(cod_min))
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, quantity)
    summary = shopper.summary(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    if expect == 201:
        assert summary.status_code == 200 and response.status_code == 201, response.content
    else:
        assert _error(summary) == (400, "PAYMENT_METHOD_NOT_AVAILABLE")
        assert _error(response) == (400, "PAYMENT_METHOD_NOT_AVAILABLE")
        assert not Order.objects.exists() and not StockReservation.objects.exists()
        # Paying by transfer instead is still possible.
        other = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="instapay")
        assert other.status_code == 201, other.content


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize(
    "method,providers",
    [
        ("instapay", {"INSTAPAY": {"ENABLED": False}}),
        ("instapay", {"INSTAPAY": {**w.INSTAPAY, "ADDRESS": "  "}}),
        ("mobile_wallet", {"VODAFONE_CASH": {"ENABLED": False}}),
        ("mobile_wallet", {"VODAFONE_CASH": {**w.WALLET, "NUMBER": ""}}),
        ("card", {}),  # Paymob is off in this world
        ("cod", {"COD": {"ENABLED": False}}),
    ],
)
def test_a_payment_method_that_is_switched_off_is_refused(settings, world, customer, method, providers):
    settings.PAYMENT_PROVIDERS = w.payment_providers(**providers)
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    for response in (
        shopper.summary(shipping_method_id=world.methods["standard"].id, payment_method=method),
        shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method=method),
    ):
        assert _error(response) == (400, "PAYMENT_METHOD_NOT_AVAILABLE"), response.content
    assert not Order.objects.exists()


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("method", ["bitcoin", "", "COD", "fawry", None])
def test_an_unknown_payment_method_is_a_validation_error(cod_on, world, customer, method):
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method=method)
    assert _error(response) == (400, "VALIDATION_ERROR")
    assert not Order.objects.exists()


@pytest.mark.parametrize("customer", w.CUSTOMERS)
def test_an_unavailable_deposit_route_is_refused(settings, world, customer):
    w.configure(settings, "cod_deposit_vodafone", INSTAPAY={"ENABLED": False})
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", deposit_route="instapay")
    assert _error(response) == (400, "PAYMENT_METHOD_NOT_AVAILABLE")
    assert not Order.objects.exists() and not StockReservation.objects.exists()
    bogus = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", deposit_route="cash")
    assert _error(bogus) == (400, "VALIDATION_ERROR")


# --- coupons -------------------------------------------------------------------


def _coupon_case(case, world, shopper):
    """Create the coupon for one refusal case; returns (code, expected status, expected error code)."""
    now = timezone.now()
    day = datetime.timedelta(days=1)
    if case == "unknown":
        return "NOPE", 400, "INVALID_COUPON"
    if case == "inactive":
        w.make_coupon("OFF", is_active=False)
        return "OFF", 400, "INVALID_COUPON"
    if case == "not_started":
        w.make_coupon("SOON", start_date=now + day)
        return "SOON", 400, "INVALID_COUPON"
    if case == "expired":
        w.make_coupon("OLD", expiration_date=now - day)
        return "OLD", 400, "COUPON_EXPIRED"
    if case == "min_not_met":
        w.make_coupon("BIG", minimum_order_amount=w.Money(Decimal("100.01"), "EGP"))
        return "BIG", 400, "COUPON_MINIMUM_NOT_MET"
    if case == "used_up":
        coupon = w.make_coupon("GONE", usage_limit=1, per_user_limit=None)
        _spend(world, coupon)
        return "GONE", 409, "COUPON_USAGE_LIMIT_REACHED"
    if case == "per_customer":
        coupon = w.make_coupon("ONCE", per_user_limit=1)
        _spend(world, coupon, user=shopper.user, phone=w.GUEST_PHONE_E164 if shopper.user is None else "+201001112223")
        return "ONCE", 409, "COUPON_USAGE_LIMIT_REACHED"
    if case == "not_applicable":
        coupon = w.make_coupon("SCOPED")
        coupon.products.add(world.zero_rated.product)
        return "SCOPED", 400, "COUPON_NOT_APPLICABLE"
    if case == "someone_elses":
        from apps.users.models import User

        owner = User.objects.create_user(email="owner@example.com", password="x-Secret-123")
        w.make_coupon("MINE", owner=owner)
        return "MINE", 400, "INVALID_COUPON"
    raise ValueError(case)


def _spend(world, coupon, *, user=None, phone="+201009998887"):
    """A previous, real order that used the coupon."""
    from apps.coupons.services import record_usage
    from apps.orders.services import create_order_with_unique_number

    order = create_order_with_unique_number(
        user=user, currency="EGP", customer_name="Earlier", customer_email="", customer_phone=phone,
        payment_method="cod", subtotal=w.Money(100, "EGP"), grand_total=w.Money(100, "EGP"), coupon_code=coupon.code,
    )
    record_usage(coupon=coupon, user=user, order=order, discount_amount=w.Money(10, "EGP"))


COUPON_CASES = ["unknown", "inactive", "not_started", "expired", "min_not_met", "used_up", "per_customer",
                "not_applicable", "someone_elses"]


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("case", COUPON_CASES)
def test_an_unusable_coupon_is_refused_at_summary_and_checkout(cod_on, world, customer, case):
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)  # 100.00
    code, status, error = _coupon_case(case, world, shopper)
    orders_before = Order.objects.count()
    usages_before = CouponUsage.objects.count()
    for response in (
        shopper.summary(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code=code),
        shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code=code),
    ):
        assert _error(response) == (status, error), response.content
    assert Order.objects.count() == orders_before
    assert CouponUsage.objects.count() == usages_before
    # Without the code the same cart checks out fine.
    assert shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod").status_code == 201


@pytest.mark.parametrize("customer", w.CUSTOMERS)
def test_a_coupon_at_exactly_its_minimum_applies(cod_on, world, customer):
    w.make_coupon("EXACT", minimum_order_amount=w.Money(Decimal("100.00"), "EGP"))
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code="exact")
    assert response.status_code == 201, response.content
    assert w.money(response.json()["data"]["order"]["coupon_discount"]) == Decimal("10.00")


def test_a_single_use_coupon_cannot_be_spent_twice_by_a_guest_changing_phone_format(cod_on, world):
    w.make_coupon("ONCE", per_user_limit=1)
    for n, phone in enumerate(["01001234567", "+201001234567", "+20 100 123 4567"]):
        shopper = w.make_shopper("guest", world.governorates["cairo"])
        shopper.add(world.taxed, 1)
        body = shopper.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code="ONCE")
        body["contact"]["phone"] = phone
        response = shopper.checkout(body=body)
        if n == 0:
            assert response.status_code == 201, response.content
        else:
            assert _error(response) == (409, "COUPON_USAGE_LIMIT_REACHED")


def test_cancelling_gives_the_coupon_use_back(cod_on, world):
    from apps.orders.services import cancel_order

    w.make_coupon("ONCE", per_user_limit=1, usage_limit=1)
    shopper = w.make_shopper("saved_address", world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    first = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code="ONCE")
    assert first.status_code == 201
    cancel_order(Order.objects.get(), reason="changed mind")
    assert not CouponUsage.objects.exists()
    shopper.add(world.taxed, 1)
    again = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code="ONCE")
    assert again.status_code == 201, again.content


# --- idempotency -------------------------------------------------------------


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("route", ["cod", "cod_deposit_vodafone", "instapay"])
def test_replaying_the_same_key_and_body_returns_the_same_order(settings, world, customer, route):
    method, deposit_route = w.configure(settings, route)
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 2)
    key = str(uuid.uuid4())
    body = shopper.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method=method, deposit_route=deposit_route)
    first = shopper.checkout(key=key, body=body)
    second = shopper.checkout(key=key, body=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["order"]["order_number"] == second.json()["data"]["order"]["order_number"]
    assert first.json()["data"]["payment"]["id"] == second.json()["data"]["payment"]["id"]
    assert Order.objects.count() == 1
    assert Payment.objects.count() == (2 if route.startswith("cod_deposit") else 1)
    assert StockReservation.objects.aggregate(n=Sum("quantity"))["n"] == 2


@pytest.mark.parametrize("customer", w.CUSTOMERS)
def test_reusing_a_key_with_a_different_body_is_a_conflict(cod_on, world, customer):
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    key = str(uuid.uuid4())
    assert shopper.checkout(key=key, shipping_method_id=world.methods["standard"].id, payment_method="cod").status_code == 201
    response = shopper.checkout(key=key, shipping_method_id=world.methods["express"].id, payment_method="cod")
    assert _error(response) == (409, "IDEMPOTENCY_KEY_CONFLICT")
    assert Order.objects.count() == 1


@pytest.mark.parametrize("customer", w.CUSTOMERS)
@pytest.mark.parametrize("header", [None, ""])
def test_a_missing_key_is_refused(cod_on, world, customer, header):
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    body = shopper.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    extra = {} if header is None else {"HTTP_IDEMPOTENCY_KEY": header}
    response = shopper.client.post(f"{w.API}/checkout/", body, format="json", **extra)
    assert _error(response) == (400, "IDEMPOTENCY_KEY_REQUIRED")
    assert not Order.objects.exists()


@pytest.mark.parametrize("customer", w.CUSTOMERS)
def test_a_new_key_after_the_cart_was_spent_cannot_place_a_second_order(cod_on, world, customer):
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    body = shopper.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert shopper.checkout(body=body).status_code == 201
    response = shopper.checkout(body=body)  # fresh key, cart now empty
    assert _error(response) == (400, "CART_EMPTY")
    assert Order.objects.count() == 1


def test_a_key_seen_by_the_payment_table_but_not_the_cache_cannot_attach_another_order(cod_on, world):
    """The cache was flushed between two checkouts that reused a key."""
    from django.core.cache import cache

    key = str(uuid.uuid4())
    one = w.make_shopper("guest", world.governorates["cairo"])
    one.add(world.taxed, 1)
    assert one.checkout(key=key, shipping_method_id=world.methods["standard"].id, payment_method="cod").status_code == 201
    cache.clear()
    two = w.make_shopper("guest", world.governorates["cairo"])
    two.add(world.taxed, 1)
    response = two.checkout(key=key, shipping_method_id=world.methods["standard"].id, payment_method="cod")
    assert _error(response) == (409, "IDEMPOTENCY_KEY_CONFLICT")
    assert Order.objects.count() == 1


# --- addresses ---------------------------------------------------------------


def test_a_signed_in_customer_cannot_check_out_to_someone_elses_address(cod_on, world):
    victim = w.make_shopper("saved_address", world.governorates["cairo"])
    thief = w.make_shopper("saved_address", world.governorates["cairo"])
    thief.add(world.taxed, 1)
    thief.address_id = victim.address_id
    assert _error(thief.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")) == (404, "ADDRESS_NOT_FOUND")
    assert not Order.objects.exists()


def test_a_guest_cannot_name_a_saved_address(cod_on, world):
    victim = w.make_shopper("saved_address", world.governorates["cairo"])
    guest = w.make_shopper("guest", world.governorates["cairo"])
    guest.add(world.taxed, 1)
    body = guest.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    body["address_id"] = victim.address_id
    assert _error(guest.checkout(body=body)) == (404, "ADDRESS_NOT_FOUND")


@pytest.mark.parametrize("missing", ["district", "building", "governorate"])
def test_a_guest_address_missing_what_the_courier_needs_is_refused(cod_on, world, missing):
    guest = w.make_shopper("guest", world.governorates["cairo"])
    guest.add(world.taxed, 1)
    body = guest.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    del body["address"][missing]
    assert _error(guest.checkout(body=body)) == (400, "VALIDATION_ERROR")


@pytest.mark.parametrize("phone", ["", "123", "0100123456", "not a phone"])
def test_a_guest_needs_a_real_phone(cod_on, world, phone):
    guest = w.make_shopper("guest", world.governorates["cairo"])
    guest.add(world.taxed, 1)
    body = guest.checkout_body(shipping_method_id=world.methods["standard"].id, payment_method="cod")
    body["contact"]["phone"] = phone
    assert _error(guest.checkout(body=body)) == (400, "VALIDATION_ERROR")


def test_an_inactive_governorate_cannot_be_shipped_to(cod_on, world):
    from apps.shipping.models import Governorate

    Governorate.objects.filter(pk=world.governorates["aswan"].pk).update(is_active=False)
    guest = w.make_shopper("guest", world.governorates["aswan"])
    guest.add(world.taxed, 1)
    assert _error(guest.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod")) == (400, "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Money rules: discounts capped, VAT after discounts, amounts printed alike
# ---------------------------------------------------------------------------


def test_discounts_never_exceed_the_subtotal_and_the_order_reconciles(cod_on, world):
    w.make_coupon("HUGE", kind=CouponDiscountType.FIXED_AMOUNT, value="1000.00")
    w.make_promotion(pct="10")
    shopper = w.make_shopper("guest", world.governorates["cairo"])
    shopper.add(world.taxed, 2)
    response = shopper.checkout(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code="HUGE")
    assert response.status_code == 201, response.content
    order = Order.objects.get()
    assert order.coupon_discount.amount + order.promotion_discount.amount <= order.subtotal.amount
    assert order.subtotal.amount - order.coupon_discount.amount - order.promotion_discount.amount \
        + order.tax_total.amount + order.shipping_total.amount + order.cod_fee.amount == order.grand_total.amount


def test_vat_is_charged_on_what_the_customer_actually_pays(cod_on, world):
    w.make_coupon("HALF", value="50")
    shopper = w.make_shopper("guest", world.governorates["cairo"])
    shopper.add(world.taxed, 2)  # 200.00 VAT-able, 50% off -> 100.00 taxable
    response = shopper.summary(shipping_method_id=world.methods["standard"].id, payment_method="cod", coupon_code="HALF")
    assert response.status_code == 200
    assert w.money(response.json()["data"]["tax_total"]) == Decimal("14.00")


def test_the_summary_prints_every_amount_with_two_decimals(cod_on, world):
    shopper = w.make_shopper("guest", world.governorates["cairo"])
    shopper.add(world.taxed, 1)
    data = shopper.summary(shipping_method_id=world.methods["standard"].id, payment_method="instapay").json()["data"]
    for key in ("subtotal", "coupon_discount", "promotion_discount", "tax_total", "shipping_total", "cod_fee", "grand_total"):
        assert len(data[key].split(".")[-1]) == 2 and "." in data[key], (key, data[key])
