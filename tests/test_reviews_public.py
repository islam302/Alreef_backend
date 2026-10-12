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


# --------------------------------------------------------------------------
# Branch customers: opinions given at the store, recorded by staff
# --------------------------------------------------------------------------
from datetime import date  # noqa: E402

from apps.reviews.models import StoreReview  # noqa: E402

STORE_URL = "/api/v1/admin-api/store-reviews/"


def _store(**overrides):
    defaults = dict(customer_name="أحمد م.", comment="اشتريت من الفرع والعسل ممتاز", consent=True, published=True)
    defaults.update(overrides)
    return StoreReview.objects.create(**defaults)


def test_latest_reviews_mix_online_and_branch_opinions_and_say_which(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    online = _review(product, _user("a@example.com", "محمد", "أحمد"))
    branch = _store(product=product, rating=None, collected_on=date.today())

    data = api_client.get(LATEST_URL).json()["data"]

    by_source = {r["source"]: r for r in data}
    assert set(by_source) == {"online", "in_store"}
    assert by_source["online"]["id"] == online.id
    shop = by_source["in_store"]
    assert shop["id"] == branch.id
    assert shop["user_name"] == "أحمد م."
    # Nothing a branch opinion cannot know is filled in for it.
    assert shop["rating"] is None
    assert shop["verified_purchase"] is False
    assert shop["product"]["slug"] == product.slug


def test_branch_opinions_need_consent_and_publishing(api_client):
    _store(published=False)
    _store(consent=False, published=True)  # cannot happen through the API; still never shown
    shown = _store()

    data = api_client.get(LATEST_URL).json()["data"]

    assert [(r["source"], r["id"]) for r in data] == [("in_store", shown.id)]


def test_a_branch_opinion_drops_a_product_that_is_no_longer_sold(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    _store(product=product)
    product.status = ProductStatus.ARCHIVED
    product.save(update_fields=["status"])

    (row,) = api_client.get(LATEST_URL).json()["data"]
    assert row["product"] is None


def test_staff_record_a_branch_opinion_and_publish_it_only_with_consent(staff_client, staff_user):
    created = staff_client.post(STORE_URL, {"customer_name": "  منى   ع. ", "comment": "خدمة ممتازة", "rating": 5}, format="json")
    assert created.status_code == 201, created.content
    body = created.json()["data"]
    assert body["customer_name"] == "منى ع."
    assert body["published"] is False
    assert StoreReview.objects.get(pk=body["id"]).created_by == staff_user

    refused = staff_client.patch(f"{STORE_URL}{body['id']}/", {"published": True}, format="json")
    assert refused.status_code == 400

    ok = staff_client.patch(f"{STORE_URL}{body['id']}/", {"consent": True, "published": True}, format="json")
    assert ok.status_code == 200
    assert ok.json()["data"]["published"] is True


def test_a_branch_opinion_never_names_by_email_or_phone(staff_client):
    for name in ["mona@example.com", "منى 01012345678", "منى ٠١٠١٢٣٤٥٦٧٨"]:
        response = staff_client.post(STORE_URL, {"customer_name": name, "comment": "ممتاز"}, format="json")
        assert response.status_code == 400, name


def test_only_staff_can_touch_branch_opinions(api_client, authenticated_client):
    assert api_client.get(STORE_URL).status_code == 401
    assert authenticated_client.post(STORE_URL, {"customer_name": "منى", "comment": "ممتاز"}, format="json").status_code == 403
