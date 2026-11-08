"""
Reviews from guests: somebody who ordered without an account reviews an item
of their own delivered order, proved like the tracking page proves it.

The rules are the account's rules — delivered orders only, one review per
purchased line, moderated before it shows — plus the guest-lookup guarantees:
a forged token, a wrong phone or somebody else's order all answer the same 404.
"""
import uuid

import pytest
from rest_framework.test import APIClient

from apps.orders.models import Order, OrderStatus
from apps.orders.services import transition_status
from apps.reviews.models import Review

pytestmark = pytest.mark.django_db

GUEST_REVIEW_URL = "/api/v1/reviews/guest/"
LOOKUP_URL = "/api/v1/orders/lookup/"
GUEST_PHONE = "01001234567"


def _guest_order(product, governorate, shipping_setup, *, full_name="Guest Buyer", phone=GUEST_PHONE, deliver=True):
    client = APIClient()
    client.post("/api/v1/cart/items/", {"variant_id": product["variant"].id, "quantity": 1}, format="json")
    response = client.post(
        "/api/v1/checkout/",
        {
            "contact": {"full_name": full_name, "phone": phone, "email": ""},
            "address": {"governorate": governorate.id, "district": "Downtown", "street": "12 Tahrir St", "building": "3"},
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    order = Order.objects.get(order_number=data["order"]["order_number"])
    if deliver:
        for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            order = transition_status(order, status)
    return order, data["access_token"]


@pytest.fixture
def delivered_guest_order(product_with_variant_and_stock, governorate, shipping_setup):
    return _guest_order(product_with_variant_and_stock, governorate, shipping_setup)


def _review(body):
    return APIClient().post(GUEST_REVIEW_URL, body, format="json")


def test_a_guest_reviews_an_item_of_their_delivered_order_with_the_token(delivered_guest_order):
    order, token = delivered_guest_order
    item = order.items.get()
    response = _review({"token": token, "order_item_id": item.id, "rating": 5, "comment": "عسل ممتاز"})
    assert response.status_code == 201, response.content
    review = Review.objects.get()
    assert review.user is None
    assert review.order_item_id == item.id
    assert review.product_id == item.product_id
    assert review.verified_purchase is True
    assert review.approved is False  # moderated first, like every review


def test_order_number_and_phone_work_as_well(delivered_guest_order):
    order, _ = delivered_guest_order
    item = order.items.get()
    response = _review({"order_number": order.order_number, "phone": GUEST_PHONE, "order_item_id": item.id, "rating": 4})
    assert response.status_code == 201, response.content


def test_an_order_not_yet_delivered_cannot_be_reviewed(product_with_variant_and_stock, governorate, shipping_setup):
    order, token = _guest_order(product_with_variant_and_stock, governorate, shipping_setup, deliver=False)
    response = _review({"token": token, "order_item_id": order.items.get().id, "rating": 5})
    assert response.status_code == 403
    assert response.json()["errors"]["code"] == "REVIEW_NOT_ELIGIBLE"
    assert not Review.objects.exists()


def test_one_review_per_purchased_item(delivered_guest_order):
    order, token = delivered_guest_order
    body = {"token": token, "order_item_id": order.items.get().id, "rating": 5}
    assert _review(body).status_code == 201
    second = _review(body)
    assert second.status_code == 409
    assert second.json()["errors"]["code"] == "REVIEW_ALREADY_EXISTS"
    assert Review.objects.count() == 1


def test_an_item_of_another_order_is_refused(delivered_guest_order, product_with_variant_and_stock, governorate, shipping_setup):
    order, token = delivered_guest_order
    other, _ = _guest_order(product_with_variant_and_stock, governorate, shipping_setup, phone="01009876543")
    response = _review({"token": token, "order_item_id": other.items.get().id, "rating": 5})
    assert response.status_code == 403
    assert not Review.objects.exists()


@pytest.mark.parametrize(
    "proof",
    [
        {"token": "forged.token"},
        {"order_number": "WILL-BE-SET", "phone": "01111111111"},  # wrong phone
        {"order_number": "ORD-00000000-99999", "phone": GUEST_PHONE},  # no such order
    ],
)
def test_a_bad_proof_answers_the_same_not_found(delivered_guest_order, proof):
    order, _ = delivered_guest_order
    if proof.get("order_number") == "WILL-BE-SET":
        proof = {**proof, "order_number": order.order_number}
    response = _review({**proof, "order_item_id": order.items.get().id, "rating": 5})
    assert response.status_code == 404
    assert response.json()["errors"]["code"] == "ORDER_NOT_FOUND"
    assert not Review.objects.exists()


def test_a_proof_is_required(delivered_guest_order):
    order, _ = delivered_guest_order
    response = _review({"order_item_id": order.items.get().id, "rating": 5})
    assert response.status_code == 400
    assert not Review.objects.exists()


def test_an_account_holders_order_cannot_be_reviewed_as_a_guest(authenticated_client, delivered_order):
    item = delivered_order.items.get()
    response = _review(
        {"order_number": delivered_order.order_number, "phone": delivered_order.customer_phone, "order_item_id": item.id, "rating": 5}
    )
    assert response.status_code == 404
    assert not Review.objects.exists()


def test_the_public_name_is_first_name_and_initial_from_the_order(
    api_client, product_with_variant_and_stock, governorate, shipping_setup
):
    order, token = _guest_order(product_with_variant_and_stock, governorate, shipping_setup, full_name="سارة محمد علي")
    _review({"token": token, "order_item_id": order.items.get().id, "rating": 5, "comment": "جميل"})
    Review.objects.update(approved=True)
    product_id = order.items.get().product_id
    listed = api_client.get(f"/api/v1/products/{product_id}/reviews/").json()
    names = [r["user_name"] for r in listed.get("results", listed.get("data", {}).get("results", []))]
    assert names == ["سارة ع."]
    latest = api_client.get("/api/v1/reviews/latest/").json()["data"]
    assert latest[0]["user_name"] == "سارة ع."
    assert GUEST_PHONE not in str(listed) and GUEST_PHONE not in str(latest)


def test_tracking_says_which_items_are_still_reviewable(delivered_guest_order):
    order, token = delivered_guest_order
    before = APIClient().post(LOOKUP_URL, {"token": token}, format="json").json()["data"]["items"][0]
    assert before["id"] == order.items.get().id and before["reviewed"] is False
    _review({"token": token, "order_item_id": before["id"], "rating": 5})
    after = APIClient().post(LOOKUP_URL, {"token": token}, format="json").json()["data"]["items"][0]
    assert after["reviewed"] is True


def test_staff_moderation_shows_guest_reviews(staff_client, delivered_guest_order):
    order, token = delivered_guest_order
    _review({"token": token, "order_item_id": order.items.get().id, "rating": 3, "comment": "كويس"})
    rows = staff_client.get("/api/v1/admin-api/reviews/").json()
    row = (rows.get("results") or rows["data"]["results"])[0] if isinstance(rows, dict) else rows[0]
    assert row["is_guest"] is True
    assert row["customer_name"] == "Guest Buyer"
    assert row["order_number"] == order.order_number
