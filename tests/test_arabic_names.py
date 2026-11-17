"""
The Arabic storefront gets Arabic names from the API: shipping methods (in
quotes, the methods list and the orders placed with them) and the governorate
and city of a saved address. The English fields stay for older consumers.
"""

import importlib
import uuid

import pytest
from django.apps import apps as django_apps

from apps.orders.models import Order
from apps.shipping.models import City, ShippingMethod

pytestmark = pytest.mark.django_db


def _place_order(client, variant, address, method):
    client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    response = client.post(
        "/api/v1/checkout/",
        {"address_id": address.id, "shipping_method_id": method.id, "payment_method": "cod", "coupon_code": ""},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    return Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])


def test_quotes_carry_the_arabic_method_name(api_client, shipping_setup):
    method = shipping_setup["method"]
    method.name_ar = "توصيل عادي"
    method.save()

    response = api_client.get("/api/v1/shipping/quotes/", {"governorate": "cairo", "subtotal": "100.00"})

    assert response.status_code == 200
    quote = response.json()["data"][0]
    assert quote["method_name"] == "Standard"
    assert quote["method_name_ar"] == "توصيل عادي"


def test_quote_arabic_name_is_blank_when_unset(api_client, shipping_setup):
    response = api_client.get("/api/v1/shipping/quotes/", {"governorate": "cairo", "subtotal": "100.00"})

    assert response.json()["data"][0]["method_name_ar"] == ""


def test_order_keeps_the_arabic_method_name_from_checkout(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    method = shipping_setup["method"]
    method.name_ar = "توصيل سريع للقاهرة"
    method.save()

    order = _place_order(authenticated_client, product_with_variant_and_stock["variant"], address, method)

    assert order.shipping_method_name == "Standard"
    assert order.shipping_method_name_ar == "توصيل سريع للقاهرة"
    detail = authenticated_client.get(f"/api/v1/orders/{order.order_number}/").json()["data"]
    assert detail["shipping_method_name"] == "Standard"
    assert detail["shipping_method_name_ar"] == "توصيل سريع للقاهرة"

    # A later rename does not rewrite what the order was placed with.
    method.name_ar = "اسم جديد"
    method.save()
    order.refresh_from_db()
    assert order.shipping_method_name_ar == "توصيل سريع للقاهرة"


def test_guest_lookup_returns_the_arabic_method_name(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    from apps.orders.serializers import GuestOrderStatusSerializer

    method = shipping_setup["method"]
    method.name_ar = "توصيل عادي"
    method.save()
    order = _place_order(authenticated_client, product_with_variant_and_stock["variant"], address, method)

    assert GuestOrderStatusSerializer(order).data["shipping_method_name_ar"] == "توصيل عادي"


def test_staff_can_set_the_arabic_method_name(staff_client, shipping_setup):
    method = shipping_setup["method"]

    response = staff_client.patch(
        f"/api/v1/admin-api/shipping/methods/{method.id}/", {"name_ar": "توصيل عادي"}, format="json",
    )

    assert response.status_code == 200, response.content
    method.refresh_from_db()
    assert method.name_ar == "توصيل عادي"
    assert method.name == "Standard"


def test_address_carries_arabic_governorate_and_city(authenticated_client, address, governorate):
    city = City.objects.create(governorate=governorate, name_en="Nasr City", name_ar="مدينة نصر", is_active=True)
    address.city = city
    address.save()

    response = authenticated_client.get(f"/api/v1/addresses/{address.id}/")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["governorate_name"] == "Cairo"
    assert data["governorate_name_ar"] == "القاهرة"
    assert data["city_name"] == "Nasr City"
    assert data["city_name_ar"] == "مدينة نصر"


def test_address_without_city_has_null_city_names(authenticated_client, address):
    response = authenticated_client.get("/api/v1/addresses/")

    row = response.json()["data"][0] if isinstance(response.json()["data"], list) else response.json()["data"]["results"][0]
    assert row["governorate_name_ar"] == "القاهرة"
    assert row["city_name"] is None
    assert row["city_name_ar"] is None


def test_shipping_migration_fills_arabic_names_by_kind():
    migration = importlib.import_module("apps.shipping.migrations.0003_shippingmethod_name_ar")
    standard = ShippingMethod.objects.create(name="Standard Delivery", method_type="standard")
    express = ShippingMethod.objects.create(name="Bosta Express", method_type="express")
    arabic = ShippingMethod.objects.create(name="توصيل الجمعة", method_type="standard")
    named = ShippingMethod.objects.create(name="Pickup", method_type="pickup", name_ar="من المحل")

    migration.fill_arabic_names(django_apps, None)

    for m in (standard, express, arabic, named):
        m.refresh_from_db()
    assert standard.name_ar == "توصيل عادي"
    assert express.name_ar == "توصيل سريع"
    assert arabic.name_ar == "توصيل الجمعة"
    assert named.name_ar == "من المحل"  # never overwritten


def test_orders_migration_backfills_from_the_method_name(user):
    migration = importlib.import_module("apps.orders.migrations.0008_order_shipping_method_name_ar")
    ShippingMethod.objects.create(name="Standard Delivery", method_type="standard", name_ar="توصيل عادي")
    common = dict(user=user, currency="EGP", subtotal=0, grand_total=0, customer_name="x", customer_phone="+201001234567", shipping_address_snapshot={})
    matched = Order.objects.create(order_number="ORD-T-1", shipping_method_name="Standard Delivery", **common)
    unknown = Order.objects.create(order_number="ORD-T-2", shipping_method_name="Old Courier", **common)

    migration.backfill(django_apps, None)

    matched.refresh_from_db()
    unknown.refresh_from_db()
    assert matched.shipping_method_name_ar == "توصيل عادي"
    assert unknown.shipping_method_name_ar == ""
