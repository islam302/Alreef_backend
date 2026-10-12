"""
The dashboard's offers screen: one call sets every size's price and "was"
price and keeps the product's headline price equal to its main size, so the
price a card shows is the price the cart charges.
"""

from decimal import Decimal

import pytest

from apps.audit.models import AuditLog
from apps.catalog.models import Product, ProductVariant

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/v1/admin-api/products/"


@pytest.fixture
def jar(product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    main = product_with_variant_and_stock["variant"]
    main.is_default = True
    main.save(update_fields=["is_default"])
    big = ProductVariant.objects.create(product=product, price=Decimal("450.00"), weight_grams=1000)
    return product, main, big


def _offer(client, product, body):
    return client.post(f"{PRODUCTS}{product.pk}/offer/", body, format="json")


def test_an_offer_sets_each_size_and_copies_the_main_size_onto_the_product(staff_client, jar):
    product, main, big = jar
    response = _offer(staff_client, product, {"variants": [
        {"id": main.pk, "price": "200.00", "compare_at_price": "250.00"},
        {"id": big.pk, "price": "400.00", "compare_at_price": "450.00"},
    ]})

    assert response.status_code == 200, response.content
    main.refresh_from_db(); big.refresh_from_db(); product.refresh_from_db()
    assert (main.price.amount, main.compare_at_price.amount) == (Decimal("200.00"), Decimal("250.00"))
    assert big.price.amount == Decimal("400.00")
    # The card and the offers band read these; the cart charges main.price.
    assert (product.price.amount, product.compare_at_price.amount) == (Decimal("200.00"), Decimal("250.00"))
    assert AuditLog.objects.filter(model_name="Product", object_id=str(product.pk)).exists()

    on_sale = staff_client.get(PRODUCTS, {"on_sale": "true"}).json()["data"]["results"]
    assert [p["id"] for p in on_sale] == [product.pk]


def test_ending_an_offer_clears_the_was_price(staff_client, jar):
    product, main, _ = jar
    _offer(staff_client, product, {"variants": [{"id": main.pk, "price": "200.00", "compare_at_price": "250.00"}]})
    _offer(staff_client, product, {"variants": [{"id": main.pk, "price": "250.00", "compare_at_price": None}]})

    product.refresh_from_db()
    assert product.compare_at_price is None
    assert product.price.amount == Decimal("250.00")
    assert staff_client.get(PRODUCTS, {"on_sale": "true"}).json()["data"]["results"] == []


def test_a_was_price_must_be_above_the_price(staff_client, jar):
    product, main, _ = jar
    response = _offer(staff_client, product, {"variants": [{"id": main.pk, "price": "250.00", "compare_at_price": "250.00"}]})
    assert response.status_code == 400
    main.refresh_from_db()
    assert main.compare_at_price is None


def test_a_size_of_another_product_is_refused(staff_client, jar, category):
    product, _, _ = jar
    other = Product.objects.create(name_en="O", name_ar="O", slug="other", category=category, price=Decimal("10.00"))
    stranger = ProductVariant.objects.create(product=other, price=Decimal("10.00"))

    response = _offer(staff_client, product, {"variants": [{"id": stranger.pk, "price": "5.00", "compare_at_price": "10.00"}]})
    assert response.status_code == 404
    stranger.refresh_from_db()
    assert stranger.price.amount == Decimal("10.00")


def test_only_staff_can_set_an_offer(api_client, authenticated_client, jar):
    product, main, _ = jar
    body = {"variants": [{"id": main.pk, "price": "1.00", "compare_at_price": "250.00"}]}
    assert _offer(api_client, product, body).status_code == 401
    assert _offer(authenticated_client, product, body).status_code == 403
