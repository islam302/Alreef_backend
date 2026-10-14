"""
Stock that an order no longer needs goes back on the shelf.

Two leaks, both silent until a product shows "sold out" with jars in the store:

* A confirmed order (every COD order is confirmed at checkout) has its stock
  consumed. Cancelling it released only *active* reservations, so the consumed
  units were simply gone.
* An online order whose payment never completed held its reservation forever:
  order reservations have no expiry and nothing cancelled the order.
"""
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.inventory.models import InventoryItem
from apps.orders.models import Order, OrderStatus
from apps.orders.services import cancel_order
from apps.payments.models import Payment, PaymentStatus
from apps.payments.providers.base import StatusResult
from apps.payments.tasks import release_unpaid_orders_task

pytestmark = pytest.mark.django_db


def _available(stock):
    return InventoryItem.objects.get(pk=stock["item"].pk).available_quantity


def test_cancelling_a_confirmed_cod_order_puts_the_stock_back(gateway_payment, product_with_variant_and_stock):
    payment = gateway_payment(provider="cod", method="cod", quantity=3)
    assert payment.order.status == OrderStatus.CONFIRMED
    assert _available(product_with_variant_and_stock) == 7

    cancel_order(payment.order, reason="customer called")

    assert _available(product_with_variant_and_stock) == 10


def test_cancelling_twice_cannot_restock_twice(gateway_payment, product_with_variant_and_stock):
    from apps.core.error_codes import ApplicationError

    payment = gateway_payment(provider="cod", method="cod", quantity=3)
    cancel_order(payment.order, reason="once")
    with pytest.raises(ApplicationError):
        cancel_order(Order.objects.get(pk=payment.order_id), reason="twice")

    assert _available(product_with_variant_and_stock) == 10


# ---------------------------------------------------------------------------
# Online orders nobody paid for
# ---------------------------------------------------------------------------


class _Gateway:
    def __init__(self, status, raw=None):
        self.status, self.raw = status, raw or {}

    def get_status(self, *, payment):
        return StatusResult(status=self.status, raw=self.raw)

    def cancel(self, *, payment):
        return True


def _age(payment, hours):
    when = timezone.now() - timedelta(hours=hours)
    Order.objects.filter(pk=payment.order_id).update(created_at=when)
    Payment.objects.filter(pk=payment.pk).update(created_at=when)


def _install(monkeypatch, gateway):
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: gateway)


def test_a_failed_card_order_is_cancelled_after_a_day(
    monkeypatch, gateways_enabled, gateway_payment, product_with_variant_and_stock
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.FAILED, quantity=2)
    _age(payment, 25)
    _install(monkeypatch, _Gateway("failed", {"amount_cents": int(payment.amount.amount * 100)}))

    release_unpaid_orders_task()

    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.CANCELLED


def test_an_abandoned_checkout_is_asked_about_then_expired_and_cancelled(
    monkeypatch, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="mobile_wallet", status=PaymentStatus.REQUIRES_ACTION)
    _age(payment, 25)
    _install(monkeypatch, _Gateway("pending", {"amount_cents": int(payment.amount.amount * 100)}))

    release_unpaid_orders_task()

    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.EXPIRED
    assert payment.order.status == OrderStatus.CANCELLED


def test_an_order_that_turns_out_to_be_paid_is_confirmed_not_cancelled(
    monkeypatch, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _age(payment, 25)
    _install(monkeypatch, _Gateway("captured", {"id": 3001, "amount_cents": int(payment.amount.amount * 100)}))

    release_unpaid_orders_task()

    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.CONFIRMED


def test_a_gateway_that_cannot_answer_leaves_the_order_alone(monkeypatch, gateways_enabled, gateway_payment):
    from apps.payments.providers.http import GatewayError

    class Down(_Gateway):
        def get_status(self, *, payment):
            raise GatewayError("The payment provider did not respond in time.")

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _age(payment, 25)
    _install(monkeypatch, Down("pending"))

    release_unpaid_orders_task()

    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION
    assert payment.order.status == OrderStatus.PENDING


def test_a_recent_unpaid_order_is_left_alone(monkeypatch, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.FAILED)
    _age(payment, 2)
    _install(monkeypatch, _Gateway("failed"))

    release_unpaid_orders_task()

    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.PENDING


def test_instapay_waits_for_staff(monkeypatch, gateways_enabled, gateway_payment):
    """An InstaPay transfer is checked by a person; the shop decides, not a timer."""
    payment = gateway_payment(provider="instapay", method="instapay", status=PaymentStatus.REQUIRES_ACTION)
    _age(payment, 72)

    release_unpaid_orders_task()

    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.PENDING


def test_a_late_success_after_the_auto_cancel_is_kept_and_flagged(
    api_client, monkeypatch, gateways_enabled, gateway_payment
):
    from tests.test_payments_webhooks import paymob_transaction, post_paymob

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.FAILED)
    _age(payment, 25)
    _install(monkeypatch, _Gateway("failed", {"amount_cents": int(payment.amount.amount * 100)}))
    release_unpaid_orders_task()
    monkeypatch.undo()

    with mock.patch("apps.notifications.tasks.send_email_task.delay"):
        post_paymob(api_client, paymob_transaction(payment, id=3101))

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.metadata.get("needs_refund") is True
