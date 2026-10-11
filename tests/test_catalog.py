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


def test_product_filter_by_badge_returns_only_marked_products(api_client, category, brand):
    marked = _make_product(category, brand, slug="best-honey", sku="HONEY-BEST-001", badges=["BEST_SELLER"])
    plain = _make_product(category, brand, slug="plain-honey", sku="HONEY-PLAIN-001")

    response = api_client.get(PRODUCTS_URL, {"badge": "best_seller"})

    slugs = {p["slug"] for p in response.json()["data"]["results"]}
    assert marked.slug in slugs
    assert plain.slug not in slugs


def test_product_filter_by_unknown_or_computed_badge_matches_nothing(api_client, category, brand):
    _make_product(category, brand, slug="best-honey", sku="HONEY-BEST-001", badges=["BEST_SELLER"])

    for code in ("OUT_OF_STOCK", "nonsense"):
        response = api_client.get(PRODUCTS_URL, {"badge": code})
        assert response.status_code == 200
        assert response.json()["data"]["results"] == []


def test_product_filter_by_slugs_returns_the_named_products(api_client, category, brand):
    first = _make_product(category, brand, slug="first-honey", sku="HONEY-FIRST-001")
    second = _make_product(category, brand, slug="second-honey", sku="HONEY-SECOND-001")
    other = _make_product(category, brand, slug="other-honey", sku="HONEY-OTHER-002")

    response = api_client.get(PRODUCTS_URL, {"slugs": f"{first.slug}, {second.slug},missing"})

    slugs = {p["slug"] for p in response.json()["data"]["results"]}
    assert slugs == {first.slug, second.slug}
    assert other.slug not in slugs


def test_product_filter_by_slugs_never_exposes_unpublished_products(api_client, category, brand):
    hidden = _make_product(category, brand, slug="hidden-honey", sku="HONEY-HIDDEN-001", published=False)

    response = api_client.get(PRODUCTS_URL, {"slugs": hidden.slug})

    assert response.json()["data"]["results"] == []


def test_search_endpoint_finds_product_by_name(api_client, product_with_variant_and_stock):
    response = api_client.get(SEARCH_URL, {"q": "Sidr"})

    assert response.status_code == 200
    body = response.json()
    slugs = {p["slug"] for p in body["data"]["results"]}
    assert product_with_variant_and_stock["product"].slug in slugs


def test_search_endpoint_requires_query_param(api_client):
    response = api_client.get(SEARCH_URL)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Recently-viewed tracking must never break the product page
# ---------------------------------------------------------------------------


def test_product_detail_survives_repeated_views_by_the_same_guest(
    api_client, product_with_variant_and_stock
):
    """The regression that made this matter: the unique constraint on
    RecentlyViewed listed three columns, one of which is always NULL, and
    Postgres treats NULL as distinct from NULL — so it enforced nothing.
    Duplicate rows accumulated and update_or_create's get() then raised
    MultipleObjectsReturned on every later view, leaving the product page
    permanently 500 for that visitor."""
    slug = product_with_variant_and_stock["product"].slug

    for _ in range(4):
        response = api_client.get(f"/api/v1/products/{slug}/")
        assert response.status_code == 200, response.content

    from apps.catalog.models import RecentlyViewed

    assert RecentlyViewed.objects.count() == 1


def test_product_detail_answers_even_when_tracking_blows_up(
    api_client, product_with_variant_and_stock
):
    """Second line of defence. Recently-viewed is a convenience feature on the
    busiest page in the shop; whatever goes wrong inside it, the customer
    must still get the product."""
    from unittest import mock

    slug = product_with_variant_and_stock["product"].slug

    with mock.patch(
        "apps.catalog.models.RecentlyViewed.objects.update_or_create",
        side_effect=RuntimeError("tracking exploded"),
    ):
        response = api_client.get(f"/api/v1/products/{slug}/")

    assert response.status_code == 200, response.content
    assert response.json()["data"]["slug"] == slug


def test_recently_viewed_rejects_a_duplicate_at_the_database_level(
    db, product_with_variant_and_stock, user
):
    """The constraint itself, not the view's handling of it."""
    from django.db import IntegrityError, transaction

    from apps.catalog.models import RecentlyViewed

    product = product_with_variant_and_stock["product"]

    RecentlyViewed.objects.create(user=None, session_key="abc123", product=product)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RecentlyViewed.objects.create(user=None, session_key="abc123", product=product)

    RecentlyViewed.objects.create(user=user, session_key=None, product=product)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RecentlyViewed.objects.create(user=user, session_key=None, product=product)
