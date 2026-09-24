import uuid

import pytest

from apps.core.error_codes import ErrorCodes
from apps.orders.models import Order, OrderStatus
from apps.orders.services import transition_status
from apps.payments.models import Payment, PaymentStatus

pytestmark = pytest.mark.django_db

CART_ITEMS_URL = "/api/v1/cart/items/"
CHECKOUT_URL = "/api/v1/checkout/"


def _checkout_payload(address, method, coupon_code=""):
    return {
        "address_id": address.id,
        "shipping_method_id": method.id,
        "payment_method": "cod",
        "coupon_code": coupon_code,
    }


def _add_to_cart(client, variant, quantity=2):
    return client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": quantity}, format="json")


def test_full_cod_checkout_confirms_order_and_decrements_inventory(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    variant = product_with_variant_and_stock["variant"]
    batch = product_with_variant_and_stock["batch"]
    method = shipping_setup["method"]

    _add_to_cart(authenticated_client, variant, quantity=2)

    response = authenticated_client.post(
        CHECKOUT_URL, _checkout_payload(address, method), format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 201
    body = response.json()
    order_data = body["data"]["order"]
    payment_data = body["data"]["payment"]

    assert order_data["status"] == OrderStatus.CONFIRMED
    assert len(order_data["items"]) == 1
    assert order_data["items"][0]["quantity"] == 2
    assert order_data["items"][0]["sku_snapshot"] == variant.sku

    # A COD order is confirmed but NOT paid. Nobody has collected any cash yet,
    # and this assertion used to say CAPTURED — which is how the bug survived:
    # the test agreed with the code that money we did not have was ours.
    assert payment_data["provider"] == "cod"
    assert payment_data["status"] == PaymentStatus.PENDING

    payment = Payment.objects.get(pk=payment_data["id"])
    assert payment.provider == "cod"
    assert payment.status == PaymentStatus.PENDING
    # The customer is told what to have ready at the door.
    assert payment.metadata["collect_amount"]

    # Stock is still committed: the order is real, so the honey is spoken for.
    batch.refresh_from_db()
    assert batch.quantity == 8  # 10 - 2, consumed on confirmation


def test_cod_payment_is_captured_when_the_order_is_delivered(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    """
    The other half of COD: delivery is when the cash becomes ours.

    Walks the order through the real status machine rather than calling the
    payment service directly, because the thing worth protecting is that *any*
    route to DELIVERED collects the cash — the hook is on the transition, not on
    one dashboard button.
    """
    from apps.orders.models import OrderStatus as OS
    from apps.orders.services import transition_status

    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    _add_to_cart(authenticated_client, variant, quantity=1)

    response = authenticated_client.post(
        "/api/v1/checkout/",
        {"address_id": address.id, "shipping_method_id": method.id, "payment_method": "cod"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    payment = Payment.objects.get(order=order)
    assert payment.status == PaymentStatus.PENDING

    for status in (OS.PROCESSING, OS.PACKED, OS.SHIPPED, OS.DELIVERED):
        order = transition_status(order, status)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    # And it is idempotent: re-running the collection changes nothing.
    from apps.payments.services import record_cod_collection

    record_cod_collection(payment)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.refunds.count() == 0


def test_checkout_retry_with_same_idempotency_key_returns_same_order(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    _add_to_cart(authenticated_client, variant, quantity=1)

    key = str(uuid.uuid4())
    payload = _checkout_payload(address, method)

    first = authenticated_client.post(CHECKOUT_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
    second = authenticated_client.post(CHECKOUT_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["order"]["order_number"] == second.json()["data"]["order"]["order_number"]
    assert Order.objects.filter(user__isnull=False).count() == 1


def test_checkout_requires_idempotency_key_header(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    _add_to_cart(authenticated_client, variant, quantity=1)

    response = authenticated_client.post(CHECKOUT_URL, _checkout_payload(address, method), format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.IDEMPOTENCY_KEY_REQUIRED


def test_checkout_with_empty_cart_raises_cart_empty(authenticated_client, address, shipping_setup):
    response = authenticated_client.post(
        CHECKOUT_URL, _checkout_payload(address, shipping_setup["method"]), format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.CART_EMPTY


def _place_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1):
    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    _add_to_cart(authenticated_client, variant, quantity=quantity)
    response = authenticated_client.post(
        CHECKOUT_URL, _checkout_payload(address, method), format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201
    order_number = response.json()["data"]["order"]["order_number"]
    return Order.objects.get(order_number=order_number)


def test_order_cancel_from_confirmed_succeeds(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    order = _place_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup)
    assert order.status == OrderStatus.CONFIRMED

    response = authenticated_client.post(
        f"/api/v1/orders/{order.order_number}/cancel/", {"reason": "Changed my mind"}, format="json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == OrderStatus.CANCELLED
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED
    assert order.cancel_reason == "Changed my mind"


def test_order_cancel_from_delivered_fails(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    order = _place_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup)
    # March the order through the legal state machine up to DELIVERED.
    for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
        order = transition_status(order, status)
    assert order.status == OrderStatus.DELIVERED

    response = authenticated_client.post(
        f"/api/v1/orders/{order.order_number}/cancel/", {"reason": "too late"}, format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == ErrorCodes.ORDER_CANNOT_CANCEL
    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED


def test_order_detail_and_list_endpoints(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    order = _place_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup)

    list_response = authenticated_client.get("/api/v1/orders/")
    assert list_response.status_code == 200
    order_numbers = {o["order_number"] for o in list_response.json()["data"]["results"]}
    assert order.order_number in order_numbers

    detail_response = authenticated_client.get(f"/api/v1/orders/{order.order_number}/")
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["order_number"] == order.order_number
