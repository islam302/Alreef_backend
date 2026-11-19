import datetime
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product, ProductImage, ProductStatus, ProductType, ProductVariant
from apps.inventory.models import Batch
from apps.inventory.services import get_or_create_item
from apps.shipping.models import RateCalculationType, ShippingMethod, ShippingRate, ShippingZone

pytestmark = pytest.mark.django_db

FEED_URL = "/api/v1/feeds/google-merchant.xml"
G = "{http://base.google.com/ns/1.0}"


@pytest.fixture(autouse=True)
def _frontend(settings):
    settings.FRONTEND_URL = "https://shop.example.test"


def _product(category, slug, **extra):
    fields = dict(
        name_en=slug, name_ar="عسل سدر & <جبلي>", slug=slug, sku=f"P-{slug.upper()}", category=category,
        product_type=ProductType.VARIABLE, status=ProductStatus.ACTIVE, published=True,
        price=Decimal("250.00"), description="عسل <b>طبيعي</b> & أصلي",
    )
    fields.update(extra)
    return Product.objects.create(**fields)


def _variant(product, sku, grams, price, stock=0, **extra):
    extra.setdefault("is_active", True)
    variant = ProductVariant.objects.create(
        product=product, sku=sku, weight_grams=grams, price=Decimal(price), **extra,
    )
    item = get_or_create_item(variant)
    if stock:
        Batch.objects.create(
            item=item, batch_number=f"B-{sku}", quantity=stock, initial_quantity=stock,
            expiration_date=timezone.localdate() + datetime.timedelta(days=365), is_active=True,
        )
    return variant


def _feed(client):
    response = client.get(FEED_URL)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/xml")
    assert "public" in response["Cache-Control"]
    root = ET.fromstring(response.content)  # raises on malformed XML
    assert root.tag == "rss" and root.get("version") == "2.0"
    return {item.findtext(f"{G}id"): item for item in root.iter("item")}


@pytest.fixture
def catalogue(category):
    product = _product(category, "mountain-sidr")
    small = _variant(product, "SIDR-250G", 250, "250.00", stock=5, is_default=True, sort_order=0)
    large = _variant(
        product, "SIDR-1KG", 1000, "900.00", stock=0, sort_order=1, compare_at_price=Decimal("1000.00"),
    )
    _variant(product, "SIDR-OLD", 500, "400.00", stock=5, is_active=False)
    ProductImage.objects.create(product=product, image="products/1/front.jpg", is_primary=True)
    ProductImage.objects.create(product=product, image="products/1/side.jpg", sort_order=1)
    ProductImage.objects.create(product=product, variant=large, image="products/1/kilo.jpg")

    draft = _product(category, "draft-honey", status=ProductStatus.DRAFT)
    _variant(draft, "DRAFT-1", 500, "100.00", stock=5)
    ProductImage.objects.create(product=draft, image="products/2/a.jpg")
    hidden = _product(category, "hidden-honey", published=False)
    _variant(hidden, "HIDDEN-1", 500, "100.00", stock=5)
    ProductImage.objects.create(product=hidden, image="products/3/a.jpg")
    return {"product": product, "small": small, "large": large}


def test_one_item_per_active_size_of_published_products_only(api_client, catalogue):
    items = _feed(api_client)
    assert set(items) == {"SIDR-250G", "SIDR-1KG"}


def test_item_fields(api_client, catalogue):
    items = _feed(api_client)
    small, large = items["SIDR-250G"], items["SIDR-1KG"]

    assert small.findtext(f"{G}item_group_id") == "P-MOUNTAIN-SIDR"
    assert small.findtext("title") == "عسل سدر & <جبلي> 250 جم"
    assert large.findtext("title") == "عسل سدر & <جبلي> 1 كجم"
    assert small.findtext("description") == "عسل طبيعي & أصلي"
    assert small.findtext("link") == "https://shop.example.test/product/mountain-sidr?size=250"
    assert large.findtext("link") == "https://shop.example.test/product/mountain-sidr?size=1000"
    assert small.findtext(f"{G}brand") == "الريف"
    assert small.findtext(f"{G}condition") == "new"
    assert small.findtext(f"{G}identifier_exists") == "no"
    assert "Honey" in small.findtext(f"{G}google_product_category")


def test_prices_and_sale_prices(api_client, catalogue):
    items = _feed(api_client)
    small, large = items["SIDR-250G"], items["SIDR-1KG"]

    assert small.findtext(f"{G}price") == "250.00 EGP"
    assert small.find(f"{G}sale_price") is None
    # On sale: the regular price is the old one, the sale price what is charged.
    assert large.findtext(f"{G}price") == "1000.00 EGP"
    assert large.findtext(f"{G}sale_price") == "900.00 EGP"


def test_availability_follows_real_stock(api_client, catalogue):
    items = _feed(api_client)
    assert items["SIDR-250G"].findtext(f"{G}availability") == "in_stock"
    assert items["SIDR-1KG"].findtext(f"{G}availability") == "out_of_stock"


def test_images_are_absolute_and_never_another_sizes(api_client, catalogue):
    items = _feed(api_client)
    small, large = items["SIDR-250G"], items["SIDR-1KG"]

    assert small.findtext(f"{G}image_link") == "http://testserver/media/products/1/front.jpg"
    assert [e.text for e in small.findall(f"{G}additional_image_link")] == [
        "http://testserver/media/products/1/side.jpg"
    ]
    assert large.findtext(f"{G}image_link").endswith("/products/1/kilo.jpg")
    assert len(large.findall(f"{G}additional_image_link")) == 2


def test_shipping_is_given_only_for_a_single_flat_fee(api_client, catalogue, governorate):
    from django.core.cache import cache

    assert _feed(api_client)["SIDR-250G"].find(f"{G}shipping") is None

    zone = ShippingZone.objects.create(name="Egypt", is_active=True)
    zone.governorates.add(governorate)
    standard = ShippingMethod.objects.create(name="Standard", is_active=True)
    ShippingRate.objects.create(
        zone=zone, method=standard, calculation_type=RateCalculationType.FLAT, base_price=Decimal("60.00"),
    )
    cache.clear()
    shipping = _feed(api_client)["SIDR-250G"].find(f"{G}shipping")
    assert shipping.findtext(f"{G}country") == "EG"
    assert shipping.findtext(f"{G}price") == "60.00 EGP"

    express = ShippingMethod.objects.create(name="Express", method_type="express", is_active=True)
    ShippingRate.objects.create(
        zone=zone, method=express, calculation_type=RateCalculationType.FLAT, base_price=Decimal("100.00"),
    )
    cache.clear()
    assert _feed(api_client)["SIDR-250G"].find(f"{G}shipping") is None


def test_feed_is_cached(api_client, catalogue):
    _feed(api_client)
    Product.objects.filter(pk=catalogue["product"].pk).update(published=False)
    assert set(_feed(api_client)) == {"SIDR-250G", "SIDR-1KG"}


def test_feed_is_read_only(api_client, catalogue):
    assert api_client.post(FEED_URL).status_code == 405


def test_empty_catalogue_is_still_a_valid_feed(api_client, db):
    assert _feed(api_client) == {}
