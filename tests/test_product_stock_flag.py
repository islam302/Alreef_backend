"""A product the cart cannot take (no size) must not look buyable on a card."""

import pytest

pytestmark = pytest.mark.django_db


def test_product_without_sizes_is_not_in_stock(api_client, category):
    from apps.catalog.models import Product

    Product.objects.create(name_en="No sizes", name_ar="بدون أوزان", slug="no-sizes", category=category, price=100, published=True, status="active")
    items = api_client.get("/api/v1/products/").json()["data"]
    items = items.get("items") or items.get("results")
    assert [p["in_stock"] for p in items if p["slug"] == "no-sizes"] == [False]
