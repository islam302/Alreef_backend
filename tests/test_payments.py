"""
The payment layer's own rules: COD, the state machine, refunds, and the webhook
checks that do not depend on which gateway sent the event.

Per-gateway signature and contract tests live in test_payments_providers.py;
the full webhook matrix lives in test_payments_webhooks.py.
"""
import uuid

import pytest
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.payments import state as payment_state
from apps.payments.models import Payment, PaymentStatus, RefundStatus
from apps.payments.providers.cod import CODPaymentProvider
from apps.payments.services import (
    create_refund,
    mark_payment_captured,
    mark_payment_failed,
    process_webhook,
    record_cod_collection,
    record_cod_refusal,
)

pytestmark = pytest.mark.django_db

CART_ITEMS_URL = "/api/v1/cart/items/"
CHECKOUT_URL = "/api/v1/checkout/"


# ---------------------------------------------------------------------------
# The COD adapter in isolation
# ---------------------------------------------------------------------------


def test_cod_provider_create_payment_succeeds_without_network_call():
    provider = CODPaymentProvider()
    payment = Payment(provider="cod", method="cod", amount=Money("100.00", "EGP"))

    result = provider.create_payment(payment=payment, order=None)

    assert result.success is True
    assert result.provider_reference.startswith("COD-")
    # Pending, not captured: no cash has been collected at checkout time.
    assert result.status == "pending"


def test_cod_provider_capture_succeeds():
    provider = CODPaymentProvider()
    payment = Payment(
        provider="cod", method="cod", amount=Money("100.00", "EGP"), provider_reference="COD-ABC123"
    )

    result = provider.capture(payment=payment)

    assert result.success is True
    assert result.provider_transaction_id == "COD-ABC123"


def test_cod_provider_refund_is_recorded_as_manual():
    """
    A COD refund is a person handing cash back. The adapter records it and says
    so in the reference, so reconciliation can tell it apart from a gateway
    refund that moved money by itself.
    """
    provider = CODPaymentProvider()
    payment = Payment(provider="cod", method="cod", amount=Money("100.00", "EGP"))

    result = provider.refund(payment=payment, amount=Money("50.00", "EGP"), reason="damaged")

    assert result.success is True
    assert result.provider_refund_id.startswith("MANUAL-COD-")


def test_cod_provider_verify_webhook_always_false():
    """COD has no webhooks, so every POST to its webhook URL must be refused."""
    provider = CODPaymentProvider()
    assert provider.verify_webhook(headers={}, body=b"", query_params={}) is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2):
    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    authenticated_client.post(
        CART_ITEMS_URL, {"variant_id": variant.id, "quantity": quantity}, format="json"
    )
    response = authenticated_client.post(
        CHECKOUT_URL,
        {
            "address_id": address.id,
            "shipping_method_id": method.id,
            "payment_method": "cod",
            "coupon_code": "",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201
    return Payment.objects.get(pk=response.json()["data"]["payment"]["id"])


def _collected_cod_payment(*args, **kwargs):
    """A COD payment whose cash has been collected — i.e. actually refundable."""
    payment = _place_cod_order(*args, **kwargs)
    record_cod_collection(payment)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    return payment


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def test_captured_payment_cannot_be_walked_back_to_failed(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The single most valuable rule here. A replayed `failed` event arriving after
    a `captured` one must not lose the fact that we hold the customer's money —
    `mark_payment_failed` used to overwrite the status unconditionally.
    """
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    with pytest.raises(ApplicationError) as exc_info:
        mark_payment_failed(payment, error_message="late failure event")

    assert exc_info.value.code == ErrorCodes.PAYMENT_INVALID_STATE
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_capturing_twice_is_a_no_op(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    capture_count = payment.transactions.filter(transaction_type="capture").count()

    mark_payment_captured(payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    # No second capture transaction: the order is not confirmed twice either.
    assert payment.transactions.filter(transaction_type="capture").count() == capture_count


def test_state_machine_rejects_every_move_out_of_a_terminal_state():
    for terminal in payment_state.TERMINAL:
        for target in PaymentStatus.values:
            if target == terminal:
                continue
            assert not payment_state.can_transition(terminal, target), (
                f"{terminal} -> {target} must not be allowed"
            )


def test_same_state_transition_is_always_allowed():
    """What makes a duplicated webhook harmless rather than an error."""
    for status in PaymentStatus.values:
        payment_state.assert_transition(status, status)


# ---------------------------------------------------------------------------
# COD collection and refusal
# ---------------------------------------------------------------------------


def test_cod_collection_captures_and_confirms(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _place_cod_order(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    assert payment.status == PaymentStatus.PENDING

    record_cod_collection(payment, note="collected at door")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_cod_refusal_fails_the_payment(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _place_cod_order(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    record_cod_refusal(payment, reason="customer not home, refused")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    assert "refused" in payment.failure_reason


def test_a_gateway_payment_cannot_be_settled_by_hand(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    Only cash on delivery and InstaPay are settled by a person. Letting staff hand-
    confirm a card payment would be a way to mark an order paid that the gateway
    never captured.
    """
    payment = _place_cod_order(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    Payment.objects.filter(pk=payment.pk).update(provider="paymob")
    payment.refresh_from_db()

    with pytest.raises(ApplicationError) as exc_info:
        record_cod_collection(payment)

    assert exc_info.value.code == ErrorCodes.PAYMENT_INVALID_STATE


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


def test_refund_is_refused_while_no_money_has_been_collected(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    A pending COD order holds no money. Refunding it would pay out cash the
    courier never collected — which the old code permitted, because COD payments
    were marked captured the moment the order was placed.
    """
    payment = _place_cod_order(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    assert payment.status == PaymentStatus.PENDING

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=Money("10.00", payment.amount_currency))

    assert exc_info.value.code == ErrorCodes.PAYMENT_INVALID_STATE
    assert payment.refunds.count() == 0


def test_create_refund_caps_at_payment_amount(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2
    )

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=payment.amount + Money("1.00", payment.amount_currency))

    assert exc_info.value.code == ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT


def test_refunds_cannot_be_stacked_past_the_captured_amount(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """Two refunds that are each individually valid must not together exceed."""
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2
    )
    half = Money(payment.amount.amount / 2, payment.amount_currency)

    create_refund(payment=payment, amount=half, reason="first half")
    payment.refresh_from_db()

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(
            payment=payment,
            amount=half + Money("1.00", payment.amount_currency),
            reason="more than the rest",
        )

    assert exc_info.value.code == ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT


def test_create_refund_rejects_zero_and_negative_amounts(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    for bad in ("0.00", "-5.00"):
        with pytest.raises(ApplicationError) as exc_info:
            create_refund(payment=payment, amount=Money(bad, payment.amount_currency))
        assert exc_info.value.code == ErrorCodes.VALIDATION_ERROR


def test_create_refund_rejects_a_different_currency(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=Money("10.00", "USD"))

    assert exc_info.value.code == ErrorCodes.PAYMENT_CURRENCY_MISMATCH


def test_create_refund_partial_amount_succeeds(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2
    )
    partial_amount = Money(payment.amount.amount / 2, payment.amount_currency)

    refund = create_refund(payment=payment, amount=partial_amount, reason="one item damaged")

    assert refund.status == RefundStatus.PROCESSED
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_create_refund_full_amount_marks_payment_refunded(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1
    )

    refund = create_refund(payment=payment, amount=payment.amount, reason="full return")

    assert refund.status == RefundStatus.PROCESSED
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED


def test_a_fully_refunded_payment_cannot_be_refunded_again(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1
    )
    create_refund(payment=payment, amount=payment.amount, reason="full return")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=Money("1.00", payment.amount_currency))

    assert exc_info.value.code == ErrorCodes.PAYMENT_INVALID_STATE


def test_create_refund_is_idempotent_with_same_key(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1
    )
    key = str(uuid.uuid4())

    first = create_refund(
        payment=payment, amount=Money("10.00", payment.amount_currency), idempotency_key=key
    )
    second = create_refund(
        payment=payment, amount=Money("10.00", payment.amount_currency), idempotency_key=key
    )

    assert first.id == second.id
    assert payment.refunds.count() == 1


def test_refund_is_audited(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    from apps.audit.models import AuditAction, AuditLog

    payment = _collected_cod_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    create_refund(payment=payment, amount=Money("10.00", payment.amount_currency), reason="goodwill")

    assert AuditLog.objects.filter(
        action=AuditAction.REFUND, model_name="Payment", object_id=str(payment.id)
    ).exists()


# ---------------------------------------------------------------------------
# Provider-agnostic webhook checks
# ---------------------------------------------------------------------------


class _FakeSignedProvider:
    """
    Stands in for a gateway adapter whose signature always verifies.

    The point of these tests is what `process_webhook` does with a *validly
    signed* event whose contents disagree with our record — which is precisely
    the situation a compromised or confused provider, or a misrouted event,
    produces. The signature is not the thing under test here.
    """

    code = "fake"

    def __init__(self, **event):
        self._event = event

    def verify_webhook(self, *, headers, body, query_params):
        return True

    def parse_webhook_event(self, *, headers, body, query_params):
        return {
            "event_type": "payment.captured",
            "status": "captured",
            "payload": {},
            **self._event,
        }


@pytest.fixture
def fake_gateway(monkeypatch):
    """Registers a 'fake' gateway with a status mapping, and returns a factory."""
    monkeypatch.setitem(
        payment_state.PROVIDER_STATUS_MAP,
        "fake",
        {"captured": PaymentStatus.CAPTURED, "failed": PaymentStatus.FAILED, "noop": None},
    )

    def install(**event):
        provider = _FakeSignedProvider(**event)
        monkeypatch.setattr(
            "apps.payments.services.get_payment_provider", lambda code: provider
        )
        return provider

    return install


def _fake_payment(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    """A COD order relabelled as the fake gateway, so webhook matching applies."""
    payment = _place_cod_order(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1
    )
    Payment.objects.filter(pk=payment.pk).update(provider="fake")
    payment.refresh_from_db()
    return payment


def test_process_webhook_rejects_amount_mismatch(
    fake_gateway, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _fake_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    fake_gateway(
        event_id="evt-mismatch-1",
        provider_reference=payment.provider_reference,
        merchant_reference=payment.merchant_reference,
        amount="1.00",
    )

    event = process_webhook(provider_code="fake", headers={}, body=b"{}", query_params={})

    # Recorded, refused, and surfaced as a 409 by the view. It does not raise:
    # raising inside the transaction would roll back the record of the incident,
    # which is the one thing worth keeping about it.
    assert event.processed is False
    assert "amount" in event.rejection_reason.lower()
    assert event.http_status == 409
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


def test_process_webhook_accepts_matching_amount(
    fake_gateway, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _fake_payment(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    fake_gateway(
        event_id="evt-match-1",
        provider_reference=payment.provider_reference,
        merchant_reference=payment.merchant_reference,
        amount=str(payment.amount.amount),
    )

    event = process_webhook(provider_code="fake", headers={}, body=b"{}", query_params={})

    assert event.processed is True
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
