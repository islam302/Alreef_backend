"""
SKUs and barcodes are generated when staff leave them empty, and left alone
when staff type their own (apps.catalog.codes).
"""

from decimal import Decimal

import pytest

from apps.catalog.codes import internal_barcode, is_valid_ean13
from apps.catalog.models import Product, ProductStatus, ProductType, ProductVariant

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/v1/admin-api/products/"


def _product(category, **overrides):
    defaults = dict(
        name_en="Clover", name_ar="برسيم", slug=f"clover-{len(overrides)}", category=category,
        product_type=ProductType.VARIABLE, status=ProductStatus.ACTIVE, published=True, price=Decimal("100.00"),
    )
    defaults.update(overrides)
    return Product.objects.create(**defaults)


def test_a_product_saved_without_codes_gets_both(category):
    product = _product(category)
    product.refresh_from_db()

    assert product.sku == f"ELR-{product.pk:05d}"
    assert product.barcode == internal_barcode("1", product.pk)
    assert is_valid_ean13(product.barcode)
    assert product.barcode.startswith("201")


def test_a_variant_sku_builds_on_its_product_and_weight(category):
    product = _product(category)
    by_weight = ProductVariant.objects.create(product=product, price=Decimal("100.00"), weight_grams=500)
    no_weight = ProductVariant.objects.create(product=product, price=Decimal("100.00"))

    assert by_weight.sku == f"{product.sku}-500G"
    assert no_weight.sku == f"{product.sku}-V{no_weight.pk}"
    for variant in (by_weight, no_weight):
        assert is_valid_ean13(variant.barcode) and variant.barcode.startswith("202")
    assert by_weight.barcode != no_weight.barcode


def test_codes_staff_type_are_kept(category):
    product = _product(category, sku="  SUPPLIER-77 ", barcode="6221234567890")
    product.refresh_from_db()

    assert product.sku == "SUPPLIER-77"
    assert product.barcode == "6221234567890"


def test_a_generated_sku_never_collides_with_a_typed_one(category):
    first = _product(category, slug="a")
    # Someone types the code the *next* product would be given.
    _product(category, slug="b", sku=f"ELR-{first.pk + 2:05d}")
    third = _product(category, slug="c")

    assert third.pk == first.pk + 2
    assert third.sku == f"ELR-{third.pk:05d}-{third.pk}"
    assert Product.objects.filter(sku=third.sku).count() == 1


def test_the_dashboard_can_create_a_product_and_a_size_without_codes(staff_client, category, brand):
    created = staff_client.post(
        PRODUCTS,
        {"name_en": "Clover", "name_ar": "برسيم", "slug": "clover", "sku": "", "category": category.id,
         "brand": brand.id, "price": "180.00", "status": "active", "published": True},
        format="json",
    )
    assert created.status_code == 201, created.content
    body = created.json()["data"]
    product = Product.objects.get(pk=body["id"])
    assert product.sku.startswith("ELR-") and is_valid_ean13(product.barcode)

    size = staff_client.post(f"{PRODUCTS}{product.pk}/variants/", {"price": "180.00", "weight_grams": 250}, format="json")
    assert size.status_code == 201, size.content
    variant = ProductVariant.objects.get(pk=size.json()["data"]["id"])
    assert variant.sku == f"{product.sku}-250G"
    assert is_valid_ean13(variant.barcode)


def test_internal_barcodes_are_valid_ean13():
    # Known-good check digits for the reserved in-store range.
    assert internal_barcode("1", 1) == "2010000000014"
    assert all(is_valid_ean13(internal_barcode(kind, pk)) for kind in "12" for pk in (1, 9, 10, 12345, 999_999_999))
