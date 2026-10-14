"""
Money paths hit at the same instant.

Paymob retries a callback it thinks we missed while the first delivery is still
being processed; the reconciliation sweep asks about a payment in the same
second its callback lands; a member of staff double-clicks refund; a customer
cancels while the "paid" callback is mid-flight. Each of these needs real
threads and committed rows (`transaction=True`), because the row locks are the
thing under test.
"""
from unittest import mock

import pytest

from apps.orders.models import OrderStatus
from apps.payments.models import (
    Payment,
    PaymentStatus,
    PaymentTransaction,
    PaymentTransactionType,
    PaymentWebhookEvent,
    Refund,
    RefundStatus,
)
from apps.payments.providers.base import RefundResult, StatusResult
from tests.test_checkout_concurrency import run_parallel
from tests.test_payments_webhooks import paymob_transaction, post_paymob

pytestmark = pytest.mark.django_db(transaction=True)


def _client():
    from rest_framework.test import APIClient

    return APIClient()


class _Gateway:
    def __init__(self, status="captured", raw=None):
        self.status, self.raw = status, raw or {}
        self.refund_calls = 0

    def get_status(self, *, payment):
        return StatusResult(status=self.status, raw=self.raw)

    def refund(self, *, payment, amount, reason=""):
        self.refund_calls += 1
        return RefundResult(success=True, provider_refund_id=f"R-{self.refund_calls}", raw={})

    def cancel(self, *, payment):
        return True


def _paymob_answering(status, raw):
    """The real Paymob adapter (so callbacks are signature-checked) whose status inquiry is canned."""
    from apps.payments.providers.paymob import PaymobPaymentProvider

    class Hybrid(PaymobPaymentProvider):
        def get_status(self, *, payment):
            return StatusResult(status=status, raw=raw)

    return lambda code: Hybrid()


def _captures(payment):
    return PaymentTransaction.objects.filter(
        payment=payment, transaction_type=PaymentTransactionType.CAPTURE
    ).count()


def test_the_same_callback_delivered_five_times_at_once_confirms_once(gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment, id=2001)

    with mock.patch("apps.notifications.tasks.send_email_task.delay") as send:
        results, errors = run_parallel(lambda i: post_paymob(_client(), transaction).status_code, n=5)

    assert not errors
    assert all(code == 200 for code in results), results
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert PaymentWebhookEvent.objects.filter(provider_event_id="paymob:2001").count() == 1
    assert _captures(payment) == 1
    templates = [c.kwargs.get("template_name") for c in send.call_args_list]
    assert templates.count("payment_confirmation") == 1


def test_callback_and_sweep_racing_capture_once(monkeypatch, gateways_enabled, gateway_payment):
    from apps.payments.services import reconcile_payment

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    cents = int(payment.amount.amount * 100)
    monkeypatch.setattr(
        "apps.payments.services.get_payment_provider",
        _paymob_answering("captured", {"id": 2101, "amount_cents": cents}),
    )

    def act(i):
        if i == 0:
            return post_paymob(_client(), paymob_transaction(payment, id=2101)).status_code
        return reconcile_payment(Payment.objects.get(pk=payment.pk)).status

    results, errors = run_parallel(act, n=2)

    assert not errors, errors
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert _captures(payment) == 1


def test_two_refund_clicks_at_once_pay_out_once(monkeypatch, gateways_enabled, gateway_payment):
    from djmoney.money import Money

    from apps.payments.services import create_refund

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.CAPTURED)
    gateway = _Gateway()
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: gateway)

    def click(i):
        # Different keys: two browser tabs, not one retried request.
        return create_refund(
            payment=Payment.objects.get(pk=payment.pk),
            amount=Money(payment.amount.amount, payment.amount.currency),
            reason="customer",
            idempotency_key=f"tab-{i}",
        ).status

    results, errors = run_parallel(click, n=2)

    assert gateway.refund_calls == 1
    assert results == [RefundStatus.PROCESSED]
    assert len(errors) == 1, errors
    assert Refund.objects.filter(payment=payment, status=RefundStatus.PROCESSED).count() == 1


def test_cancel_racing_the_paid_callback_never_loses_the_money(
    monkeypatch, gateways_enabled, gateway_payment, user
):
    """
    Whichever wins, the end state is consistent: either the order is confirmed
    and the cancel was refused, or the order is cancelled and the payment is
    flagged for a refund. Never a cancelled order holding unflagged money.
    """
    from rest_framework_simplejwt.tokens import AccessToken

    payment = gateway_payment(provider="paymob", method="mobile_wallet", status=PaymentStatus.REQUIRES_ACTION)
    order_number = payment.order.order_number
    # The cancel asks the gateway first; it answers "nothing yet", which is
    # exactly the moment the race is about. The callback is checked for real.
    monkeypatch.setattr(
        "apps.payments.services.get_payment_provider",
        _paymob_answering("pending", {"amount_cents": int(payment.amount.amount * 100)}),
    )

    def act(i):
        client = _client()
        if i == 0:
            return post_paymob(client, paymob_transaction(payment, id=2201)).status_code
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client.post(
            f"/api/v1/orders/{order_number}/cancel/", {"reason": "changed my mind"}, format="json"
        ).status_code

    results, errors = run_parallel(act, n=2)

    assert not errors, errors
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    if payment.order.status == OrderStatus.CANCELLED:
        assert payment.metadata.get("needs_refund") is True
    else:
        assert payment.order.status == OrderStatus.CONFIRMED
