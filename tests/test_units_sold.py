"""
Jars sold per product: the real number for the shop in the dashboard, and a
public «اتباع منه N علبة» on the storefront once it reaches the shop's
threshold (SiteSettings.sold_count_min). Cancelled, returned and refunded
orders sold nothing.
"""

from decimal import Decimal

import pytest

from apps.content.models import SiteSettings
from apps.orders.models import Order, OrderItem, OrderStatus

pytestmark = pytest.mark.django_db

_seq = iter(range(1, 10_000))


def _order(user, product, variant, quantity, status=OrderStatus.PENDING):
    n = next(_seq)
    order = Order.objects.create(
        user=user, order_number=f"ORD-T-{n:05d}", status=status,
        customer_name="عميل", customer_phone="01000000000", payment_method="cod",
        subtotal=Decimal("250.00") * quantity, grand_total=Decimal("250.00") * quantity,
    )
    OrderItem.objects.create(
        order=order, product=product, variant=variant, quantity=quantity,
        product_name_snapshot=product.name_ar, sku_snapshot=variant.sku, unit_price=Decimal("250.00"),
        line_total=Decimal("250.00") * quantity,
    )
    return order


def _threshold(n):
    s = SiteSettings.load()
    s.sold_count_min = n
    s.save()


@pytest.fixture
def sold(user, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    variant = product_with_variant_and_stock["variant"]
    _order(user, product, variant, 3)
    _order(user, product, variant, 2, OrderStatus.DELIVERED)
    _order(user, product, variant, 4, OrderStatus.CONFIRMED)
    for status in (OrderStatus.CANCELLED, OrderStatus.RETURNED, OrderStatus.REFUNDED):
        _order(user, product, variant, 50, status)
    return product  # 3 + 2 + 4 = 9 jars sold


def test_dashboard_shows_jars_sold_without_the_lost_orders(staff_client, sold):
    rows = staff_client.get("/api/v1/admin-api/products/").json()["data"]["results"]
    assert rows[0]["units_sold"] == 9


def test_dashboard_shows_zero_for_a_product_never_sold(staff_client, product_with_variant_and_stock):
    rows = staff_client.get("/api/v1/admin-api/products/").json()["data"]["results"]
    assert rows[0]["units_sold"] == 0


def test_dashboard_can_sort_by_jars_sold(staff_client, sold):
    response = staff_client.get("/api/v1/admin-api/products/", {"ordering": "-units_sold"})
    assert response.status_code == 200


def test_storefront_hides_the_count_below_the_threshold(api_client, sold):
    _threshold(10)
    listed = api_client.get("/api/v1/products/").json()["data"]["results"][0]
    detail = api_client.get(f"/api/v1/products/{sold.slug}/").json()["data"]
    assert listed["sold_count"] is None
    assert detail["sold_count"] is None


def test_storefront_shows_the_count_once_it_reaches_the_threshold(api_client, sold):
    _threshold(9)
    listed = api_client.get("/api/v1/products/").json()["data"]["results"][0]
    detail = api_client.get(f"/api/v1/products/{sold.slug}/").json()["data"]
    assert listed["sold_count"] == 9
    assert detail["sold_count"] == 9


def test_threshold_zero_never_shows_it(api_client, sold):
    _threshold(0)
    listed = api_client.get("/api/v1/products/").json()["data"]["results"][0]
    assert listed["sold_count"] is None


def test_staff_can_set_the_threshold(staff_client):
    response = staff_client.patch("/api/v1/admin-api/site-settings/", {"sold_count_min": 25}, format="json")
    assert response.status_code == 200, response.content
    assert SiteSettings.load().sold_count_min == 25
