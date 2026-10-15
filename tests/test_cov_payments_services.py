"""
Branch coverage for apps.payments.services / tasks / views: the failure paths
that only show up when a gateway misbehaves, plus the staff endpoints' 404s.
No real gateway is contacted; a scripted stand-in replaces the provider.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.orders.models import OrderStatus
from apps.payments import services
from apps.payments.models import (
    Payment,
    PaymentStatus,
    PaymentTransaction,
    PaymentWebhookEvent,
    Refund,
    RefundStatus,
)
from apps.payments.providers.base import CaptureResult, CreatePaymentResult, RefundResult, StatusResult
from apps.payments.providers.http import GatewayError

pytestmark = pytest.mark.django_db


class Fake:
    """A provider whose every answer is scripted by the test."""

    def __init__(self, **kw):
        self.kw = kw

    def _answer(self, name, default):
        value = self.kw.get(name, default)
        if isinstance(value, BaseException):
            raise value
        return value

    def create_payment(self, *, payment, order):
        return self._answer("create", CreatePaymentResult(success=True, status="pending"))

    def capture(self, *, payment, amount=None):
        return self._answer("capture", CaptureResult(success=True))

    def cancel(self, *, payment):
        return self._answer("cancel", True)

    def refund(self, *, payment, amount, reason=""):
        return self._answer("refund", RefundResult(success=True, provider_refund_id="R1", raw={}))

    def get_status(self, *, payment):
        return self._answer("status", StatusResult(status="captured", raw={}))

    def verify_webhook(self, **kw):
        return self._answer("verify", True)

    def parse_webhook_event(self, **kw):
        return self._answer("parse", {})


def install(monkeypatch, fake):
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: fake)
    return fake


# ---------------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------------


def test_redact_shapes():
    assert services._redact(None) == {}
    assert services._redact([{"token": "x"}]) == {"items": [{"token": "***REDACTED***"}]}
    assert services._redact("plain") == {"raw": "plain"}


# ---------------------------------------------------------------------------
# create_payment_for_order
# ---------------------------------------------------------------------------


def test_create_payment_gateway_unreachable_records_failure(monkeypatch, gateway_payment):
    order = gateway_payment(provider="cod", method="cod").order
    install(monkeypatch, Fake(create=GatewayError("timeout")))
    with pytest.raises(ApplicationError) as exc:
        services.create_payment_for_order(order=order, provider_code="paymob", method="card")
    assert exc.value.code == ErrorCodes.PAYMENT_PROVIDER_ERROR


def test_create_payment_refused_by_gateway(monkeypatch, gateway_payment):
    order = gateway_payment(provider="cod", method="cod").order
    install(monkeypatch, Fake(create=CreatePaymentResult(success=False, error="", raw=["x"])))
    with pytest.raises(ApplicationError) as exc:
        services.create_payment_for_order(order=order, provider_code="paymob", method="card")
    assert exc.value.code == ErrorCodes.PAYMENT_FAILED
    assert "could not be initiated" in exc.value.message


def test_create_payment_idempotent_on_key(monkeypatch, gateway_payment):
    order = gateway_payment(provider="cod", method="cod").order
    install(monkeypatch, Fake())
    first = services.create_payment_for_order(order=order, provider_code="paymob", method="card", idempotency_key="k-1")
    again = services.create_payment_for_order(order=order, provider_code="paymob", method="card", idempotency_key="k-1")
    assert first.pk == again.pk


def test_create_payment_rejects_foreign_currency(settings, gateway_payment):
    order = gateway_payment(provider="cod", method="cod").order
    settings.PAYMENT_ALLOWED_CURRENCIES = ["USD"]
    with pytest.raises(ApplicationError) as exc:
        services.create_payment_for_order(order=order, provider_code="cod", method="cod")
    assert exc.value.code == ErrorCodes.PAYMENT_CURRENCY_MISMATCH


# ---------------------------------------------------------------------------
# terminal-state idempotency
# ---------------------------------------------------------------------------


def test_mark_failed_twice_and_cancel_twice_are_noops(monkeypatch, gateway_payment):
    install(monkeypatch, Fake(cancel=NotImplementedError("no void")))
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    services.mark_payment_cancelled(p, reason="customer left")
    p.refresh_from_db()
    assert p.status == PaymentStatus.CANCELLED
    assert services.mark_payment_cancelled(p).status == PaymentStatus.CANCELLED

    q = gateway_payment(provider="paymob", method="card", status=PaymentStatus.FAILED)
    assert services.mark_payment_failed(q).status == PaymentStatus.FAILED


def test_mark_expired_is_idempotent(gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.EXPIRED)
    assert services.mark_payment_expired(p).status == PaymentStatus.EXPIRED


# ---------------------------------------------------------------------------
# manual settlement
# ---------------------------------------------------------------------------


def test_manual_receipt_refuses_gateway_payment(gateway_payment):
    p = gateway_payment(provider="paymob", method="card")
    with pytest.raises(ApplicationError) as exc:
        services.record_manual_receipt(p)
    assert exc.value.code == ErrorCodes.PAYMENT_INVALID_STATE
    with pytest.raises(ApplicationError):
        services.record_manual_refusal(p)


def test_manual_receipt_already_captured_is_noop(gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    assert services.record_manual_receipt(p).status == PaymentStatus.CAPTURED


def test_manual_receipt_capture_failure_raises(monkeypatch, gateway_payment):
    p = gateway_payment(provider="instapay", method="instapay")
    install(monkeypatch, Fake(capture=CaptureResult(success=False)))
    with pytest.raises(ApplicationError) as exc:
        services.record_manual_receipt(p)
    assert exc.value.code == ErrorCodes.PAYMENT_FAILED
    p.refresh_from_db()
    assert p.status == PaymentStatus.PENDING


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_without_inquiry_support_leaves_payment(monkeypatch, gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    install(monkeypatch, Fake(status=NotImplementedError("none")))
    assert services.reconcile_payment(p).status == PaymentStatus.REQUIRES_ACTION


def test_reconcile_unknown_status_is_not_guessed(monkeypatch, gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    install(monkeypatch, Fake(status=StatusResult(status="weird", raw={})))
    assert services.reconcile_payment(p).status == PaymentStatus.REQUIRES_ACTION


@pytest.mark.parametrize("mapped,expected", [("voided", PaymentStatus.CANCELLED), ("authorized", PaymentStatus.AUTHORIZED)])
def test_reconcile_applies_non_capture_states(monkeypatch, gateway_payment, mapped, expected):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    install(monkeypatch, Fake(status=StatusResult(status=mapped, raw={})))
    assert services.reconcile_payment(p).status == expected


def test_apply_mapped_expired(gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    services._apply_mapped_status(p, PaymentStatus.EXPIRED, reason="late")
    p.refresh_from_db()
    assert p.status == PaymentStatus.EXPIRED and p.failure_reason == "late"


def test_amount_parsing_helpers(gateway_payment):
    assert services._amount_from_raw("nope") is None
    assert services._amount_from_raw({"amount_cents": "abc"}) is None
    assert services._amount_from_raw({"orderAmount": "12.5"}) == Decimal("12.50")
    assert services._amount_from_raw({"paymentAmount": "x"}) is None
    assert services._amount_from_raw({}) is None
    assert services._cents_to_amount({"k": "bad"}, "k") is None
    assert services._cents_to_amount(None, "k") is None
    p = gateway_payment(provider="paymob", method="card")
    assert services._amounts_agree(p, None) is True
    assert services._transaction_id_from_raw({"id": ""}) == ""


def _refund_pending(gateway_payment, created_ago=timedelta(0)):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REFUND_PENDING)
    refund = Refund.objects.create(
        payment=p, order=p.order, amount=Money(Decimal("10.00"), "EGP"),
        idempotency_key="r-pending", status=RefundStatus.PENDING,
    )
    if created_ago:
        Refund.objects.filter(pk=refund.pk).update(created_at=timezone.now() - created_ago)
    return p, refund


def test_settle_pending_refund_waits_without_a_refunded_figure(monkeypatch, gateway_payment):
    p, refund = _refund_pending(gateway_payment)
    install(monkeypatch, Fake(status=StatusResult(status="captured", raw={})))
    services.reconcile_payment(p)
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PENDING


def test_settle_pending_refund_refunded_status_means_processed(monkeypatch, gateway_payment):
    p, refund = _refund_pending(gateway_payment)
    install(monkeypatch, Fake(status=StatusResult(status="refunded", raw={})))
    p = services.reconcile_payment(p)
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PROCESSED
    assert p.status == PaymentStatus.PARTIALLY_REFUNDED


def test_settle_pending_refund_too_early_to_call_failed(monkeypatch, gateway_payment):
    p, refund = _refund_pending(gateway_payment)
    install(monkeypatch, Fake(status=StatusResult(status="captured", raw={"refunded_amount_cents": 0})))
    services.reconcile_payment(p)
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PENDING


def test_settle_pending_refund_old_and_absent_is_failed(monkeypatch, gateway_payment):
    p, refund = _refund_pending(gateway_payment, created_ago=timedelta(days=2))
    install(monkeypatch, Fake(status=StatusResult(status="captured", raw={"refunded_amount_cents": 0})))
    p = services.reconcile_payment(p)
    refund.refresh_from_db()
    assert refund.status == RefundStatus.FAILED
    assert p.status == PaymentStatus.CAPTURED


def test_settle_with_no_pending_refunds_is_noop(gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REFUND_PENDING)
    services._settle_pending_refunds(p, {}, PaymentStatus.REFUNDED)
    p.refresh_from_db()
    assert p.status == PaymentStatus.REFUND_PENDING


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def _hook(**parsed):
    return services.process_webhook(provider_code="paymob", headers={}, body=b"{}", query_params={})


def test_webhook_unparseable_payload(monkeypatch):
    install(monkeypatch, Fake(parse=ValueError("bad json")))
    with pytest.raises(ApplicationError) as exc:
        _hook()
    assert exc.value.code == ErrorCodes.PAYMENT_WEBHOOK_INVALID_SIGNATURE


def test_webhook_without_event_id(monkeypatch):
    install(monkeypatch, Fake(parse={"event_id": "txn:None"}))
    with pytest.raises(ApplicationError):
        _hook()


def test_webhook_unreadable_amount_rejected(monkeypatch, gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    install(monkeypatch, Fake(parse={
        "event_id": "e-amt", "provider_reference": p.provider_reference, "amount": "abc", "status": "captured",
    }))
    event = _hook()
    assert event.http_status == 409 and "could not be read" in event.rejection_reason


def test_webhook_unmapped_status_rejected(monkeypatch, gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    install(monkeypatch, Fake(parse={
        "event_id": "e-unk", "provider_reference": p.provider_reference, "status": "mystery",
    }))
    event = _hook()
    assert event.processed is False and "Unmapped" in event.rejection_reason


def test_webhook_without_status_is_processed_noop(monkeypatch, gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    install(monkeypatch, Fake(parse={"event_id": "e-none", "provider_reference": p.provider_reference}))
    event = _hook()
    assert event.processed is True
    p.refresh_from_db()
    assert p.status == PaymentStatus.REQUIRES_ACTION


def test_webhook_matching_nothing(monkeypatch):
    install(monkeypatch, Fake(parse={"event_id": "e-x", "status": "captured"}))
    event = _hook()
    assert event.payment is None and event.http_status == 202
    assert PaymentWebhookEvent.objects.filter(provider_event_id="e-x", processed=False).exists()


# ---------------------------------------------------------------------------
# refunds
# ---------------------------------------------------------------------------


def test_refund_not_supported_for_method(gateway_payment):
    p = gateway_payment(provider="paymob", method="mystery", status=PaymentStatus.CAPTURED)
    with pytest.raises(ApplicationError) as exc:
        services.create_refund(payment=p, amount=Money(Decimal("1.00"), "EGP"))
    assert exc.value.code == ErrorCodes.REFUND_NOT_SUPPORTED


def test_refund_validation_errors(monkeypatch, gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    install(monkeypatch, Fake())
    with pytest.raises(ApplicationError) as exc:
        services.create_refund(payment=p, amount=Money(Decimal("1.00"), "USD"))
    assert exc.value.code == ErrorCodes.PAYMENT_CURRENCY_MISMATCH
    with pytest.raises(ApplicationError) as exc:
        services.create_refund(payment=p, amount=Money(Decimal("0.00"), "EGP"))
    assert exc.value.code == ErrorCodes.VALIDATION_ERROR
    with pytest.raises(ApplicationError) as exc:
        services.create_refund(payment=p, amount=p.amount + Money(1, "EGP"))
    assert exc.value.code == ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT


def test_refund_provider_not_implemented_restores_capture(monkeypatch, gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    install(monkeypatch, Fake(refund=NotImplementedError("manual only")))
    with pytest.raises(ApplicationError) as exc:
        services.create_refund(payment=p, amount=Money(Decimal("5.00"), "EGP"))
    assert exc.value.code == ErrorCodes.PAYMENT_PROVIDER_ERROR
    p.refresh_from_db()
    assert p.status == PaymentStatus.CAPTURED
    assert Refund.objects.get(payment=p).status == RefundStatus.FAILED


def test_refund_declined_result_restores_capture(monkeypatch, gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    install(monkeypatch, Fake(refund=RefundResult(success=False, raw={})))
    refund = services.create_refund(payment=p, amount=Money(Decimal("5.00"), "EGP"))
    assert refund.status == RefundStatus.FAILED
    p.refresh_from_db()
    assert p.status == PaymentStatus.CAPTURED
    assert PaymentTransaction.objects.filter(payment=p, success=False).exists()


def test_full_refund_clears_needs_refund_flag(monkeypatch, gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    Payment.objects.filter(pk=p.pk).update(metadata={"needs_refund": True})
    install(monkeypatch, Fake())
    services.create_refund(payment=p, amount=p.amount, idempotency_key="full-1")
    p.refresh_from_db()
    assert p.status == PaymentStatus.REFUNDED
    assert p.metadata["needs_refund"] is False
    # Same key again: the processed refund is returned, nothing moves twice.
    again = services.create_refund(payment=p, amount=p.amount, idempotency_key="full-1")
    assert Refund.objects.filter(payment=p).count() == 1 and again.status == RefundStatus.PROCESSED


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


def _age(payment, days):
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(days=days))


def test_reconcile_sweep_survives_a_failing_payment(monkeypatch, gateway_payment):
    from apps.payments.tasks import reconcile_pending_payments_task

    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _age(p, 1)
    install(monkeypatch, Fake(status=GatewayError("down")))
    assert reconcile_pending_payments_task() == 0


def test_expire_sweep_logs_and_continues(monkeypatch, gateway_payment):
    from apps.payments.tasks import expire_stale_payments_task

    p = gateway_payment(provider="instapay", method="instapay", status=PaymentStatus.REQUIRES_ACTION)
    _age(p, 60)

    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr("apps.payments.services.mark_payment_expired", boom)
    assert expire_stale_payments_task() == 0


def _old_unpaid_order(gateway_payment):
    p = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    from apps.orders.models import Order

    Order.objects.filter(pk=p.order_id).update(created_at=timezone.now() - timedelta(days=2))
    return p


def test_release_unpaid_orders_cancels_and_expires(monkeypatch, gateway_payment):
    from apps.payments.tasks import release_unpaid_orders_task

    p = _old_unpaid_order(gateway_payment)
    install(monkeypatch, Fake(status=StatusResult(status="pending", raw={})))
    assert release_unpaid_orders_task() == 1
    p.refresh_from_db()
    p.order.refresh_from_db()
    assert p.status == PaymentStatus.EXPIRED
    assert p.order.status == OrderStatus.CANCELLED


def test_release_unpaid_orders_leaves_unknown_gateway_alone(monkeypatch, gateway_payment):
    from apps.payments.tasks import release_unpaid_orders_task

    p = _old_unpaid_order(gateway_payment)
    install(monkeypatch, Fake(status=GatewayError("down")))
    assert release_unpaid_orders_task() == 0
    p.order.refresh_from_db()
    assert p.order.status == OrderStatus.PENDING


def test_release_unpaid_orders_confirms_paid_one(monkeypatch, gateway_payment):
    from apps.payments.tasks import release_unpaid_orders_task

    p = _old_unpaid_order(gateway_payment)
    install(monkeypatch, Fake(status=StatusResult(status="captured", raw={"amount_cents": int(p.amount.amount * 100)})))
    assert release_unpaid_orders_task() == 0
    p.order.refresh_from_db()
    assert p.order.status == OrderStatus.CONFIRMED


def test_release_unpaid_orders_survives_exceptions(monkeypatch, gateway_payment):
    from apps.payments.tasks import release_unpaid_orders_task

    _old_unpaid_order(gateway_payment)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr("apps.payments.services.reconcile_payment", boom)
    assert release_unpaid_orders_task() == 0


# ---------------------------------------------------------------------------
# staff views
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["refund/", "reconcile/", "settle/"])
def test_staff_payment_endpoints_404(staff_client, suffix):
    response = staff_client.post(
        f"/api/v1/payments/999999/{suffix}", {"amount": "1.00", "outcome": "received"},
        format="json", HTTP_IDEMPOTENCY_KEY="k-404-" + suffix,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("suffix", ["refund/", "reconcile/", "settle/"])
def test_staff_payment_endpoints_forbidden_for_customers(authenticated_client, gateway_payment, suffix):
    p = gateway_payment(provider="cod", method="cod")
    response = authenticated_client.post(
        f"/api/v1/payments/{p.pk}/{suffix}", {"amount": "1.00", "outcome": "received"},
        format="json", HTTP_IDEMPOTENCY_KEY="k-403-" + suffix,
    )
    assert response.status_code == 403


def test_staff_reconcile_and_settle(staff_client, gateway_payment):
    p = gateway_payment(provider="instapay", method="instapay")
    r = staff_client.post(f"/api/v1/payments/{p.pk}/reconcile/", format="json")
    assert r.status_code == 200
    r = staff_client.post(f"/api/v1/payments/{p.pk}/settle/", {"outcome": "refused", "note": "no transfer"}, format="json")
    assert r.status_code == 200, r.content
    p.refresh_from_db()
    assert p.status == PaymentStatus.FAILED


def test_staff_refund_endpoint(staff_client, gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    r = staff_client.post(
        f"/api/v1/payments/{p.pk}/refund/", {"amount": "5.00", "reason": "jar cracked"},
        format="json", HTTP_IDEMPOTENCY_KEY="refund-view-1",
    )
    assert r.status_code == 201, r.content
    assert r.json()["data"]["status"] in (RefundStatus.PROCESSED, RefundStatus.PENDING)
