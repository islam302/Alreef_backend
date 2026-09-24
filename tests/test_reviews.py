import pytest

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.reviews.models import Review
from apps.users.models import User

pytestmark = pytest.mark.django_db

REVIEWS_CREATE_URL = "/api/v1/reviews/"


def _reviews_list_url(product_id):
    return f"/api/v1/products/{product_id}/reviews/"


def _helpful_url(review_id):
    return f"/api/v1/reviews/{review_id}/helpful/"


def test_create_review_without_delivered_order_is_rejected(authenticated_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]

    response = authenticated_client.post(
        REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 5, "title": "Great", "comment": "Loved it"}, format="json",
    )

    assert response.status_code == 403
    assert response.json()["errors"]["code"] == ErrorCodes.REVIEW_NOT_ELIGIBLE


def test_create_review_for_delivered_order_succeeds(authenticated_client, delivered_order):
    product = delivered_order.items.first().product

    response = authenticated_client.post(
        REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 5, "title": "Amazing honey", "comment": "Will buy again"}, format="json",
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["rating"] == 5
    assert body["verified_purchase"] is True

    review = Review.objects.get(pk=body["id"])
    assert review.order_item == delivered_order.items.first()
    assert review.approved is False  # pending moderation


def test_reviewing_an_already_reviewed_order_item_is_no_longer_eligible(authenticated_client, delivered_order):
    """get_eligible_order_item() excludes order items that already carry a
    review (review__isnull=True), so once the single delivered order_item
    for a product has been reviewed, a second attempt is turned away as
    REVIEW_NOT_ELIGIBLE rather than reaching the order_item a second time."""
    product = delivered_order.items.first().product
    authenticated_client.post(REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 4}, format="json")

    response = authenticated_client.post(REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 5}, format="json")

    assert response.status_code == 403
    assert response.json()["errors"]["code"] == ErrorCodes.REVIEW_NOT_ELIGIBLE


def test_duplicate_review_on_same_order_item_raises_review_already_exists(user, delivered_order, monkeypatch):
    """Exercises the REVIEW_ALREADY_EXISTS guard in apps.reviews.services.create_review
    directly. Under normal traffic this order_item would already have been
    filtered out by get_eligible_order_item (see test above) -- this test
    forces get_eligible_order_item to keep returning the same order_item,
    the way a genuine race between two concurrent requests for the same
    order_item would, to prove the duplicate guard itself is correct."""
    from apps.reviews import services as review_services

    order_item = delivered_order.items.first()
    product = order_item.product

    review_services.create_review(user=user, product=product, rating=4)

    monkeypatch.setattr(review_services, "get_eligible_order_item", lambda *, user, product: order_item)

    with pytest.raises(ApplicationError) as exc_info:
        review_services.create_review(user=user, product=product, rating=5)

    assert exc_info.value.code == ErrorCodes.REVIEW_ALREADY_EXISTS


def test_unapproved_reviews_do_not_appear_in_public_list(authenticated_client, delivered_order):
    product = delivered_order.items.first().product
    authenticated_client.post(REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 4, "comment": "Pending review"}, format="json")

    response = authenticated_client.get(_reviews_list_url(product.id))

    assert response.status_code == 200
    assert response.json()["data"]["results"] == []


def test_approved_review_appears_in_public_list(authenticated_client, delivered_order):
    product = delivered_order.items.first().product
    create_response = authenticated_client.post(REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 4}, format="json")
    review = Review.objects.get(pk=create_response.json()["data"]["id"])
    review.approved = True
    review.save(update_fields=["approved"])

    response = authenticated_client.get(_reviews_list_url(product.id))

    assert response.status_code == 200
    ids = [r["id"] for r in response.json()["data"]["results"]]
    assert review.id in ids


def test_vote_helpful_is_idempotent(authenticated_client, delivered_order):
    product = delivered_order.items.first().product
    create_response = authenticated_client.post(REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 4}, format="json")
    review = Review.objects.get(pk=create_response.json()["data"]["id"])
    review.approved = True
    review.save(update_fields=["approved"])

    first = authenticated_client.post(_helpful_url(review.id), format="json")
    second = authenticated_client.post(_helpful_url(review.id), format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["helpful_votes"] == 1
    assert second.json()["data"]["helpful_votes"] == 1
    review.refresh_from_db()
    assert review.helpful_votes == 1


def test_vote_helpful_by_different_users_counts_separately(api_client, authenticated_client, delivered_order):
    product = delivered_order.items.first().product
    create_response = authenticated_client.post(REVIEWS_CREATE_URL, {"product_id": product.id, "rating": 4}, format="json")
    review = Review.objects.get(pk=create_response.json()["data"]["id"])
    review.approved = True
    review.save(update_fields=["approved"])

    other_user = User.objects.create_user(email="voter@example.com", password="SuperSecret123")
    from rest_framework_simplejwt.tokens import RefreshToken
    from rest_framework.test import APIClient

    other_client = APIClient()
    refresh = RefreshToken.for_user(other_user)
    other_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    authenticated_client.post(_helpful_url(review.id), format="json")
    response = other_client.post(_helpful_url(review.id), format="json")

    assert response.json()["data"]["helpful_votes"] == 2
