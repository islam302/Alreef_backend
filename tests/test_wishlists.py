import pytest

from apps.wishlists.models import WishlistItem

pytestmark = pytest.mark.django_db

WISHLIST_URL = "/api/v1/wishlist/"
WISHLIST_ADD_URL = "/api/v1/wishlist/add/"


def _remove_url(product_id):
    return f"/api/v1/wishlist/{product_id}/"


def _check_url(product_id):
    return f"/api/v1/wishlist/{product_id}/check/"


def test_wishlist_requires_authentication(api_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]

    response = api_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")

    assert response.status_code == 401


def test_add_item_to_wishlist(authenticated_client, user, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]

    response = authenticated_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")

    assert response.status_code == 201
    assert WishlistItem.objects.filter(user=user, product=product).exists()


def test_duplicate_add_is_idempotent(authenticated_client, user, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]

    first = authenticated_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")
    second = authenticated_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")

    assert first.status_code == 201
    assert second.status_code == 201
    assert WishlistItem.objects.filter(user=user, product=product).count() == 1


def test_list_wishlist_items(authenticated_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    authenticated_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")

    response = authenticated_client.get(WISHLIST_URL)

    assert response.status_code == 200
    products = [item["product"]["id"] for item in response.json()["data"]]
    assert product.id in products


def test_check_wishlist_membership(authenticated_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]

    before = authenticated_client.get(_check_url(product.id))
    authenticated_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")
    after = authenticated_client.get(_check_url(product.id))

    assert before.json()["data"]["in_wishlist"] is False
    assert after.json()["data"]["in_wishlist"] is True


def test_remove_item_from_wishlist(authenticated_client, user, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    authenticated_client.post(WISHLIST_ADD_URL, {"product_id": product.id}, format="json")

    response = authenticated_client.delete(_remove_url(product.id))

    assert response.status_code == 204
    assert not WishlistItem.objects.filter(user=user, product=product).exists()


def test_remove_item_not_in_wishlist_returns_404(authenticated_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]

    response = authenticated_client.delete(_remove_url(product.id))

    assert response.status_code == 404
