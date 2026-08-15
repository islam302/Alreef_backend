import uuid

import pytest
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.payments.models import Payment, PaymentStatus, RefundStatus
from apps.payments.providers.cod import CODPaymentProvider
from apps.payments.services import create_refund, process_webhook

pytestmark = pytest.mark.django_db

CART_ITEMS_URL = "/api/v1/cart/items/"
CHECKOUT_URL = "/api/v1/checkout/"


def test_cod_provider_create_payment_succeeds_without_network_call():
    provider = CODPaymentProvider()
    payment = Payment(provider="cod", method="cod", amount=Money("100.00", "EGP"))

    result = provider.create_payment(payment=payment, order=None)

    assert result.success is True
    assert result.provider_reference.startswith("COD-")
    assert result.status == "pending"


def test_cod_provider_capture_succeeds():
    provider = CODPaymentProvider()
    payment = Payment(provider="cod", method="cod", amount=Money("100.00", "EGP"), provider_reference="COD-ABC123")

    result = provider.capture(payment=payment)

    assert result.success is True
    assert result.provider_transaction_id == "COD-ABC123"


def test_cod_provider_refund_succeeds():
    provider = CODPaymentProvider()
    payment = Payment(provider="cod", method="cod", amount=Money("100.00", "EGP"))

    result = provider.refund(payment=payment, amount=Money("50.00", "EGP"), reason="damaged")

    assert result.success is True
    assert result.provider_refund_id.startswith("COD-REFUND-")


def test_cod_provider_verify_webhook_always_false():
    provider = CODPaymentProvider()
    assert provider.verify_webhook(headers={}, body=b"", query_params={}) is False


def _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2):
    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": quantity}, format="json")
    response = authenticated_client.post(
        CHECKOUT_URL,
        {
            "address_id": address.id, "shipping_method_id": method.id,
            "payment_method": "cod", "coupon_code": "",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201
    payment_id = response.json()["data"]["payment"]["id"]
    return Payment.objects.get(pk=payment_id)


def test_create_refund_caps_at_payment_amount(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    payment = _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2)
    assert payment.status == PaymentStatus.CAPTURED

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=payment.amount + Money("1.00", payment.amount_currency))

    assert exc_info.value.code == ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT


def test_create_refund_partial_amount_succeeds(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    payment = _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=2)
    partial_amount = Money(payment.amount.amount / 2, payment.amount_currency)

    refund = create_refund(payment=payment, amount=partial_amount, reason="one item damaged")

    assert refund.status == RefundStatus.PROCESSED
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PARTIALLY_REFUNDED


def test_create_refund_full_amount_marks_payment_refunded(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    payment = _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1)

    refund = create_refund(payment=payment, amount=payment.amount, reason="full return")

    assert refund.status == RefundStatus.PROCESSED
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED


def test_create_refund_is_idempotent_with_same_key(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    payment = _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1)
    key = str(uuid.uuid4())

    first = create_refund(payment=payment, amount=Money("10.00", payment.amount_currency), idempotency_key=key)
    second = create_refund(payment=payment, amount=Money("10.00", payment.amount_currency), idempotency_key=key)

    assert first.id == second.id


class _FakeSignedProvider:
    """Stands in for a real gateway adapter: signature always verifies, and
    the webhook payload reports its own (attacker-controlled-in-a-real-attack)
    amount alongside a provider_reference."""

    code = "fake"

    def __init__(self, *, provider_reference: str, amount, event_id: str):
        self._provider_reference = provider_reference
        self._amount = amount
        self._event_id = event_id

    def verify_webhook(self, *, headers, body, query_params):
        return True

    def parse_webhook_event(self, *, headers, body, query_params):
        return {
            "event_id": self._event_id,
            "event_type": "payment.captured",
            "provider_reference": self._provider_reference,
            "status": "captured",
            "payload": {},
            "amount": self._amount,
        }


def test_process_webhook_rejects_amount_mismatch(
    monkeypatch, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    payment = _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1)
    fake_provider = _FakeSignedProvider(
        provider_reference=payment.provider_reference, amount="1.00", event_id="evt-mismatch-1",
    )
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: fake_provider)

    with pytest.raises(ApplicationError) as exc_info:
        process_webhook(provider_code="fake", headers={}, body=b"{}", query_params={})

    assert exc_info.value.code == ErrorCodes.PAYMENT_AMOUNT_MISMATCH


def test_process_webhook_accepts_matching_amount(
    monkeypatch, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    payment = _place_cod_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup, quantity=1)
    fake_provider = _FakeSignedProvider(
        provider_reference=payment.provider_reference, amount=str(payment.amount.amount), event_id="evt-match-1",
    )
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: fake_provider)

    event = process_webhook(provider_code="fake", headers={}, body=b"{}", query_params={})

    assert event.processed is True
