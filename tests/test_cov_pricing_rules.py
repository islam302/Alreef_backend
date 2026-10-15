"""
Pricing rules: coupon validity and caps, promotion stacking and buy-X-get-Y,
shipping rate maths, and the idempotency mixin's conflict answers.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.coupons.models import Coupon
from apps.coupons.services import compute_discount, get_valid_coupon, validate_coupon_for_cart
from apps.promotions.models import Promotion
from apps.promotions.services import compute_promotions_for_items
from apps.shipping.models import RateCalculationType
from apps.shipping.services import get_available_quotes, get_quote_for_method

pytestmark = pytest.mark.django_db

EGP = "EGP"


def _items(product, *, quantity=2, unit="100.00"):
    unit_price = Money(Decimal(unit), EGP)
    return [SimpleNamespace(
        variant=SimpleNamespace(product=product), quantity=quantity,
        unit_price_snapshot=unit_price, line_total=unit_price * quantity,
    )]


# --------------------------------------------------------------------------
# coupons
# --------------------------------------------------------------------------


def test_coupon_not_started_yet():
    Coupon.objects.create(code="SOON", discount_type="percentage", percentage_value=10,
                          start_date=timezone.now() + timedelta(days=1))
    with pytest.raises(ApplicationError) as exc:
        get_valid_coupon("soon")
    assert exc.value.code == ErrorCodes.INVALID_COUPON


def test_coupon_on_empty_cart(user):
    from apps.carts.models import Cart

    Coupon.objects.create(code="ANY", discount_type="percentage", percentage_value=10, per_user_limit=None)
    with pytest.raises(ApplicationError) as exc:
        validate_coupon_for_cart(code="ANY", cart=Cart.objects.create(user=user))
    assert exc.value.code == ErrorCodes.CART_EMPTY


def test_guest_without_phone_is_not_counted(user):
    from apps.coupons.services import _check_per_user_limit

    coupon = Coupon.objects.create(code="ONE", discount_type="percentage", percentage_value=10, per_user_limit=1)
    _check_per_user_limit(coupon, None, "")  # nothing to key on: allowed, not crashed


def test_discount_shapes(product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    items = _items(product)
    subtotal = Money(Decimal("200.00"), EGP)

    free_ship = Coupon.objects.create(code="SHIP", discount_type="free_shipping")
    assert compute_discount(coupon=free_ship, items=items, subtotal=subtotal).amount == 0

    capped = Coupon.objects.create(code="CAP", discount_type="percentage", percentage_value=50,
                                   maximum_discount_amount=Money(30, EGP))
    assert compute_discount(coupon=capped, items=items, subtotal=subtotal) == Money(30, EGP)

    fixed = Coupon.objects.create(code="FIX", discount_type="fixed_amount", fixed_amount_value=Money(500, EGP))
    assert compute_discount(coupon=fixed, items=items, subtotal=subtotal) == subtotal

    odd = Coupon.objects.create(code="ODD", discount_type="mystery")
    assert compute_discount(coupon=odd, items=items, subtotal=subtotal).amount == 0


# --------------------------------------------------------------------------
# promotions
# --------------------------------------------------------------------------


def test_non_stackable_only_highest_priority_applies(product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    Promotion.objects.create(name="Big", promotion_type="percentage", percentage_value=20, priority=10)
    Promotion.objects.create(name="Small", promotion_type="percentage", percentage_value=5, priority=1)
    total, applied, free = compute_promotions_for_items(_items(product))
    assert [a["promotion"].name for a in applied] == ["Big"]
    assert total == Money(40, EGP) and free is False


def test_free_shipping_and_fixed_promotions_stack(product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    Promotion.objects.create(name="Ship", promotion_type="free_shipping", stackable=True, priority=5)
    Promotion.objects.create(name="Fixed", promotion_type="fixed_amount", fixed_amount_value=Money(500, EGP), stackable=True)
    total, applied, free = compute_promotions_for_items(_items(product))
    assert free is True
    assert total == Money(200, EGP)  # capped at the subtotal, never negative


def test_scoped_promotion_skips_other_products(product_with_variant_and_stock, brand):
    product = product_with_variant_and_stock["product"]
    from apps.brands.models import Brand

    other = Brand.objects.create(name="Other", slug="other")
    promo = Promotion.objects.create(name="Other brand", promotion_type="percentage", percentage_value=10)
    promo.brands.add(other)
    total, applied, _ = compute_promotions_for_items(_items(product))
    assert applied == [] and total.amount == 0


@pytest.mark.parametrize("buy,get,qty,expected", [(0, 1, 4, 0), (2, 1, 2, 0), (2, 1, 3, 100)])
def test_buy_x_get_y(product_with_variant_and_stock, buy, get, qty, expected):
    product = product_with_variant_and_stock["product"]
    Promotion.objects.create(name="BOGO", promotion_type="buy_x_get_y", buy_quantity=buy, get_quantity=get)
    total, _, _ = compute_promotions_for_items(_items(product, quantity=qty))
    assert total == Money(expected, EGP)


# --------------------------------------------------------------------------
# shipping
# --------------------------------------------------------------------------


def test_quotes_need_a_governorate():
    with pytest.raises(ApplicationError) as exc:
        get_available_quotes(governorate=None, subtotal=Money(10, EGP))
    assert exc.value.code == ErrorCodes.SHIPPING_UNAVAILABLE


def test_rate_maths(shipping_setup, governorate):
    rate = shipping_setup["rate"]
    method = shipping_setup["method"]

    rate.min_order_amount = Money(100, EGP)
    rate.save()
    assert get_available_quotes(governorate=governorate, subtotal=Money(50, EGP)) == []
    with pytest.raises(ApplicationError) as exc:
        get_quote_for_method(governorate=governorate, method_id=method.id, subtotal=Money(50, EGP))
    assert exc.value.code == ErrorCodes.SHIPPING_METHOD_NOT_FOUND

    rate.min_order_amount = None
    rate.calculation_type = RateCalculationType.WEIGHT_BASED
    rate.price_per_kg = Money(10, EGP)
    rate.save()
    quote = get_quote_for_method(governorate=governorate, method_id=method.id, subtotal=Money(50, EGP), weight_kg=Decimal("2"))
    assert quote.price == Money(50, EGP)

    rate.calculation_type = RateCalculationType.FREE
    rate.save()
    assert get_quote_for_method(governorate=governorate, method_id=method.id, subtotal=Money(50, EGP)).is_free

    rate.calculation_type = RateCalculationType.FLAT
    rate.free_shipping_threshold = Money(40, EGP)
    rate.save()
    assert get_quote_for_method(governorate=governorate, method_id=method.id, subtotal=Money(50, EGP)).is_free


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


def _checkout(client, address, shipping_setup, key, **extra):
    return client.post(
        "/api/v1/checkout/",
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod", **extra},
        format="json", HTTP_IDEMPOTENCY_KEY=key,
    )


def test_idempotency_key_required(authenticated_client, address, shipping_setup):
    response = authenticated_client.post("/api/v1/checkout/", {}, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.IDEMPOTENCY_KEY_REQUIRED


def test_idempotency_key_reused_with_a_different_body(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    key = str(uuid.uuid4())
    first = _checkout(authenticated_client, address, shipping_setup, key)
    assert first.status_code == 201, first.content
    replay = _checkout(authenticated_client, address, shipping_setup, key)
    assert replay.status_code == 201 and replay.json() == first.json()
    changed = _checkout(authenticated_client, address, shipping_setup, key, coupon_code="X")
    assert changed.status_code == 409


def test_idempotency_request_in_flight(authenticated_client, address, shipping_setup):
    from apps.core.idempotency import _LOCK_SENTINEL, _body_fingerprint, _cache_key

    key = str(uuid.uuid4())
    body = {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"}
    fingerprint = _body_fingerprint(SimpleNamespace(data=body))
    scope = "checkout"
    cache.set(_cache_key(scope, key), {"fingerprint": fingerprint, "status": _LOCK_SENTINEL}, 60)
    response = authenticated_client.post("/api/v1/checkout/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert response.status_code == 409
