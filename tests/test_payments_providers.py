"""
The two adapters that exist: Paymob, and InstaPay's manual transfer route.

WHAT THESE TESTS ARE AND ARE NOT. No real gateway is contacted — Paymob has not
been onboarded for this project, so there are no sandbox credentials to use.
`requests.request` is replaced with a recorder and the responses it returns are
fixtures shaped like Paymob's documented ones.

That makes these contract tests, and they catch the failure modes that matter for
a gateway you cannot yet call: a signature computed over the wrong fields, an
amount sent in the wrong unit, a secret leaking into a stored payload, a disabled
provider that constructs anyway. What they cannot prove is that Paymob's
documentation matches its live service. That needs sandbox credentials and is the
last item in docs/payments/PROVIDERS.md.

The HMAC expectations are computed independently of the adapter — the
concatenation is spelled out by hand from the published field order — so a mistake
in the adapter cannot agree with a mistake in the test.

InstaPay needs none of that: there is nothing to call. Its tests are about the
thing that *is* risky, which is that a payment nobody verified must not look
settled.
"""
import hashlib
import hmac
import json

import pytest
from djmoney.money import Money

from apps.payments.providers import get_payment_provider
from apps.payments.providers.http import GatewayError
from tests.conftest import INSTAPAY_TEST_CONFIG, PAYMOB_TEST_CONFIG, payment_providers_with

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def paymob_enabled(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(PAYMOB=PAYMOB_TEST_CONFIG)
    settings.BACKEND_PUBLIC_URL = "https://api.example.test"
    settings.FRONTEND_URL = "https://shop.example.test"
    return settings


@pytest.fixture
def instapay_enabled(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    return settings


class Recorder:
    """Replaces requests.request and hands back queued fixtures."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError(f"Unexpected extra gateway call: {method} {url}")
        status, payload = self._responses.pop(0)
        return _Response(status, payload)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.content = json.dumps(payload).encode()


@pytest.fixture
def gateway(monkeypatch):
    def install(*responses):
        recorder = Recorder(responses)
        monkeypatch.setattr("apps.payments.providers.http.requests.request", recorder)
        return recorder

    return install


@pytest.fixture
def order_with_payment(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    """A real order and Payment row, built through checkout so nothing is faked."""
    import uuid

    from apps.payments.models import Payment

    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(
        "/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 2}, format="json"
    )
    response = authenticated_client.post(
        "/api/v1/checkout/",
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201
    payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])
    return payment.order, payment


# ---------------------------------------------------------------------------
# Configuration gating — the "never enabled by a flag alone" rule
# ---------------------------------------------------------------------------


def test_paymob_refuses_to_construct_when_disabled(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "ENABLED": False}
    )
    with pytest.raises(NotImplementedError):
        get_payment_provider("paymob")


@pytest.mark.parametrize("missing", ["SECRET_KEY", "PUBLIC_KEY", "HMAC_SECRET"])
def test_paymob_refuses_to_construct_with_a_missing_credential(settings, missing):
    """
    Enabled is not configured. A Paymob adapter without an HMAC secret could take
    money and have no trustworthy way to learn that it had, so it must not exist.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(PAYMOB={**PAYMOB_TEST_CONFIG, missing: ""})

    with pytest.raises(NotImplementedError) as exc_info:
        get_payment_provider("paymob")

    assert missing in str(exc_info.value)


def test_instapay_refuses_to_construct_when_disabled(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        INSTAPAY={**INSTAPAY_TEST_CONFIG, "ENABLED": False}
    )
    with pytest.raises(NotImplementedError):
        get_payment_provider("instapay")


def test_instapay_refuses_to_construct_without_an_address(settings):
    """
    An InstaPay method with nowhere to transfer to is not a payment method, it is
    a dead end the customer only discovers after checkout.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        INSTAPAY={**INSTAPAY_TEST_CONFIG, "ADDRESS": ""}
    )
    with pytest.raises(NotImplementedError) as exc_info:
        get_payment_provider("instapay")

    assert "INSTAPAY_ADDRESS" in str(exc_info.value)


@pytest.mark.parametrize("code", ["fawry", "valu", "sympl", "aman", "stripe"])
def test_removed_and_unknown_routes_are_rejected(code):
    """Fawry, valU, Sympl and Aman were removed. Nothing should resolve them."""
    with pytest.raises(NotImplementedError):
        get_payment_provider(code)


def test_manual_route_has_no_adapter_and_says_why():
    with pytest.raises(NotImplementedError) as exc_info:
        get_payment_provider("manual")

    assert "outside the system" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Paymob: HMAC verification
# ---------------------------------------------------------------------------


def _paymob_transaction(**overrides):
    """A transaction object shaped like Paymob's documented callback payload."""
    transaction = {
        "id": 210000123,
        "amount_cents": 25000,
        "created_at": "2026-09-21T10:00:00.000000",
        "currency": "EGP",
        "error_occured": False,
        "has_parent_transaction": False,
        "integration_id": 4001,
        "is_3d_secure": True,
        "is_auth": False,
        "is_capture": False,
        "is_refunded": False,
        "is_standalone_payment": True,
        "is_voided": False,
        "order": {"id": 500123, "merchant_order_id": "ORD-20260921-00001-AAAABBBB"},
        "owner": 99001,
        "pending": False,
        "source_data": {"pan": "2346", "sub_type": "MasterCard", "type": "card"},
        "success": True,
    }
    transaction.update(overrides)
    return transaction


def _expected_paymob_hmac(transaction, secret=PAYMOB_TEST_CONFIG["HMAC_SECRET"]):
    """
    Compute the HMAC by hand from Paymob's published field order.

    Written out longhand on purpose: if this shared a helper with the adapter, the
    pair could be wrong together and still agree.
    """

    def render(value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    source = transaction.get("source_data") or {}
    ordered = [
        transaction.get("amount_cents"),
        transaction.get("created_at"),
        transaction.get("currency"),
        transaction.get("error_occured"),
        transaction.get("has_parent_transaction"),
        transaction.get("id"),
        transaction.get("integration_id"),
        transaction.get("is_3d_secure"),
        transaction.get("is_auth"),
        transaction.get("is_capture"),
        transaction.get("is_refunded"),
        transaction.get("is_standalone_payment"),
        transaction.get("is_voided"),
        (transaction.get("order") or {}).get("id"),
        transaction.get("owner"),
        transaction.get("pending"),
        source.get("pan"),
        source.get("sub_type"),
        source.get("type"),
        transaction.get("success"),
    ]
    message = "".join(render(value) for value in ordered)
    return hmac.new(secret.encode(), message.encode(), hashlib.sha512).hexdigest()


def test_paymob_accepts_a_correctly_signed_callback(paymob_enabled):
    provider = get_payment_provider("paymob")
    transaction = _paymob_transaction()
    body = json.dumps({"type": "TRANSACTION", "obj": transaction}).encode()

    assert provider.verify_webhook(
        headers={}, body=body, query_params={"hmac": _expected_paymob_hmac(transaction)}
    )


def test_paymob_accepts_the_hmac_from_the_body_as_well_as_the_query(paymob_enabled):
    """Paymob sends the digest both ways; either must be accepted."""
    provider = get_payment_provider("paymob")
    transaction = _paymob_transaction()
    body = json.dumps(
        {"type": "TRANSACTION", "obj": transaction, "hmac": _expected_paymob_hmac(transaction)}
    ).encode()

    assert provider.verify_webhook(headers={}, body=body, query_params={})


def test_paymob_rejects_a_tampered_amount(paymob_enabled):
    """
    The attack this check exists for: take a genuine callback, raise the amount,
    keep the signature. The HMAC covers amount_cents, so it must stop matching.
    """
    provider = get_payment_provider("paymob")
    signature = _expected_paymob_hmac(_paymob_transaction())
    body = json.dumps({"obj": _paymob_transaction(amount_cents=1)}).encode()

    assert not provider.verify_webhook(headers={}, body=body, query_params={"hmac": signature})


@pytest.mark.parametrize(
    "field,value",
    [
        ("success", False),
        ("id", 999999),
        ("currency", "USD"),
        ("is_refunded", True),
        ("integration_id", 9999),
        ("owner", 1),
    ],
)
def test_paymob_signature_covers_each_decisive_field(paymob_enabled, field, value):
    """Changing any field in the signed set must invalidate the signature."""
    provider = get_payment_provider("paymob")
    signature = _expected_paymob_hmac(_paymob_transaction())
    body = json.dumps({"obj": _paymob_transaction(**{field: value})}).encode()

    assert not provider.verify_webhook(headers={}, body=body, query_params={"hmac": signature})


def test_paymob_rejects_a_callback_with_no_signature(paymob_enabled):
    provider = get_payment_provider("paymob")
    body = json.dumps({"obj": _paymob_transaction()}).encode()

    assert not provider.verify_webhook(headers={}, body=body, query_params={})


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"obj": null}', b'{"obj": {}}'])
def test_paymob_rejects_malformed_bodies(paymob_enabled, body):
    provider = get_payment_provider("paymob")
    assert not provider.verify_webhook(headers={}, body=body, query_params={"hmac": "x" * 128})


def test_paymob_rejects_a_flattened_redirect_payload(paymob_enabled):
    """
    Only the server-to-server callback may settle a payment. The browser redirect
    carries the same fields flattened into query parameters, and accepting that
    shape would let a customer's browser confirm its own order.
    """
    provider = get_payment_provider("paymob")
    flat = {k: v for k, v in _paymob_transaction().items() if k != "order"}
    flat["order_id"] = 500123

    assert not provider.verify_webhook(
        headers={}, body=json.dumps(flat).encode(), query_params={"hmac": "x" * 128}
    )


# ---------------------------------------------------------------------------
# Paymob: parsing and classification
# ---------------------------------------------------------------------------


def test_paymob_parses_amount_back_into_major_units(paymob_enabled):
    from decimal import Decimal

    provider = get_payment_provider("paymob")
    body = json.dumps({"obj": _paymob_transaction(amount_cents=25000)}).encode()

    parsed = provider.parse_webhook_event(headers={}, body=body, query_params={})

    assert parsed["amount"] == Decimal("250.00")
    assert parsed["currency"] == "EGP"
    assert parsed["merchant_reference"] == "ORD-20260921-00001-AAAABBBB"
    assert parsed["provider_reference"] == "210000123"
    assert parsed["event_id"] == "paymob:210000123"


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, "captured"),
        ({"success": False}, "failed"),
        ({"pending": True}, "pending"),
        ({"is_voided": True}, "voided"),
        ({"is_refunded": True}, "refunded"),
        # An authorization that has not been captured is money promised, not taken.
        ({"is_auth": True, "is_capture": False}, "authorized"),
        # A refunded transaction still reports success; terminal wins.
        ({"is_refunded": True, "success": True}, "refunded"),
    ],
)
def test_paymob_classifies_transaction_outcomes(paymob_enabled, overrides, expected):
    from apps.payments.providers.paymob import PaymobPaymentProvider

    assert PaymobPaymentProvider.classify_transaction(_paymob_transaction(**overrides)) == expected


# ---------------------------------------------------------------------------
# Paymob: creating a payment
# ---------------------------------------------------------------------------


def test_paymob_create_payment_sends_our_amount_in_cents_and_our_reference(
    paymob_enabled, gateway, order_with_payment
):
    order, payment = order_with_payment
    payment.method = "card"
    recorder = gateway(
        (201, {"id": "int_123", "client_secret": "cs_test_abc", "intention_order_id": 9001})
    )

    provider = get_payment_provider("paymob")
    result = provider.create_payment(payment=payment, order=order)

    assert result.success is True
    # The customer has somewhere to go and has not paid.
    assert result.status == "requires_action"
    assert "cs_test_abc" in result.redirect_url
    assert PAYMOB_TEST_CONFIG["PUBLIC_KEY"] in result.redirect_url

    sent = recorder.calls[0]
    assert sent["url"] == "https://accept.paymob.com/v1/intention/"
    assert sent["headers"]["Authorization"] == f"Token {PAYMOB_TEST_CONFIG['SECRET_KEY']}"
    assert sent["timeout"] == 5
    body = sent["json"]
    # Server-computed amount, in minor units, never anything from a request.
    assert body["amount"] == int(payment.amount.amount * 100)
    assert body["currency"] == "EGP"
    assert body["payment_methods"] == [4001]
    assert body["special_reference"] == payment.merchant_reference
    assert body["notification_url"] == "https://api.example.test/api/v1/payments/webhooks/paymob/"


def _items_total(body):
    return sum(item["amount"] * item["quantity"] for item in body["items"])


def test_paymob_intention_items_add_up_to_the_amount_with_shipping(
    paymob_enabled, gateway, order_with_payment
):
    order, payment = order_with_payment
    payment.method = "card"
    recorder = gateway((201, {"id": "int_1", "client_secret": "cs_1"}))

    get_payment_provider("paymob").create_payment(payment=payment, order=order)

    body = recorder.calls[0]["json"]
    # Paymob rejects an intention whose items do not sum to its amount.
    assert _items_total(body) == body["amount"]
    assert order.shipping_total.amount > 0
    assert body["items"][-1]["name"] == "Shipping & fees"


def test_paymob_intention_is_one_line_when_something_came_off(
    paymob_enabled, gateway, order_with_payment
):
    from decimal import Decimal

    from djmoney.money import Money

    order, payment = order_with_payment
    payment.method = "card"
    # A gift card or discount paid part of it: the products alone exceed the charge.
    payment.amount = Money(Decimal("1.00"), "EGP")
    recorder = gateway((201, {"id": "int_1", "client_secret": "cs_1"}))

    get_payment_provider("paymob").create_payment(payment=payment, order=order)

    body = recorder.calls[0]["json"]
    assert body["amount"] == 100
    assert body["items"] == [{"name": f"Order {order.order_number}", "amount": 100, "quantity": 1}]


@pytest.mark.parametrize("method,integration_id", [("card", 4001), ("mobile_wallet", 4002)])
def test_paymob_routes_each_method_to_its_own_integration_id(
    paymob_enabled, gateway, order_with_payment, method, integration_id
):
    order, payment = order_with_payment
    payment.method = method
    recorder = gateway((201, {"id": "int_1", "client_secret": "cs_1"}))

    get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert recorder.calls[0]["json"]["payment_methods"] == [integration_id]


def test_paymob_serves_instapay_when_its_integration_id_is_configured(
    settings, gateway, order_with_payment
):
    """
    Paymob lists InstaPay as coming soon. When it lands, the only change is this
    setting — the adapter already routes the method.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "INTEGRATION_ID_INSTAPAY": "4009"}
    )
    settings.BACKEND_PUBLIC_URL = "https://api.example.test"
    order, payment = order_with_payment
    payment.method = "instapay"
    recorder = gateway((201, {"id": "int_1", "client_secret": "cs_1"}))

    result = get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert result.success is True
    assert recorder.calls[0]["json"]["payment_methods"] == [4009]


def test_paymob_method_without_an_integration_id_is_refused(paymob_enabled, order_with_payment):
    order, payment = order_with_payment
    payment.method = "instapay"

    with pytest.raises(NotImplementedError) as exc_info:
        get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert "INTEGRATION_ID_INSTAPAY" in str(exc_info.value)


def test_paymob_rejects_a_method_it_does_not_serve(paymob_enabled, order_with_payment):
    order, payment = order_with_payment
    payment.method = "cod"

    with pytest.raises(NotImplementedError):
        get_payment_provider("paymob").create_payment(payment=payment, order=order)


def test_paymob_create_payment_reports_failure_when_no_client_secret_comes_back(
    paymob_enabled, gateway, order_with_payment
):
    order, payment = order_with_payment
    payment.method = "card"
    gateway((201, {"id": "int_123"}))

    result = get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert result.success is False
    assert "checkout reference" in result.error


def test_paymob_create_payment_survives_a_gateway_error(paymob_enabled, gateway, order_with_payment):
    order, payment = order_with_payment
    payment.method = "card"
    gateway((500, {"detail": "upstream exploded"}))

    result = get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert result.success is False
    # The customer-facing message does not quote the provider's internals.
    assert "rejected the request" in result.error


# ---------------------------------------------------------------------------
# Paymob: refunds
# ---------------------------------------------------------------------------


def test_paymob_refund_authenticates_with_the_accept_api_key_and_sends_cents(
    paymob_enabled, gateway, order_with_payment
):
    order, payment = order_with_payment
    payment.provider_reference = "210000123"
    recorder = gateway(
        (201, {"token": "auth-token-xyz"}),
        (201, {"id": 210000999, "success": True}),
    )

    result = get_payment_provider("paymob").refund(
        payment=payment, amount=Money("100.00", "EGP"), reason="damaged"
    )

    assert result.success is True
    assert result.provider_refund_id == "210000999"

    auth_call, refund_call = recorder.calls
    assert auth_call["url"].endswith("/api/auth/tokens")
    assert auth_call["json"] == {"api_key": PAYMOB_TEST_CONFIG["API_KEY"]}
    assert refund_call["url"].endswith("/api/acceptance/void_refund/refund")
    assert refund_call["json"]["transaction_id"] == "210000123"
    assert refund_call["json"]["amount_cents"] == 10000
    assert refund_call["json"]["auth_token"] == "auth-token-xyz"


def test_paymob_refund_without_the_accept_api_key_fails_clearly(paymob_enabled, order_with_payment):
    """
    PAYMOB_API_KEY is a different credential from PAYMOB_SECRET_KEY, and only
    refunds need it. A shop can be live for taking money and unable to refund —
    which staff must be told, not discover mid-refund.
    """
    order, payment = order_with_payment
    payment.provider_reference = "210000123"
    paymob_enabled.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "API_KEY": ""}
    )

    result = get_payment_provider("paymob").refund(payment=payment, amount=Money("10.00", "EGP"))

    assert result.success is False
    assert "PAYMOB_API_KEY" in result.error


def test_paymob_refund_on_a_payment_with_no_transaction_is_refused(
    paymob_enabled, order_with_payment
):
    order, payment = order_with_payment
    payment.provider_reference = ""

    result = get_payment_provider("paymob").refund(payment=payment, amount=Money("10.00", "EGP"))

    assert result.success is False


# ---------------------------------------------------------------------------
# InstaPay: the manual transfer route
# ---------------------------------------------------------------------------


def test_instapay_hands_the_customer_the_address_and_the_reference(
    instapay_enabled, order_with_payment
):
    """
    Everything the customer needs to make the transfer, and the reference that
    lets a staff member match it to this order when it lands.
    """
    order, payment = order_with_payment

    result = get_payment_provider("instapay").create_payment(payment=payment, order=order)

    assert result.success is True
    # NOT captured, and not even pending: the customer has something to do.
    assert result.status == "requires_action"
    assert result.metadata["instapay_address"] == "alreef@instapay"
    assert result.metadata["transfer_reference"] == order.order_number
    assert result.metadata["transfer_amount"] == f"{payment.amount.amount:.2f}"
    # The Arabic instruction carries the address, the amount and the reference,
    # because a customer reading one sentence should not have to hunt.
    assert "alreef@instapay" in result.metadata["instructions_ar"]
    assert order.order_number in result.metadata["instructions_ar"]


def test_instapay_makes_no_network_call(instapay_enabled, monkeypatch, order_with_payment):
    """There is nothing to call, and calling anything would be a fabrication."""

    def explode(*args, **kwargs):
        raise AssertionError("InstaPay must not make an HTTP request")

    monkeypatch.setattr("apps.payments.providers.http.requests.request", explode)
    order, payment = order_with_payment

    assert get_payment_provider("instapay").create_payment(payment=payment, order=order).success


def test_instapay_never_verifies_a_webhook(instapay_enabled):
    """
    InstaPay sends none, so any POST claiming to be one is hostile. (The route does
    not exist either — `instapay` is not in WEBHOOK_PROVIDERS.)
    """
    provider = get_payment_provider("instapay")
    assert provider.verify_webhook(headers={}, body=b'{"paid": true}', query_params={}) is False

    with pytest.raises(NotImplementedError):
        provider.parse_webhook_event(headers={}, body=b"{}", query_params={})


def test_instapay_has_no_authorize_step(instapay_enabled, order_with_payment):
    order, payment = order_with_payment
    with pytest.raises(NotImplementedError):
        get_payment_provider("instapay").authorize(payment=payment)


def test_instapay_refund_is_recorded_as_manual(instapay_enabled, order_with_payment):
    order, payment = order_with_payment

    result = get_payment_provider("instapay").refund(payment=payment, amount=Money("50.00", "EGP"))

    assert result.success is True
    # The prefix is what tells reconciliation no gateway moved this money.
    assert result.provider_refund_id.startswith("MANUAL-IPN-")


def test_instapay_status_echoes_our_own_record(instapay_enabled, order_with_payment):
    """
    There is no external record. Saying so is honest — a caller polling this learns
    nothing new, which is the truth — and it means the reconciliation sweep can
    call it without a special case.
    """
    order, payment = order_with_payment

    result = get_payment_provider("instapay").get_status(payment=payment)

    assert result.status == payment.status
    assert result.raw == {"source": "local"}


# ---------------------------------------------------------------------------
# The HTTP layer
# ---------------------------------------------------------------------------


def test_gateway_calls_always_carry_a_timeout(paymob_enabled, gateway, order_with_payment):
    """A gateway that stops answering must not hold a worker, or a customer, open."""
    order, payment = order_with_payment
    payment.method = "card"
    recorder = gateway((201, {"id": "1", "client_secret": "cs"}))

    get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert recorder.calls[0]["timeout"] == PAYMOB_TEST_CONFIG["TIMEOUT_SECONDS"]


def test_gateway_timeout_becomes_a_gateway_error(monkeypatch, paymob_enabled, order_with_payment):
    import requests

    order, payment = order_with_payment
    payment.method = "card"

    def timeout(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr("apps.payments.providers.http.requests.request", timeout)

    result = get_payment_provider("paymob").create_payment(payment=payment, order=order)

    assert result.success is False
    assert "did not respond in time" in result.error


def test_oversized_gateway_response_is_refused(monkeypatch, paymob_enabled):
    """A provider is not a trusted source of unbounded JSON."""
    from apps.payments.providers.http import MAX_RESPONSE_BYTES, GatewayClient

    class Huge:
        status_code = 200
        content = b"x" * (MAX_RESPONSE_BYTES + 1)

    monkeypatch.setattr("apps.payments.providers.http.requests.request", lambda *a, **k: Huge())
    client = GatewayClient(provider_code="paymob", base_url="https://accept.paymob.com")

    with pytest.raises(GatewayError) as exc_info:
        client.post("/v1/intention/")

    assert "large" in exc_info.value.message


def test_non_json_gateway_response_is_refused(monkeypatch, paymob_enabled):
    from apps.payments.providers.http import GatewayClient

    class Html:
        status_code = 200
        content = b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr("apps.payments.providers.http.requests.request", lambda *a, **k: Html())
    client = GatewayClient(provider_code="paymob", base_url="https://accept.paymob.com")

    with pytest.raises(GatewayError):
        client.post("/v1/intention/")


# ---------------------------------------------------------------------------
# Amount arithmetic
# ---------------------------------------------------------------------------


def test_cents_conversion_is_exact_for_awkward_amounts():
    """
    Money in minor units is where rounding bugs become undercharges. 250.05 EGP
    must be 25005 cents, not 25004.
    """
    from decimal import Decimal

    from apps.payments.providers.paymob import PaymobPaymentProvider

    assert PaymobPaymentProvider._to_cents(Decimal("250.05")) == 25005
    assert PaymobPaymentProvider._to_cents(Decimal("0.01")) == 1
    assert PaymobPaymentProvider._to_cents(Decimal("1000")) == 100000
    # ROUND_HALF_UP, not Decimal's banker's-rounding default, so an exact half
    # never undercharges.
    assert PaymobPaymentProvider._to_cents(Decimal("10.005")) == 1001
