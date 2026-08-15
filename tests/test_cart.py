import datetime

import pytest
from django.utils import timezone

from apps.coupons.models import Coupon, CouponDiscountType
from apps.core.error_codes import ErrorCodes

pytestmark = pytest.mark.django_db

CART_URL = "/api/v1/cart/"
CART_ITEMS_URL = "/api/v1/cart/items/"
CART_MERGE_URL = "/api/v1/cart/merge/"
CART_COUPON_URL = "/api/v1/cart/coupon/"


def _item_url(item_id):
    return f"/api/v1/cart/items/{item_id}/"


def test_add_item_to_cart_guest(api_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]

    response = api_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 2}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["quantity"] == 2
    assert body["data"]["item_count"] == 2

    # Guest session persists across requests on the same client.
    detail = api_client.get(CART_URL)
    assert detail.json()["data"]["items"][0]["quantity"] == 2


def test_add_item_to_cart_authenticated(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]

    response = authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["quantity"] == 1


def test_update_item_quantity(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    add_response = authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    item_id = add_response.json()["data"]["items"][0]["id"]

    response = authenticated_client.patch(_item_url(item_id), {"quantity": 5}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["quantity"] == 5


def test_remove_item_from_cart(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    add_response = authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    item_id = add_response.json()["data"]["items"][0]["id"]

    response = authenticated_client.delete(_item_url(item_id))

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []


def test_cart_merge_sums_quantities_for_same_variant(api_client, authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]

    guest_response = api_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 3}, format="json")
    guest_cart_id = guest_response.json()["data"]["id"]

    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 2}, format="json")

    merge_response = authenticated_client.post(CART_MERGE_URL, {"guest_cart_id": guest_cart_id}, format="json")

    assert merge_response.status_code == 200
    items = merge_response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 5


def test_apply_valid_coupon(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    Coupon.objects.create(
        code="SAVE10", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True,
    )

    response = authenticated_client.post(CART_COUPON_URL, {"code": "SAVE10"}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["coupon_code"] == "SAVE10"


def test_apply_invalid_coupon(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")

    response = authenticated_client.post(CART_COUPON_URL, {"code": "DOES-NOT-EXIST"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.INVALID_COUPON


def test_apply_expired_coupon(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    Coupon.objects.create(
        code="EXPIRED10", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True,
        expiration_date=timezone.now() - datetime.timedelta(days=1),
    )

    response = authenticated_client.post(CART_COUPON_URL, {"code": "EXPIRED10"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.COUPON_EXPIRED
