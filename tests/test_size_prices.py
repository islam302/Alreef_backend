"""
Prices are set on sizes. The product's own price — what cards and the offers
band show — follows its headline size (the default one, else the first), so
the card never disagrees with what the cart charges.
"""

from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(product_with_variant_and_stock):
    return product_with_variant_and_stock["product"]


def _add(client, product, **body):
    return client.post(f"/api/v1/admin-api/products/{product.pk}/variants/", body, format="json")


def test_new_default_size_sets_the_product_price(staff_client, product):
    product.variants.update(is_default=False)
    _add(staff_client, product, name_suffix="1 كيلو", weight_grams=1000, price="420.00",
         compare_at_price="500.00", is_default=True)
    product.refresh_from_db()
    assert product.price.amount == Decimal("420.00")
    assert product.compare_at_price.amount == Decimal("500.00")


def test_editing_the_headline_size_moves_the_product_price(staff_client, product):
    main = product.variants.first()
    main.is_default = True
    main.save()
    response = staff_client.patch(f"/api/v1/admin-api/variants/{main.pk}/", {"price": "99.00"}, format="json")
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.price.amount == Decimal("99.00")


def test_deleting_the_headline_size_falls_back_to_the_next(staff_client, product):
    other = _add(staff_client, product, name_suffix="ربع كيلو", weight_grams=250, price="75.00").json()["data"]
    main = product.variants.exclude(pk=other["id"]).first()
    staff_client.delete(f"/api/v1/admin-api/variants/{main.pk}/")
    product.refresh_from_db()
    assert product.price.amount == Decimal("75.00")
