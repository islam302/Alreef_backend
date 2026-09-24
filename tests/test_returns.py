import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ErrorCodes
from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    ReturnItem,
    ReturnPolicySettings,
    ReturnReason,
    ReturnRequest,
    ReturnRequestStatus,
)
from apps.orders.services import complete_return_and_refund
from apps.payments.models import Payment, PaymentStatus, RefundStatus

pytestmark = pytest.mark.django_db


def _returns_create_url(order_number):
    return f"/api/v1/orders/{order_number}/returns/"


def test_create_return_request_within_window_succeeds(authenticated_client, delivered_order):
    order_item = delivered_order.items.first()

    response = authenticated_client.post(
        _returns_create_url(delivered_order.order_number),
        {"reason": ReturnReason.DAMAGED, "description": "Jar arrived broken", "items": [{"order_item_id": order_item.id, "quantity": 1}]},
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["data"]["status"] == ReturnRequestStatus.REQUESTED
    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.RETURN_REQUESTED


def test_create_return_request_outside_policy_window_fails(authenticated_client, delivered_order):
    ReturnPolicySettings.get_solo()  # ensure the row exists at its default 14-day window
    order_item = delivered_order.items.first()
    delivered_order.delivered_at = timezone.now() - datetime.timedelta(days=30)
    delivered_order.save(update_fields=["delivered_at"])

    response = authenticated_client.post(
        _returns_create_url(delivered_order.order_number),
        {"reason": ReturnReason.DAMAGED, "items": [{"order_item_id": order_item.id, "quantity": 1}]},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == ErrorCodes.ORDER_INVALID_TRANSITION
    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.DELIVERED


def test_create_return_request_quantity_exceeds_purchased_fails(authenticated_client, delivered_order):
    order_item = delivered_order.items.first()
    assert order_item.quantity == 1

    response = authenticated_client.post(
        _returns_create_url(delivered_order.order_number),
        {"reason": ReturnReason.DAMAGED, "items": [{"order_item_id": order_item.id, "quantity": 5}]},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.VALIDATION_ERROR
    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.DELIVERED


def _order_with_payment(user, *, quantity, unit_price, grand_total):
    order = Order.objects.create(
        order_number=f"ORD-RET-{user.id}-{quantity}-{unit_price}",
        user=user, customer_name="Jane Doe", customer_email=user.email, customer_phone="+201001234567",
        payment_method="cod", subtotal=Decimal(str(unit_price)) * quantity, grand_total=Decimal(str(grand_total)),
        status=OrderStatus.RETURN_REQUESTED,
    )
    order_item = OrderItem.objects.create(
        order=order, product_name_snapshot="Sidr Honey", sku_snapshot="HONEY-SIDR-001-500G",
        unit_price=Decimal(str(unit_price)), quantity=quantity, line_total=Decimal(str(unit_price)) * quantity,
    )
    payment = Payment.objects.create(order=order, provider="cod", method="cod", amount=Decimal(str(grand_total)), status=PaymentStatus.CAPTURED)
    return order, order_item, payment


def test_complete_return_and_refund_full_amount_marks_order_refunded(user):
    # grand_total == the returned line total exactly -> a full refund.
    order, order_item, payment = _order_with_payment(user, quantity=1, unit_price="250.00", grand_total="250.00")
    return_request = ReturnRequest.objects.create(order=order, user=user, reason=ReturnReason.DAMAGED, status=ReturnRequestStatus.APPROVED)
    ReturnItem.objects.create(return_request=return_request, order_item=order_item, quantity=1)

    result = complete_return_and_refund(return_request=return_request, processed_by=user)

    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED
    assert result.status == ReturnRequestStatus.REFUNDED
    refund = payment.refunds.get()
    assert refund.amount == Money("250.00", "EGP")
    assert refund.status == RefundStatus.PROCESSED


def test_complete_return_and_refund_partial_amount_marks_order_partially_refunded(user):
    # 2 units purchased, grand_total covers both, but only 1 unit is returned.
    order, order_item, payment = _order_with_payment(user, quantity=2, unit_price="250.00", grand_total="500.00")
    return_request = ReturnRequest.objects.create(order=order, user=user, reason=ReturnReason.DAMAGED, status=ReturnRequestStatus.APPROVED)
    ReturnItem.objects.create(return_request=return_request, order_item=order_item, quantity=1)

    result = complete_return_and_refund(return_request=return_request, processed_by=user)

    order.refresh_from_db()
    assert order.status == OrderStatus.PARTIALLY_REFUNDED
    assert result.status == ReturnRequestStatus.REFUNDED
    refund = payment.refunds.get()
    assert refund.amount == Money("250.00", "EGP")
