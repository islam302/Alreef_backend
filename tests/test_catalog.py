from decimal import Decimal

import pytest

from apps.catalog.models import Product, ProductStatus, ProductType, ProductVariant
from apps.categories.models import Category

pytestmark = pytest.mark.django_db

PRODUCTS_URL = "/api/v1/products/"
SEARCH_URL = "/api/v1/search/"
CATEGORIES_URL = "/api/v1/categories/"
CATEGORY_TREE_URL = "/api/v1/categories/tree/"


def _make_product(category, brand, **overrides):
    defaults = dict(
        name_en="Acacia Honey", name_ar="عسل السنط", slug="acacia-honey", sku="HONEY-ACACIA-001",
        category=category, brand=brand, product_type=ProductType.SIMPLE,
        status=ProductStatus.ACTIVE, published=True, price=Decimal("300.00"),
    )
    defaults.update(overrides)
    return Product.objects.create(**defaults)


def test_product_list_only_shows_published_and_active(api_client, product_with_variant_and_stock, category, brand):
    draft_product = _make_product(
        category, brand, slug="draft-honey", sku="HONEY-DRAFT-001",
        status=ProductStatus.DRAFT, published=False,
    )
    unpublished_active = _make_product(
        category, brand, slug="unpublished-honey", sku="HONEY-UNPUB-001",
        status=ProductStatus.ACTIVE, published=False,
    )

    response = api_client.get(PRODUCTS_URL)

    assert response.status_code == 200
    body = response.json()
    slugs = {p["slug"] for p in body["data"]["results"]}
    assert product_with_variant_and_stock["product"].slug in slugs
    assert draft_product.slug not in slugs
    assert unpublished_active.slug not in slugs


def test_product_detail_by_slug(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    response = api_client.get(f"{PRODUCTS_URL}{product.slug}/")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["slug"] == product.slug
    assert len(body["data"]["variants"]) == 1
    assert body["data"]["variants"][0]["stock"]["available_quantity"] == 10


def test_product_detail_404_for_unpublished(api_client, category, brand):
    product = _make_product(category, brand, slug="hidden-honey", sku="HONEY-HIDDEN-001", published=False)
    response = api_client.get(f"{PRODUCTS_URL}{product.slug}/")
    assert response.status_code == 404


def test_category_tree_structure(api_client, category):
    child = Category.objects.create(name_en="Wildflower", name_ar="زهور برية", slug="wildflower", parent=category, is_active=True)

    response = api_client.get(CATEGORY_TREE_URL)

    assert response.status_code == 200
    body = response.json()
    root_slugs = {c["slug"] for c in body["data"]}
    assert category.slug in root_slugs
    root_entry = next(c for c in body["data"] if c["slug"] == category.slug)
    assert any(ch["slug"] == child.slug for ch in root_entry["children"])


def test_category_list(api_client, category):
    response = api_client.get(CATEGORIES_URL)
    assert response.status_code == 200
    body = response.json()
    assert any(c["slug"] == category.slug for c in body["data"])


def test_product_filter_by_category(api_client, product_with_variant_and_stock, category, brand):
    other_category = Category.objects.create(name_en="Grocery", name_ar="بقالة", slug="grocery", is_active=True)
    other_product = _make_product(other_category, brand, slug="grocery-item", sku="GROCERY-001")

    response = api_client.get(PRODUCTS_URL, {"category": category.slug})

    assert response.status_code == 200
    slugs = {p["slug"] for p in response.json()["data"]["results"]}
    assert product_with_variant_and_stock["product"].slug in slugs
    assert other_product.slug not in slugs


def test_product_filter_by_brand(api_client, product_with_variant_and_stock, category, brand):
    from apps.brands.models import Brand

    other_brand = Brand.objects.create(name="Other Brand", slug="other-brand", is_active=True)
    other_product = _make_product(category, other_brand, slug="other-brand-honey", sku="HONEY-OTHER-001")

    response = api_client.get(PRODUCTS_URL, {"brand": brand.slug})

    slugs = {p["slug"] for p in response.json()["data"]["results"]}
    assert product_with_variant_and_stock["product"].slug in slugs
    assert other_product.slug not in slugs


def test_product_filter_by_price_range(api_client, product_with_variant_and_stock, category, brand):
    cheap_product = _make_product(category, brand, slug="cheap-honey", sku="HONEY-CHEAP-001", price=Decimal("10.00"))
    expensive_product = _make_product(category, brand, slug="expensive-honey", sku="HONEY-EXP-001", price=Decimal("999.00"))

    response = api_client.get(PRODUCTS_URL, {"min_price": "100", "max_price": "500"})

    slugs = {p["slug"] for p in response.json()["data"]["results"]}
    assert product_with_variant_and_stock["product"].slug in slugs
    assert cheap_product.slug not in slugs
    assert expensive_product.slug not in slugs


def test_search_endpoint_finds_product_by_name(api_client, product_with_variant_and_stock):
    response = api_client.get(SEARCH_URL, {"q": "Sidr"})

    assert response.status_code == 200
    body = response.json()
    slugs = {p["slug"] for p in body["data"]["results"]}
    assert product_with_variant_and_stock["product"].slug in slugs


def test_search_endpoint_requires_query_param(api_client):
    response = api_client.get(SEARCH_URL)
    assert response.status_code == 400
