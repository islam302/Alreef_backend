"""
`?category=<slug>` takes in the sub-categories, so splitting `honey` into
children (sidr-honey, flower-honey, ...) keeps /products/honey whole, and
`?category=offers` is resolved from prices rather than a product's one FK.
"""
from decimal import Decimal

import pytest

from apps.catalog.models import Product, ProductStatus, ProductType
from apps.categories.models import Category

pytestmark = pytest.mark.django_db

PRODUCTS_URL = "/api/v1/products/"
CATEGORIES_URL = "/api/v1/categories/"


def _product(category, slug, **extra):
    fields = dict(
        name_en=slug, name_ar=slug, slug=slug, sku=slug.upper(), category=category,
        product_type=ProductType.SIMPLE, status=ProductStatus.ACTIVE, published=True,
        price=Decimal("300.00"),
    )
    fields.update(extra)
    return Product.objects.create(**fields)


def _slugs(client, **params):
    response = client.get(PRODUCTS_URL, {"page_size": 100, **params})
    assert response.status_code == 200
    return {p["slug"] for p in response.json()["data"]["results"]}


@pytest.fixture
def tree(category):
    sidr = Category.objects.create(name_en="Sidr", name_ar="عسل السدر", slug="sidr-honey", parent=category)
    flower = Category.objects.create(name_en="Flower", name_ar="عسل الزهور", slug="flower-honey", parent=category)
    other = Category.objects.create(name_en="Grocery", name_ar="بقالة", slug="grocery")
    return {
        "root": _product(category, "plain-honey"),
        "sidr": _product(sidr, "saudi-sidr"),
        "flower": _product(flower, "citrus"),
        "other": _product(other, "tahini"),
        "draft": _product(sidr, "draft-sidr", status=ProductStatus.DRAFT),
    }


def test_parent_category_lists_its_children_products(api_client, tree):
    assert _slugs(api_client, category="honey") == {"plain-honey", "saudi-sidr", "citrus"}


def test_child_category_lists_only_its_own(api_client, tree):
    assert _slugs(api_client, category="sidr-honey") == {"saudi-sidr"}
    assert _slugs(api_client, category="flower-honey") == {"citrus"}


def test_unknown_category_lists_nothing(api_client, tree):
    assert _slugs(api_client, category="no-such-shelf") == set()


def test_offers_lists_what_is_on_sale_whatever_its_category(api_client, tree):
    tree["sidr"].compare_at_price = Decimal("400.00")
    tree["sidr"].save()
    # Not a discount: the old price is not above the price.
    tree["flower"].compare_at_price = Decimal("300.00")
    tree["flower"].save()

    assert _slugs(api_client, category="offers") == {"saudi-sidr"}


def test_offers_also_lists_products_filed_under_an_offers_category(api_client, category, tree):
    offers = Category.objects.create(name_en="Offers", name_ar="العروض", slug="offers", parent=category)
    _product(offers, "gift-box")

    assert _slugs(api_client, category="offers") == {"gift-box"}
    assert "gift-box" in _slugs(api_client, category="honey")


def test_category_list_carries_the_seo_fields(api_client, category):
    Category.objects.create(
        name_en="Sidr", name_ar="عسل السدر", slug="sidr-honey", parent=category,
        seo_title="عسل السدر الأصلي", seo_description="عسل سدر طبيعي",
    )
    rows = {c["slug"]: c for c in api_client.get(CATEGORIES_URL).json()["data"]}

    assert rows["sidr-honey"]["seo_title"] == "عسل السدر الأصلي"
    assert rows["sidr-honey"]["seo_description"] == "عسل سدر طبيعي"
    assert rows["sidr-honey"]["parent"] == category.id
