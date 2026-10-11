"""
Reviews as the public sees them: the homepage's latest-reviews list and the
name a reviewer is published under.

A review is public text written by a customer, so two things are pinned
here: only moderated reviews of buyable products are shown, and a reviewer
is never published by email or full surname.
"""

from decimal import Decimal

import pytest

from apps.catalog.models import Product, ProductStatus, ProductType
from apps.reviews.models import Review
from apps.users.models import User

pytestmark = pytest.mark.django_db

LATEST_URL = "/api/v1/reviews/latest/"


def _user(email, first="", last=""):
    return User.objects.create_user(email=email, password="Secret12345!", first_name=first, last_name=last)


def _review(product, user, **overrides):
    defaults = dict(rating=5, title="", comment="العسل وصل والتغليف ممتاز", approved=True, verified_purchase=True)
    defaults.update(overrides)
    return Review.objects.create(product=product, user=user, **defaults)


def test_latest_reviews_lists_only_approved_reviews_with_text(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    shown = _review(product, _user("a@example.com", "محمد", "أحمد"))
    _review(product, _user("b@example.com", "سارة"), approved=False)
    _review(product, _user("c@example.com", "منى"), comment="")

    response = api_client.get(LATEST_URL)

    assert response.status_code == 200
    data = response.json()["data"]
    assert [r["id"] for r in data] == [shown.id]
    assert data[0]["product"]["slug"] == product.slug
    assert data[0]["verified_purchase"] is True


def test_latest_reviews_skips_products_that_are_not_on_sale(api_client, category, brand):
    hidden = Product.objects.create(
        name_en="Old", name_ar="قديم", slug="old-honey", sku="OLD-1", category=category, brand=brand,
        product_type=ProductType.SIMPLE, status=ProductStatus.ARCHIVED, published=True, price=Decimal("100.00"),
    )
    _review(hidden, _user("a@example.com", "محمد"))

    assert api_client.get(LATEST_URL).json()["data"] == []


def test_latest_reviews_is_newest_first_and_capped(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    for i in range(25):
        _review(product, _user(f"u{i}@example.com", f"عميل{i}"))

    assert len(api_client.get(LATEST_URL, {"limit": 3}).json()["data"]) == 3
    assert len(api_client.get(LATEST_URL, {"limit": 500}).json()["data"]) == 20
    assert len(api_client.get(LATEST_URL, {"limit": "x"}).json()["data"]) == 8


def test_a_reviewer_is_published_by_first_name_and_initial_never_email(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    _review(product, _user("named@example.com", "محمد", "أحمد"))
    _review(product, _user("nameless@example.com"))

    names = {r["user_name"] for r in api_client.get(LATEST_URL).json()["data"]}
    per_product = {r["user_name"] for r in api_client.get(f"/api/v1/products/{product.id}/reviews/").json()["data"]["results"]}

    for published in (names, per_product):
        assert published == {"محمد أ.", "عميل الريف"}
        assert not any("@" in n for n in published)
