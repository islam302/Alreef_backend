"""
When the gateway is slow, late, or says two things.

Every test here is a way money can be taken without the order knowing, or given
back twice, because an answer arrived late or not at all:

* a card is declined, the customer tries another card on the same Paymob page,
  and the success lands after we already wrote the payment off as failed;
* a success callback arrives after the order was cancelled, or days late;
* a refund call times out after Paymob has already refunded;
* a customer cancels an order while their payment is still in flight, then pays
  again on a new order.

The rule in every case: a verified statement from the gateway that money moved
is never thrown away, and nothing we do on our side can move money a second time
while the first attempt's outcome is unknown.
"""
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.orders.models import OrderStatus
from apps.payments.models import Payment, PaymentStatus, PaymentWebhookEvent, Refund, RefundStatus
from apps.payments.providers.base import RefundResult, StatusResult
from apps.payments.providers.http import GatewayError
from apps.payments.services import create_refund, reconcile_payment
from apps.payments.tasks import reconcile_pending_payments_task
from tests.test_payments_webhooks import paymob_transaction, post_paymob

pytestmark = pytest.mark.django_db


class _Gateway:
    """A Paymob stand-in: answers the status inquiry and the refund call as told."""

    def __init__(self, *, status="captured", raw=None, refund=None):
        self.status, self.raw, self._refund = status, raw or {}, refund
        self.refund_calls = 0

    def get_status(self, *, payment):
        return StatusResult(status=self.status, raw=self.raw)

    def refund(self, *, payment, amount, reason=""):
        self.refund_calls += 1
        if isinstance(self._refund, Exception):
            raise self._refund
        return self._refund or RefundResult(success=True, provider_refund_id="R-1", raw={})

    def cancel(self, *, payment):
        return True


def _install(monkeypatch, gateway):
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: gateway)
    return gateway


def _cents(payment):
    return int(payment.amount.amount * 100)


def _captured_paymob(gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.CAPTURED)
    return payment


# ---------------------------------------------------------------------------
# A success that arrives after we gave up on the payment
# ---------------------------------------------------------------------------


def test_success_after_a_declined_card_is_recorded(api_client, gateways_enabled, gateway_payment):
    """
    Paymob lets a customer retry on the same checkout page. The decline arrives
    first and fails the payment; the retry succeeds with a new transaction id.
    That success used to be rejected as "out of order", so the customer was
    charged and the order never confirmed.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    declined = paymob_transaction(payment, id=301, success=False)
    assert post_paymob(api_client, declined).status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED

    paid = paymob_transaction(payment, id=302)
    assert post_paymob(api_client, paid).status_code == 200

    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == OrderStatus.CONFIRMED
    # Refunds and voids are made against the transaction, not the intention.
    assert payment.provider_reference == "302"


def test_success_after_expiry_is_recorded(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.EXPIRED)

    assert post_paymob(api_client, paymob_transaction(payment, id=401)).status_code == 200

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_a_late_failure_still_cannot_undo_a_success(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    post_paymob(api_client, paymob_transaction(payment, id=501))
    post_paymob(api_client, paymob_transaction(payment, id=500, success=False))

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_an_old_success_callback_is_still_applied(api_client, gateways_enabled, gateway_payment):
    """
    Old events are dropped to stop stale noise, but a signed "paid" is evidence
    that we hold the customer's money however late it comes.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    old = (timezone.now() - timedelta(days=5)).isoformat()

    assert post_paymob(api_client, paymob_transaction(payment, id=601, created_at=old)).status_code == 200

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_an_old_failure_callback_is_still_ignored(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    old = (timezone.now() - timedelta(days=5)).isoformat()

    post_paymob(api_client, paymob_transaction(payment, id=602, created_at=old, success=False))

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_payment_for_a_cancelled_order_is_kept_and_flagged_for_refund(
    api_client, staff_client, gateways_enabled, gateway_payment
):
    """
    The order was cancelled while the callback was delayed. The money is real:
    the payment must say so and be marked for a refund, and the customer must not
    be told their cancelled order is confirmed.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    from apps.orders.services import cancel_order

    cancel_order(payment.order, reason="staff cancelled")
    payment.refresh_from_db()

    with mock.patch("apps.notifications.tasks.send_email_task.delay") as send:
        assert post_paymob(api_client, paymob_transaction(payment, id=701)).status_code == 200

    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == OrderStatus.CANCELLED
    assert payment.metadata.get("needs_refund") is True
    templates = [c.kwargs.get("template_name") for c in send.call_args_list]
    assert "order_confirmation" not in templates


def test_a_second_successful_charge_is_flagged_not_swallowed(api_client, gateways_enabled, gateway_payment):
    """
    Two successful transactions on one payment means the customer paid twice
    (two tabs on the same checkout). The second must be recorded for a refund,
    not silently counted as the same payment.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    post_paymob(api_client, paymob_transaction(payment, id=801))
    response = post_paymob(api_client, paymob_transaction(payment, id=802))

    assert response.status_code == 409
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.provider_reference == "801"
    assert payment.metadata.get("duplicate_charges") == ["802"]
    event = PaymentWebhookEvent.objects.get(provider_event_id="paymob:802")
    assert "refund" in event.rejection_reason.lower()


def test_the_same_success_delivered_twice_is_not_a_second_charge(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment, id=901)
    with mock.patch("apps.notifications.tasks.send_email_task.delay") as send:
        assert post_paymob(api_client, transaction).status_code == 200
        assert post_paymob(api_client, transaction).status_code == 200

    payment.refresh_from_db()
    assert payment.metadata.get("duplicate_charges") in (None, [])
    templates = [c.kwargs.get("template_name") for c in send.call_args_list]
    assert templates.count("payment_confirmation") == 1


# ---------------------------------------------------------------------------
# Reconciliation finds what the callbacks missed
# ---------------------------------------------------------------------------


def test_sweep_finds_a_success_hidden_behind_a_failed_payment(monkeypatch, gateways_enabled, gateway_payment):
    """The success callback was lost after the decline arrived. The sweep must still ask."""
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.FAILED)
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(minutes=30))
    _install(monkeypatch, _Gateway(status="captured", raw={"id": 1001, "amount_cents": _cents(payment)}))

    reconcile_pending_payments_task()

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.provider_reference == "1001"


def test_reconciliation_will_not_confirm_payment_without_an_amount(monkeypatch, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _install(monkeypatch, _Gateway(status="captured", raw={}))

    with pytest.raises(ApplicationError):
        reconcile_payment(payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


# ---------------------------------------------------------------------------
# Refunds whose answer never came back
# ---------------------------------------------------------------------------


def test_a_refund_that_times_out_is_kept_pending_and_blocks_another(
    monkeypatch, gateways_enabled, gateway_payment
):
    """
    Paymob may have refunded and the reply was lost. Rolling the refund back let
    staff press refund again — a second payout. It now stays pending and nothing
    else can be refunded until we know.
    """
    payment = _captured_paymob(gateway_payment)
    gateway = _install(monkeypatch, _Gateway(refund=GatewayError("The payment provider did not respond in time.")))

    refund = create_refund(payment=payment, amount=payment.amount, reason="customer", idempotency_key="k1")

    assert refund.status == RefundStatus.PENDING
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUND_PENDING

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=payment.amount, reason="again", idempotency_key="k2")
    assert exc_info.value.code == ErrorCodes.PAYMENT_INVALID_STATE
    assert gateway.refund_calls == 1
    assert Refund.objects.filter(payment=payment).count() == 1


def test_a_refused_refund_is_recorded_and_can_be_retried(monkeypatch, gateways_enabled, gateway_payment):
    payment = _captured_paymob(gateway_payment)
    _install(
        monkeypatch,
        _Gateway(refund=GatewayError("The payment provider rejected the request.", status_code=400)),
    )

    with pytest.raises(ApplicationError):
        create_refund(payment=payment, amount=payment.amount, reason="customer", idempotency_key="same")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert Refund.objects.get(payment=payment).status == RefundStatus.FAILED

    _install(monkeypatch, _Gateway())
    refund = create_refund(payment=payment, amount=payment.amount, reason="customer", idempotency_key="same")
    assert refund.status == RefundStatus.PROCESSED


def test_double_clicked_refund_pays_out_once(monkeypatch, gateways_enabled, gateway_payment):
    payment = _captured_paymob(gateway_payment)
    gateway = _install(monkeypatch, _Gateway())

    first = create_refund(payment=payment, amount=payment.amount, reason="x", idempotency_key="dbl")
    second = create_refund(payment=payment, amount=payment.amount, reason="x", idempotency_key="dbl")

    assert first.pk == second.pk
    assert gateway.refund_calls == 1


def _pending_refund(monkeypatch, gateway_payment, *, payment=None, amount=None, age=timedelta(0)):
    payment = payment or _captured_paymob(gateway_payment)
    _install(monkeypatch, _Gateway(refund=GatewayError("The payment provider did not respond in time.")))
    refund = create_refund(payment=payment, amount=amount or payment.amount, reason="r", idempotency_key="p")
    Refund.objects.filter(pk=refund.pk).update(created_at=timezone.now() - age)
    payment.refresh_from_db()
    return payment, refund


def test_reconciliation_completes_a_refund_paymob_did_make(monkeypatch, gateways_enabled, gateway_payment):
    payment, refund = _pending_refund(monkeypatch, gateway_payment)
    _install(monkeypatch, _Gateway(status="refunded", raw={"amount_cents": _cents(payment), "is_refunded": True,
                                                         "refunded_amount_cents": _cents(payment)}))

    reconcile_payment(payment)

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PROCESSED
    assert payment.status == PaymentStatus.REFUNDED


def test_reconciliation_completes_a_partial_refund(monkeypatch, gateways_enabled, gateway_payment):
    payment = _captured_paymob(gateway_payment)
    half = Money(payment.amount.amount / 2, payment.amount.currency)
    payment, refund = _pending_refund(monkeypatch, gateway_payment, payment=payment, amount=half)
    _install(monkeypatch, _Gateway(status="captured", raw={"amount_cents": _cents(payment),
                                                         "refunded_amount_cents": int(half.amount * 100)}))

    reconcile_payment(payment)

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PROCESSED
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_a_recent_unconfirmed_refund_stays_pending(monkeypatch, gateways_enabled, gateway_payment):
    """Paymob shows no refund yet, but it is minutes old: it may still land. Wait."""
    payment, refund = _pending_refund(monkeypatch, gateway_payment)
    _install(monkeypatch, _Gateway(status="captured", raw={"amount_cents": _cents(payment),
                                                         "refunded_amount_cents": 0}))

    reconcile_payment(payment)

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PENDING
    assert payment.status == PaymentStatus.REFUND_PENDING


def test_a_refund_paymob_never_made_is_released_after_a_day(monkeypatch, gateways_enabled, gateway_payment):
    payment, refund = _pending_refund(monkeypatch, gateway_payment, age=timedelta(hours=25))
    _install(monkeypatch, _Gateway(status="captured", raw={"amount_cents": _cents(payment),
                                                         "refunded_amount_cents": 0}))

    reconcile_payment(payment)

    payment.refresh_from_db()
    refund.refresh_from_db()
    assert refund.status == RefundStatus.FAILED
    assert payment.status == PaymentStatus.CAPTURED


# ---------------------------------------------------------------------------
# A customer cancelling while the payment is in flight
# ---------------------------------------------------------------------------


def _cancel(client, order):
    return client.post(f"/api/v1/orders/{order.order_number}/cancel/", {"reason": "changed my mind"}, format="json")


def test_customer_cannot_cancel_while_payment_is_in_flight(
    monkeypatch, authenticated_client, gateways_enabled, gateway_payment
):
    """
    Cancel-then-pay-again is how a slow wallet confirmation becomes two charges.
    While the gateway has not answered, the order stays.
    """
    payment = gateway_payment(provider="paymob", method="mobile_wallet", status=PaymentStatus.REQUIRES_ACTION)
    _install(monkeypatch, _Gateway(status="pending", raw={"amount_cents": _cents(payment)}))

    response = _cancel(authenticated_client, payment.order)

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == "ORDER_PAYMENT_IN_PROGRESS"
    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.PENDING


def test_cancel_asks_the_gateway_first_and_finds_the_payment(
    monkeypatch, authenticated_client, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _install(monkeypatch, _Gateway(status="captured", raw={"id": 1201, "amount_cents": _cents(payment)}))

    response = _cancel(authenticated_client, payment.order)

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == "ORDER_ALREADY_PAID"
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == OrderStatus.CONFIRMED


def test_customer_can_cancel_once_the_payment_has_failed(
    monkeypatch, authenticated_client, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.FAILED)
    _install(monkeypatch, _Gateway(status="failed", raw={"amount_cents": _cents(payment)}))

    response = _cancel(authenticated_client, payment.order)

    assert response.status_code == 200
    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.CANCELLED


def test_customer_can_still_cancel_a_cash_on_delivery_order(authenticated_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="cod", method="cod")

    response = _cancel(authenticated_client, payment.order)

    assert response.status_code == 200


def test_staff_cancelling_a_paid_order_flags_the_refund(monkeypatch, gateways_enabled, gateway_payment):
    from apps.orders.services import cancel_order

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    from apps.payments.services import mark_payment_captured

    mark_payment_captured(payment, from_gateway=True, transaction_reference="1301")
    payment.order.refresh_from_db()
    cancel_order(payment.order, reason="out of stock")

    payment.refresh_from_db()
    assert payment.metadata.get("needs_refund") is True

    _install(monkeypatch, _Gateway())
    create_refund(payment=payment, amount=payment.amount, reason="cancelled", idempotency_key="c")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED
    assert payment.metadata.get("needs_refund") is False
