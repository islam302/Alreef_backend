"""
Customer-facing endpoints: cart edge cases, related products and catalogue
filters, addresses, account verification, store credit / loyalty histories and
guest order lookup.
"""
from decimal import Decimal

import pytest
from django.core import signing

from apps.carts.models import Cart, CartItem, CartStatus
from apps.catalog.models import Product, ProductStatus, ProductType, ProductVariant
from apps.users.models import Address

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Cart
# --------------------------------------------------------------------------


def test_adding_the_same_variant_twice_sums_and_caps(authenticated_client, product_with_variant_and_stock, settings):
    variant = product_with_variant_and_stock["variant"]
    url = "/api/v1/cart/items/"
    authenticated_client.post(url, {"variant_id": variant.id, "quantity": 2}, format="json")
    authenticated_client.post(url, {"variant_id": variant.id, "quantity": 3}, format="json")
    assert CartItem.objects.get(variant=variant).quantity == 5

    too_many = authenticated_client.post(url, {"variant_id": variant.id, "quantity": settings.MAX_CART_ITEM_QUANTITY}, format="json")
    assert too_many.status_code == 400
    assert CartItem.objects.get(variant=variant).quantity == 5


def test_inactive_variant_cannot_be_added(authenticated_client, product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    variant.is_active = False
    variant.save()
    response = authenticated_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    assert response.status_code == 404


def test_clear_cart_and_remove_coupon(authenticated_client, product_with_variant_and_stock, user):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    Cart.objects.filter(user=user).update(coupon_code="HONEY10")

    removed = authenticated_client.delete("/api/v1/cart/coupon/")
    assert removed.status_code == 200
    assert Cart.objects.get(user=user).coupon_code == ""

    cleared = authenticated_client.delete("/api/v1/cart/")
    assert cleared.status_code == 200
    assert not CartItem.objects.filter(cart__user=user).exists()


def test_cart_item_not_found(authenticated_client):
    assert authenticated_client.patch("/api/v1/cart/items/999999/", {"quantity": 2}, format="json").status_code == 404
    assert authenticated_client.delete("/api/v1/cart/items/999999/").status_code == 404


def test_merge_without_a_guest_cart_returns_own_cart(authenticated_client):
    assert authenticated_client.post("/api/v1/cart/merge/", {}, format="json").status_code == 200


def test_merge_sums_lines_and_carries_coupon(user, product_with_variant_and_stock):
    from apps.carts.services import merge_guest_cart_into_user

    variant = product_with_variant_and_stock["variant"]
    user_cart = Cart.objects.create(user=user, status=CartStatus.OPEN)
    CartItem.objects.create(cart=user_cart, variant=variant, quantity=2, unit_price_snapshot=variant.price)
    guest = Cart.objects.create(session_key="s1", status=CartStatus.OPEN, coupon_code="WELCOME")
    CartItem.objects.create(cart=guest, variant=variant, quantity=3, unit_price_snapshot=variant.price)

    merged = merge_guest_cart_into_user(guest_cart=guest, user=user)

    assert merged.pk == user_cart.pk
    assert merged.items.get(variant=variant).quantity == 5
    assert merged.coupon_code == "WELCOME"
    guest.refresh_from_db()
    assert guest.status == CartStatus.MERGED
    # A cart already the user's is returned untouched.
    assert merge_guest_cart_into_user(guest_cart=merged, user=user).pk == merged.pk


def test_merge_rejects_a_cart_from_another_session(api_client, authenticated_client, product_with_variant_and_stock, user):
    """A cart id alone is not proof of ownership; the session must match."""
    variant = product_with_variant_and_stock["variant"]
    api_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    guest = Cart.objects.get(user__isnull=True)

    authenticated_client.get("/api/v1/cart/")  # gives this client its own session
    authenticated_client.post("/api/v1/cart/merge/", {"guest_cart_id": str(guest.pk)}, format="json")
    guest.refresh_from_db()
    assert guest.status == CartStatus.OPEN and guest.items.count() == 1


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


def _product(category, brand, slug, **kw):
    product = Product.objects.create(
        name_en=slug, name_ar=slug, slug=slug, sku=slug.upper(), category=category, brand=brand,
        product_type=ProductType.SIMPLE, status=ProductStatus.ACTIVE, published=True, price=Decimal("100.00"), **kw,
    )
    ProductVariant.objects.create(product=product, sku=f"{slug.upper()}-V", price=Decimal("100.00"), is_active=True, is_default=True)
    return product


def test_related_products(api_client, product_with_variant_and_stock, category, brand):
    _product(category, brand, "clover-honey")
    body = api_client.get("/api/v1/products/sidr-honey/related/").json()
    assert [p["slug"] for p in body["data"]] == ["clover-honey"]
    assert api_client.get("/api/v1/products/no-such/related/").status_code == 404


@pytest.mark.parametrize("params,expected", [
    ({"attribute": "no-colon"}, 0),
    ({"attribute": "size:500g"}, 0),
    ({"badge": "nonsense"}, 0),
    ({"slugs": " , "}, 0),
    ({"slugs": "sidr-honey,clover-honey"}, 2),
    ({"organic": "false"}, 2),
    ({"organic": "true"}, 0),
    ({"origin": "Yemen"}, 0),
    ({"on_sale": "true"}, 1),
    ({"on_sale": "false"}, 2),
])
def test_catalog_filters(api_client, product_with_variant_and_stock, category, brand, params, expected):
    _product(category, brand, "clover-honey", compare_at_price=Decimal("150.00"))
    response = api_client.get("/api/v1/products/", params)
    assert response.status_code == 200
    data = response.json()["data"]
    results = data["results"] if isinstance(data, dict) else data
    assert len(results) == expected


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------


def test_address_crud_and_default(authenticated_client, address, governorate):
    payload = {"full_name": "Jane", "phone": "+201001234568", "governorate": governorate.id, "street": "Tahrir", "is_default": False}
    created = authenticated_client.post("/api/v1/addresses/", payload, format="json")
    assert created.status_code == 201, created.content
    new_id = created.json()["data"]["id"]

    assert authenticated_client.get(f"/api/v1/addresses/{new_id}/").status_code == 200
    assert authenticated_client.patch(f"/api/v1/addresses/{new_id}/", {"street": "Qasr El Nil"}, format="json").status_code == 200
    assert authenticated_client.put(f"/api/v1/addresses/{new_id}/", payload, format="json").status_code == 200

    assert authenticated_client.post(f"/api/v1/addresses/{new_id}/set-default/").status_code == 200
    address.refresh_from_db()
    assert address.is_default is False
    assert Address.objects.get(pk=new_id).is_default is True

    assert authenticated_client.delete(f"/api/v1/addresses/{new_id}/").status_code == 204


def test_other_customers_address_is_invisible(other_client, address):
    assert other_client.get(f"/api/v1/addresses/{address.id}/").status_code == 404
    assert other_client.post(f"/api/v1/addresses/{address.id}/set-default/").status_code == 404
    assert other_client.delete(f"/api/v1/addresses/{address.id}/").status_code == 404


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def test_email_verify_when_already_verified(authenticated_client, user):
    user.email_verified = True
    user.save()
    response = authenticated_client.post("/api/v1/auth/verify-email/")
    assert response.status_code == 200 and "already" in response.json()["message"]


def test_phone_verify_requires_a_phone_on_file(authenticated_client):
    assert authenticated_client.post("/api/v1/auth/verify-phone/", {}, format="json").status_code == 400


def test_phone_verify_refuses_another_number(authenticated_client, user):
    user.phone = "+201001234567"
    user.save()
    response = authenticated_client.post("/api/v1/auth/verify-phone/", {"phone": "+201009999999"}, format="json")
    assert response.status_code == 400


def test_phone_verify_unavailable_without_real_sms(authenticated_client, user, settings):
    user.phone = "+201001234567"
    user.save()
    settings.SMS_PROVIDER = "console"
    settings.SMS_CONSOLE_IS_DELIVERY = False
    response = authenticated_client.post("/api/v1/auth/verify-phone/", {}, format="json")
    assert response.status_code >= 400
    assert response.json()["errors"]["code"] == "AUTH_OTP_UNAVAILABLE"


def test_phone_verify_round_trip(authenticated_client, user, settings, monkeypatch):
    from apps.users import services

    user.phone = "+201001234567"
    user.save()
    settings.SMS_CONSOLE_IS_DELIVERY = True
    codes = []
    original = services.issue_phone_otp

    def capture(u):
        codes.append(original(u))
        return codes[-1]

    monkeypatch.setattr(services, "issue_phone_otp", capture)
    assert authenticated_client.post("/api/v1/auth/verify-phone/", {}, format="json").status_code == 200

    wrong = "000000" if codes[0] != "000000" else "111111"
    assert authenticated_client.post("/api/v1/auth/verify-phone/confirm/", {"code": wrong}, format="json").status_code == 400
    ok = authenticated_client.post("/api/v1/auth/verify-phone/confirm/", {"code": codes[0]}, format="json")
    assert ok.status_code == 200
    user.refresh_from_db()
    assert user.phone_verified is True
    # A used code cannot be replayed.
    assert authenticated_client.post("/api/v1/auth/verify-phone/confirm/", {"code": codes[0]}, format="json").status_code == 400


# --------------------------------------------------------------------------
# Histories
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/api/v1/gift-cards/store-credit/transactions/", "/api/v1/loyalty/transactions/"])
def test_history_lists(authenticated_client, api_client, url):
    response = authenticated_client.get(url)
    assert response.status_code == 200
    assert api_client.get(url).status_code == 401


# --------------------------------------------------------------------------
# Guest order lookup
# --------------------------------------------------------------------------


def test_guest_lookup_rejects_junk_tokens(api_client):
    from apps.orders.guest import TOKEN_SALT, order_for_token

    assert order_for_token("x" * 600) is None
    assert order_for_token(signing.dumps(["not", "a", "dict"], salt=TOKEN_SALT)) is None
    response = api_client.post("/api/v1/orders/lookup/", {"token": "forged"}, format="json")
    assert response.status_code == 404


def test_guest_lookup_locks_after_repeated_wrong_phones(api_client):
    from apps.orders.guest import LOOKUP_FAILURES_ALLOWED

    for _ in range(LOOKUP_FAILURES_ALLOWED):
        r = api_client.post("/api/v1/orders/lookup/", {"order_number": "ORD-NOPE", "phone": "not a phone"}, format="json")
        assert r.status_code == 404
    locked = api_client.post("/api/v1/orders/lookup/", {"order_number": "ORD-NOPE", "phone": "+201001234567"}, format="json")
    assert locked.status_code == 429


def test_normalize_phone():
    from apps.orders.guest import normalize_phone

    assert normalize_phone("01001234567") == "+201001234567"
    assert normalize_phone("123") == ""
    assert normalize_phone("abc") == ""
