"""
End-to-end payment behaviour: reconciliation, idempotency under retry, COD's
configurable limits, and what a customer sees when a gateway is switched off.

These are the paths that only appear once the pieces are assembled — a lost
webhook recovered by a status poll, a checkout retried after a cache flush, a COD
order above its ceiling.
"""
import uuid
from decimal import Decimal

import pytest
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import create_payment_for_order, reconcile_payment
from tests.conftest import INSTAPAY_TEST_CONFIG, PAYMOB_TEST_CONFIG, payment_providers_with

pytestmark = pytest.mark.django_db

CHECKOUT_URL = "/api/v1/checkout/"
CART_ITEMS_URL = "/api/v1/cart/items/"


def _checkout(client, variant, address, shipping_setup, *, method="cod", key=None, quantity=1):
    client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": quantity}, format="json")
    return client.post(
        CHECKOUT_URL,
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": method,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Reconciliation: recovering from a webhook that never arrived
# ---------------------------------------------------------------------------


class _StubStatus:
    """A gateway whose only job is to answer the status inquiry."""

    def __init__(self, status, raw=None):
        self._status = status
        self._raw = raw or {}

    def get_status(self, *, payment):
        from apps.payments.providers.base import StatusResult

        return StatusResult(status=self._status, raw=self._raw)


def _install(monkeypatch, provider):
    monkeypatch.setattr("apps.payments.services.get_payment_provider", lambda code: provider)


def test_reconciliation_recovers_a_lost_capture_and_confirms_the_order(
    monkeypatch, gateways_enabled, gateway_payment
):
    """
    The scenario this whole path exists for: the customer paid, the webhook never
    arrived, and the order is sitting unconfirmed. A status poll must settle it —
    and confirm the order, which the old code forgot to do.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _install(
        monkeypatch,
        _StubStatus("captured", {"amount_cents": int(payment.amount.amount * 100)}),
    )

    reconcile_payment(payment)

    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == "confirmed"


def test_reconciliation_refuses_to_settle_a_different_amount(
    monkeypatch, gateways_enabled, gateway_payment
):
    """
    A reconciliation that settles the wrong sum is a silent loss — unlike a
    webhook mismatch, nothing else would ever flag it. So it raises.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _install(monkeypatch, _StubStatus("captured", {"amount_cents": 100}))

    with pytest.raises(ApplicationError) as exc_info:
        reconcile_payment(payment)

    assert exc_info.value.code == ErrorCodes.PAYMENT_AMOUNT_MISMATCH
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_reconciliation_ignores_an_unmapped_status(monkeypatch, gateways_enabled, gateway_payment):
    """An unfamiliar status is left alone, not guessed at."""
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    _install(monkeypatch, _StubStatus("something_new"))

    reconcile_payment(payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_reconciliation_does_not_walk_a_captured_payment_backwards(
    monkeypatch, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    cents = int(payment.amount.amount * 100)
    _install(monkeypatch, _StubStatus("captured", {"amount_cents": cents}))
    reconcile_payment(payment)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED

    # Now the gateway says it failed. Captured money is ours; this must be refused.
    _install(monkeypatch, _StubStatus("failed", {"amount_cents": cents}))
    with pytest.raises(ApplicationError):
        reconcile_payment(payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_reconciliation_is_a_no_op_for_a_gateway_that_cannot_be_asked(
    gateways_enabled, gateway_payment
):
    """COD has nothing to reconcile against; asking must not error."""
    payment = gateway_payment(provider="cod", method="cod")
    before = payment.status

    reconcile_payment(payment)

    payment.refresh_from_db()
    assert payment.status == before


def test_status_poll_survives_a_gateway_that_is_down(
    monkeypatch, authenticated_client, gateways_enabled, gateway_payment
):
    """
    The customer is staring at a spinner. A provider outage must give them the
    status we already hold, not a 500.
    """
    from apps.payments.providers.http import GatewayError

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    class Down:
        def get_status(self, *, payment):
            raise GatewayError("The payment provider could not be reached.")

    _install(monkeypatch, Down())

    response = authenticated_client.get(f"/api/v1/payments/{payment.id}/status/")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == PaymentStatus.REQUIRES_ACTION


# ---------------------------------------------------------------------------
# Idempotency: never two charges for one intent
# ---------------------------------------------------------------------------


def test_checkout_retried_with_the_same_key_creates_one_order(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    variant = product_with_variant_and_stock["variant"]
    key = str(uuid.uuid4())

    first = _checkout(authenticated_client, variant, address, shipping_setup, key=key)
    second = _checkout(authenticated_client, variant, address, shipping_setup, key=key)

    assert first.status_code == 201
    assert second.status_code in (200, 201)
    assert (
        first.json()["data"]["order"]["order_number"]
        == second.json()["data"]["order"]["order_number"]
    )
    assert Payment.objects.count() == 1


def test_payment_creation_is_idempotent_even_when_the_cache_is_gone(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The cache-backed mixin is the fast path. This is the slow one: the same key
    arriving after the cache was flushed, which is exactly when a naive retry
    would charge twice. The unique constraint on Payment.idempotency_key catches
    it, and `create_payment_for_order` returns the existing row.
    """
    from django.core.cache import cache

    variant = product_with_variant_and_stock["variant"]
    response = _checkout(authenticated_client, variant, address, shipping_setup)
    assert response.status_code == 201
    original = Payment.objects.get()
    assert original.idempotency_key

    cache.clear()

    again = create_payment_for_order(
        order=original.order,
        provider_code="cod",
        method="cod",
        idempotency_key=original.idempotency_key,
    )

    assert again.id == original.id
    assert Payment.objects.count() == 1


def test_two_checkouts_with_different_keys_are_two_orders(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """The other half of idempotency: a genuine second order must go through."""
    variant = product_with_variant_and_stock["variant"]

    first = _checkout(authenticated_client, variant, address, shipping_setup)
    second = _checkout(authenticated_client, variant, address, shipping_setup)

    assert first.status_code == second.status_code == 201
    assert (
        first.json()["data"]["order"]["order_number"]
        != second.json()["data"]["order"]["order_number"]
    )


def test_checkout_without_an_idempotency_key_is_refused(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")

    response = authenticated_client.post(
        CHECKOUT_URL,
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.IDEMPOTENCY_KEY_REQUIRED


def test_every_payment_gets_a_merchant_reference(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The reference we demand back on a callback. Without it a webhook can only be
    matched on a provider-supplied id, and the wrong-order check has nothing to
    compare against.
    """
    variant = product_with_variant_and_stock["variant"]
    _checkout(authenticated_client, variant, address, shipping_setup)

    payment = Payment.objects.get()
    assert payment.merchant_reference
    assert payment.order.order_number in payment.merchant_reference
    assert len(payment.merchant_reference) <= 64


def test_merchant_references_are_unique_across_payments(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    variant = product_with_variant_and_stock["variant"]
    _checkout(authenticated_client, variant, address, shipping_setup)
    _checkout(authenticated_client, variant, address, shipping_setup)

    references = list(Payment.objects.values_list("merchant_reference", flat=True))
    assert len(references) == len(set(references)) == 2


# ---------------------------------------------------------------------------
# COD configuration
# ---------------------------------------------------------------------------


def _cod_settings(settings, **overrides):
    from django.conf import settings as django_settings

    base = {**django_settings.PAYMENT_PROVIDERS["COD"], **overrides}
    settings.PAYMENT_PROVIDERS = payment_providers_with(COD=base)
    return settings


def test_cod_fee_comes_from_configuration(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _cod_settings(settings, FEE_AMOUNT="25.00")
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup)

    assert response.status_code == 201
    assert Decimal(response.json()["data"]["order"]["cod_fee"]) == Decimal("25.00")


def test_cod_below_the_minimum_is_refused(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _cod_settings(settings, MIN_ORDER_AMOUNT="100000.00")
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup)

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE
    assert Payment.objects.count() == 0


def test_cod_above_the_maximum_is_refused(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _cod_settings(settings, MAX_ORDER_AMOUNT="1.00")
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup)

    assert response.status_code == 400
    assert Payment.objects.count() == 0


def test_a_zero_maximum_means_no_ceiling(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _cod_settings(settings, MAX_ORDER_AMOUNT="0.00")
    variant = product_with_variant_and_stock["variant"]

    assert _checkout(authenticated_client, variant, address, shipping_setup).status_code == 201


def test_cod_restricted_to_certain_shipping_methods(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """A carrier that will not handle cash cannot take a COD order."""
    _cod_settings(settings, ALLOWED_SHIPPING_METHOD_IDS=str(shipping_setup["method"].id + 999))
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup)

    assert response.status_code == 400
    assert Payment.objects.count() == 0


def test_cod_allowed_when_its_shipping_method_is_listed(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _cod_settings(settings, ALLOWED_SHIPPING_METHOD_IDS=str(shipping_setup["method"].id))
    variant = product_with_variant_and_stock["variant"]

    assert _checkout(authenticated_client, variant, address, shipping_setup).status_code == 201


def test_cod_disabled_is_refused_at_checkout(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _cod_settings(settings, ENABLED=False)
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup)

    assert response.status_code == 400
    assert Payment.objects.count() == 0


# ---------------------------------------------------------------------------
# A disabled gateway, from the customer's side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["card", "mobile_wallet", "instapay"])
def test_a_disabled_method_is_refused_before_an_order_exists(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup, method
):
    """
    Every gateway is off by default in the test settings. Picking one must fail
    while the customer is still on the checkout page — and must not leave a
    half-made order behind.
    """
    from apps.orders.models import Order

    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup, method=method)

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE
    assert Order.objects.count() == 0
    assert Payment.objects.count() == 0


def test_a_disabled_methods_error_never_names_our_configuration(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The customer is told the method is unavailable. They are not told that
    PAYMOB_HMAC_SECRET is empty — that is reconnaissance.
    """
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup, method="card")

    body = response.content.decode()
    for forbidden in ("PAYMOB", "HMAC", "SECRET", "INTEGRATION_ID", "ENABLED"):
        assert forbidden not in body


def test_an_enabled_method_appears_in_the_methods_endpoint(api_client, gateways_enabled):
    response = api_client.get("/api/v1/payments/methods/")

    codes = [item["code"] for item in response.json()["data"]]
    assert codes == ["cod", "card", "mobile_wallet", "instapay"]
    # Every method carries the Arabic label and instructions the storefront shows,
    # so neither frontend keeps its own copy of the wording.
    for item in response.json()["data"]:
        assert item["label_ar"]
        assert item["instructions_ar"]


def test_paymob_without_an_integration_id_hides_only_that_method(api_client, settings):
    """
    Partial configuration is normal: a shop may have card enabled and not wallet.
    One missing integration ID must remove one method, not the gateway.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "INTEGRATION_ID_WALLET": ""}
    )

    codes = [item["code"] for item in api_client.get("/api/v1/payments/methods/").json()["data"]]

    assert "card" in codes
    assert "mobile_wallet" not in codes


def test_an_enabled_gateway_missing_its_hmac_secret_offers_nothing(api_client, settings):
    """
    The rule that matters: a flag is not enough. Without the HMAC secret we could
    take money and never be able to verify that we had.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "HMAC_SECRET": ""}
    )

    codes = [item["code"] for item in api_client.get("/api/v1/payments/methods/").json()["data"]]

    for paymob_method in ("card", "mobile_wallet"):
        assert paymob_method not in codes


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------


def test_an_unlisted_currency_is_refused_before_a_gateway_is_contacted(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    settings.PAYMENT_ALLOWED_CURRENCIES = ["USD"]
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup)

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == ErrorCodes.PAYMENT_CURRENCY_MISMATCH
    # Nothing was created, and no gateway was contacted.
    assert Payment.objects.count() == 0


def test_egp_is_accepted_by_default(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    from apps.payments.services import assert_currency_allowed

    assert_currency_allowed("EGP")


# ---------------------------------------------------------------------------
# The customer-visible transfer details
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# InstaPay: a transfer the customer makes and staff confirm
# ---------------------------------------------------------------------------
# The risk here is the opposite of COD's. A COD order ships on a promise; an
# InstaPay order must NOT, because the promise is "I will transfer" and nothing
# verifies it. So the tests below are mostly about what stays unconfirmed.


def _instapay_enabled(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    return settings


def test_instapay_checkout_hands_the_customer_the_transfer_details(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    _instapay_enabled(settings)
    variant = product_with_variant_and_stock["variant"]

    response = _checkout(authenticated_client, variant, address, shipping_setup, method="instapay")

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["payment"]["status"] == PaymentStatus.REQUIRES_ACTION
    # The address and the order reference reach the customer, or they cannot pay.
    assert "alreef@instapay" in data["payment"]["instructions_ar"]
    assert data["order"]["order_number"] in data["payment"]["instructions_ar"]
    # Not paid, so not confirmed.
    assert data["order"]["status"] == "pending"


def test_instapay_order_does_not_consume_stock_until_the_transfer_is_confirmed(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The whole point. Stock must not be committed for an order whose payment is a
    customer's stated intention to transfer.
    """
    _instapay_enabled(settings)
    batch = product_with_variant_and_stock["batch"]
    before = batch.quantity

    _checkout(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup,
        method="instapay",
    )

    batch.refresh_from_db()
    # Reserved, not consumed.
    assert batch.quantity == before


def test_confirming_an_instapay_transfer_captures_and_confirms(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    from apps.payments.services import record_manual_receipt

    _instapay_enabled(settings)
    response = _checkout(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup,
        method="instapay",
    )
    payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])
    assert payment.status == PaymentStatus.REQUIRES_ACTION

    record_manual_receipt(payment, note="seen in the InstaPay app")

    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == "confirmed"


def test_an_instapay_transfer_that_never_arrives_fails_the_payment(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    from apps.payments.services import record_manual_refusal

    _instapay_enabled(settings)
    response = _checkout(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup,
        method="instapay",
    )
    payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])

    record_manual_refusal(payment, reason="no transfer after 48h")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    assert "no transfer" in payment.failure_reason
    # The order is left alone: whether to cancel or chase is a commercial call.
    payment.order.refresh_from_db()
    assert payment.order.status == "pending"


def test_an_unconfirmed_instapay_payment_cannot_be_refunded(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """No money arrived, so there is none to send back."""
    from djmoney.money import Money as M

    from apps.payments.services import create_refund

    _instapay_enabled(settings)
    response = _checkout(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup,
        method="instapay",
    )
    payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])

    with pytest.raises(ApplicationError) as exc_info:
        create_refund(payment=payment, amount=M("10.00", payment.amount_currency))

    assert exc_info.value.code == ErrorCodes.PAYMENT_INVALID_STATE


def test_instapay_without_an_address_is_not_offered(settings, api_client):
    """
    A published InstaPay method with nowhere to transfer to is a dead end the
    customer only finds after entering an address.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        INSTAPAY={**INSTAPAY_TEST_CONFIG, "ADDRESS": ""}
    )

    codes = [item["code"] for item in api_client.get("/api/v1/payments/methods/").json()["data"]]

    assert "instapay" not in codes


def test_delivery_does_not_capture_an_instapay_payment(
    settings, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The COD capture hook is on the DELIVERED transition. It must not fire for
    InstaPay — an InstaPay order should never have reached delivery unpaid, and if
    it somehow does, shipping it is not evidence that a transfer happened.
    """
    from apps.orders.models import OrderStatus as OS
    from apps.orders.services import transition_status

    _instapay_enabled(settings)
    response = _checkout(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup,
        method="instapay",
    )
    payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])
    order = payment.order

    # Force the order forward as if somebody dispatched it by mistake.
    from apps.orders.models import Order

    Order.objects.filter(pk=order.pk).update(status=OS.CONFIRMED)
    order.refresh_from_db()
    for status in (OS.PROCESSING, OS.PACKED, OS.SHIPPED, OS.DELIVERED):
        order = transition_status(order, status)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION
