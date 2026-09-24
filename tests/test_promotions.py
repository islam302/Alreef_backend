import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from apps.carts.models import Cart, CartItem
from apps.catalog.models import Product, ProductStatus, ProductType, ProductVariant
from apps.promotions.models import Promotion, PromotionType
from apps.promotions.services import compute_promotions_for_items

pytestmark = pytest.mark.django_db

PROMOTIONS_URL = "/api/v1/promotions/"


def test_list_active_promotions_excludes_future_and_expired(api_client):
    now = timezone.now()
    active = Promotion.objects.create(
        name="Always On", promotion_type=PromotionType.PERCENTAGE, percentage_value=5, is_active=True,
    )
    Promotion.objects.create(
        name="Future Flash Sale", promotion_type=PromotionType.PERCENTAGE, percentage_value=15, is_active=True,
        start_date=now + datetime.timedelta(days=5),
    )
    Promotion.objects.create(
        name="Expired Sale", promotion_type=PromotionType.PERCENTAGE, percentage_value=15, is_active=True,
        end_date=now - datetime.timedelta(days=1),
    )
    Promotion.objects.create(
        name="Disabled", promotion_type=PromotionType.PERCENTAGE, percentage_value=15, is_active=False,
    )

    response = api_client.get(PROMOTIONS_URL)

    assert response.status_code == 200
    names = {p["name"] for p in response.json()["data"]}
    assert names == {"Always On"}


def test_list_active_promotions_includes_currently_running_window(api_client):
    now = timezone.now()
    Promotion.objects.create(
        name="Ramadan Sale", promotion_type=PromotionType.PERCENTAGE, percentage_value=15, is_active=True,
        start_date=now - datetime.timedelta(days=1), end_date=now + datetime.timedelta(days=5),
    )

    response = api_client.get(PROMOTIONS_URL)

    names = {p["name"] for p in response.json()["data"]}
    assert "Ramadan Sale" in names


def _cart_item(cart, variant, quantity, unit_price):
    return CartItem.objects.create(cart=cart, variant=variant, quantity=quantity, unit_price_snapshot=unit_price)


def test_compute_promotions_percentage_scoped_to_category(product_with_variant_and_stock, category):
    variant = product_with_variant_and_stock["variant"]
    cart = Cart.objects.create(currency="EGP")
    item = _cart_item(cart, variant, quantity=2, unit_price=Money("250.00", "EGP"))  # line_total = 500.00

    promotion = Promotion.objects.create(
        name="10% off Honey", promotion_type=PromotionType.PERCENTAGE, percentage_value=10, is_active=True,
    )
    promotion.categories.add(category)

    total_discount, applied, free_shipping = compute_promotions_for_items([item], "EGP")

    assert total_discount == Money("50.00", "EGP")
    assert len(applied) == 1
    assert applied[0]["promotion"] == promotion
    assert free_shipping is False


def test_compute_promotions_percentage_scoped_category_does_not_match_other_category(product_with_variant_and_stock):
    from apps.categories.models import Category

    variant = product_with_variant_and_stock["variant"]  # category = Honey
    cart = Cart.objects.create(currency="EGP")
    item = _cart_item(cart, variant, quantity=1, unit_price=Money("250.00", "EGP"))

    other_category = Category.objects.create(name_en="Spices", name_ar="بهارات", slug="spices-promo", is_active=True)
    promotion = Promotion.objects.create(
        name="Spices only", promotion_type=PromotionType.PERCENTAGE, percentage_value=50, is_active=True,
    )
    promotion.categories.add(other_category)

    total_discount, applied, _ = compute_promotions_for_items([item], "EGP")

    assert total_discount == Money("0", "EGP")
    assert applied == []


def test_compute_promotions_buy_x_get_y_discounts_cheapest_units_first(product_with_variant_and_stock, brand, category):
    """Buy 2 get 1 free, unscoped. Cart has 2 units @ 100 and 1 unit @ 300
    (total qty 3 -> exactly one cycle -> 1 free unit). The discount must be
    taken from the cheapest unit (100), not the 300 one."""
    cheap_product = Product.objects.create(
        name_en="Cheap Honey", name_ar="عسل رخيص", slug="cheap-honey", sku="HONEY-CHEAP-001",
        category=category, brand=brand, product_type=ProductType.SIMPLE,
        status=ProductStatus.ACTIVE, published=True, price=Decimal("100.00"),
    )
    cheap_variant = ProductVariant.objects.create(
        product=cheap_product, sku="HONEY-CHEAP-001-V", name_suffix="250g", price=Decimal("100.00"), is_active=True, is_default=True,
    )
    expensive_variant = product_with_variant_and_stock["variant"]  # price 250, but we snapshot 300 below

    cart = Cart.objects.create(currency="EGP")
    cheap_item = _cart_item(cart, cheap_variant, quantity=2, unit_price=Money("100.00", "EGP"))
    expensive_item = _cart_item(cart, expensive_variant, quantity=1, unit_price=Money("300.00", "EGP"))

    promotion = Promotion.objects.create(
        name="Buy 2 Get 1 Free", promotion_type=PromotionType.BUY_X_GET_Y, buy_quantity=2, get_quantity=1,
        get_discount_percent=Decimal("100"), is_active=True,
    )

    total_discount, applied, _ = compute_promotions_for_items([cheap_item, expensive_item], "EGP")

    assert total_discount == Money("100.00", "EGP")  # the cheapest unit (100), not the 300 one
    assert len(applied) == 1
    assert applied[0]["promotion"] == promotion


def test_compute_promotions_buy_x_get_y_no_free_unit_below_cycle(product_with_variant_and_stock):
    variant = product_with_variant_and_stock["variant"]
    cart = Cart.objects.create(currency="EGP")
    item = _cart_item(cart, variant, quantity=2, unit_price=Money("250.00", "EGP"))  # only 2 units, cycle needs 3

    Promotion.objects.create(
        name="Buy 2 Get 1 Free", promotion_type=PromotionType.BUY_X_GET_Y, buy_quantity=2, get_quantity=1,
        get_discount_percent=Decimal("100"), is_active=True,
    )

    total_discount, applied, _ = compute_promotions_for_items([item], "EGP")

    assert total_discount == Money("0", "EGP")
    assert applied == []
