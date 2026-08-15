import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.categories.models import Category
from apps.core.error_codes import ErrorCodes
from apps.coupons.models import Coupon, CouponDiscountType, CouponUsage

pytestmark = pytest.mark.django_db

CART_ITEMS_URL = "/api/v1/cart/items/"
CART_COUPON_URL = "/api/v1/cart/coupon/"


def _coupon_detail_url(code):
    return f"/api/v1/coupons/{code}/"


def test_get_valid_coupon(api_client):
    Coupon.objects.create(code="WELCOME10", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True)

    response = api_client.get(_coupon_detail_url("WELCOME10"))

    assert response.status_code == 200
    assert response.json()["data"]["code"] == "WELCOME10"


def test_get_invalid_coupon(api_client):
    response = api_client.get(_coupon_detail_url("NO-SUCH-CODE"))

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.INVALID_COUPON


def test_get_expired_coupon(api_client):
    Coupon.objects.create(
        code="OLD10", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True,
        expiration_date=timezone.now() - datetime.timedelta(days=1),
    )

    response = api_client.get(_coupon_detail_url("OLD10"))

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.COUPON_EXPIRED


def test_apply_valid_coupon_reflects_in_cart(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    Coupon.objects.create(code="SAVE20", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=20, is_active=True)

    response = authenticated_client.post(CART_COUPON_URL, {"code": "SAVE20"}, format="json")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["coupon_code"] == "SAVE20"
    assert data["subtotal"] == "250.00"  # coupon does not change the raw subtotal itself


def test_apply_coupon_minimum_order_not_met(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    Coupon.objects.create(
        code="BIGORDER", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True,
        minimum_order_amount=Decimal("500.00"),
    )

    response = authenticated_client.post(CART_COUPON_URL, {"code": "BIGORDER"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.COUPON_MINIMUM_NOT_MET


def test_apply_category_scoped_coupon_not_applicable_to_unrelated_item(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]  # category = Honey
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")

    spices = Category.objects.create(name_en="Spices", name_ar="بهارات", slug="spices", is_active=True)
    coupon = Coupon.objects.create(code="SPICES10", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True)
    coupon.categories.add(spices)

    response = authenticated_client.post(CART_COUPON_URL, {"code": "SPICES10"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.COUPON_NOT_APPLICABLE


def test_apply_brand_scoped_coupon_applicable_to_matching_item(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    product = product_with_variant_and_stock["product"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")

    coupon = Coupon.objects.create(code="BRAND10", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True)
    coupon.brands.add(product.brand)

    response = authenticated_client.post(CART_COUPON_URL, {"code": "BRAND10"}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["coupon_code"] == "BRAND10"


def test_apply_coupon_per_user_usage_limit_enforced(authenticated_client, user, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    coupon = Coupon.objects.create(
        code="ONCEONLY", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True, per_user_limit=1,
    )
    # Simulate the user having already redeemed this coupon once before.
    CouponUsage.objects.create(coupon=coupon, user=user, discount_amount=Decimal("25.00"))

    response = authenticated_client.post(CART_COUPON_URL, {"code": "ONCEONLY"}, format="json")

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == ErrorCodes.COUPON_USAGE_LIMIT_REACHED
