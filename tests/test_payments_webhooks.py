"""
The webhook matrix.

A payment webhook endpoint is the highest-value target in an e-commerce backend:
whoever can make it say "paid" gets free honey. It is public, unauthenticated,
and the only thing guarding it is a signature. So these tests are written as a
list of the ways a hostile or confused caller can get it wrong, and the answer in
every case is that our record does not change.

Each case goes through the real HTTP endpoint, with a real Payment row created by
a real checkout, and payloads signed exactly as the provider would sign them. The
signing helpers recompute the digests independently of the adapters.
"""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.payments.models import Payment, PaymentStatus, PaymentWebhookEvent
from tests.conftest import PAYMOB_TEST_CONFIG

pytestmark = pytest.mark.django_db

PAYMOB_URL = "/api/v1/payments/webhooks/paymob/"


# ---------------------------------------------------------------------------
# Signing helpers — independent of the adapters under test
# ---------------------------------------------------------------------------


def paymob_transaction(payment, **overrides):
    transaction = {
        "id": 210000123,
        "amount_cents": int(payment.amount.amount * 100),
        "created_at": timezone.now().isoformat(),
        "currency": str(payment.amount.currency),
        "error_occured": False,
        "has_parent_transaction": False,
        "integration_id": 4001,
        "is_3d_secure": True,
        "is_auth": False,
        "is_capture": False,
        "is_refunded": False,
        "is_standalone_payment": True,
        "is_voided": False,
        "order": {"id": 500123, "merchant_order_id": payment.merchant_reference},
        "owner": 99001,
        "pending": False,
        "source_data": {"pan": "2346", "sub_type": "MasterCard", "type": "card"},
        "success": True,
    }
    transaction.update(overrides)
    return transaction


def paymob_signature(transaction, secret=PAYMOB_TEST_CONFIG["HMAC_SECRET"]):
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
    return hmac.new(
        secret.encode(), "".join(render(v) for v in ordered).encode(), hashlib.sha512
    ).hexdigest()


def post_paymob(client, transaction, *, signature=None):
    body = {"type": "TRANSACTION", "obj": transaction}
    digest = signature if signature is not None else paymob_signature(transaction)
    return client.post(
        f"{PAYMOB_URL}?hmac={digest}",
        data=json.dumps(body),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# The happy paths
# ---------------------------------------------------------------------------


def test_signed_paymob_capture_settles_the_payment_and_confirms_the_order(
    api_client, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    response = post_paymob(api_client, paymob_transaction(payment))

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    payment.order.refresh_from_db()
    assert payment.order.status == "confirmed"


def test_paymob_failed_transaction_is_applied(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    response = post_paymob(api_client, paymob_transaction(payment, success=False))

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"is_voided": True}, PaymentStatus.CANCELLED),
        ({"is_refunded": True}, PaymentStatus.REFUNDED),
    ],
)
def test_paymob_terminal_outcomes_are_applied(
    api_client, gateways_enabled, gateway_payment, overrides, expected
):
    """
    A voided or already-refunded transaction. Both report `success: true`, so this
    also guards the ordering inside `classify_transaction`: terminal wins.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.CAPTURED)

    response = post_paymob(api_client, paymob_transaction(payment, **overrides))

    payment.refresh_from_db()
    if expected == PaymentStatus.REFUNDED:
        assert response.status_code == 200
        assert payment.status == PaymentStatus.REFUNDED
    else:
        # captured -> cancelled is not a legal move: the money is already ours, so
        # a void arriving afterwards is out of order and must be refused.
        assert response.status_code == 202
        assert payment.status == PaymentStatus.CAPTURED


def test_paymob_pending_transaction_leaves_the_customer_waiting(
    api_client, gateways_enabled, gateway_payment
):
    """A delayed payment. The order must not be confirmed."""
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.PENDING)

    response = post_paymob(api_client, paymob_transaction(payment, pending=True, success=False))

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION
    payment.order.refresh_from_db()
    assert payment.order.status == "pending"


# ---------------------------------------------------------------------------
# Signature failures — the ones that matter most
# ---------------------------------------------------------------------------


def test_unsigned_paymob_webhook_is_refused(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    response = api_client.post(
        PAYMOB_URL,
        data=json.dumps({"obj": paymob_transaction(payment)}),
        content_type="application/json",
    )

    assert response.status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_forged_paymob_signature_is_refused(api_client, gateways_enabled, gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    response = post_paymob(api_client, paymob_transaction(payment), signature="0" * 128)

    assert response.status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_signature_from_a_different_secret_is_refused(
    api_client, gateways_enabled, gateway_payment
):
    """An attacker who knows the algorithm but not the secret still gets nowhere."""
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment)

    response = post_paymob(
        api_client, transaction, signature=paymob_signature(transaction, secret="WRONG-SECRET")
    )

    assert response.status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_an_unsigned_event_records_nothing(api_client, gateways_enabled, gateway_payment):
    """
    Nothing is written for a rejected signature — otherwise anybody could claim a
    `provider_event_id` and, because that column is unique, block the genuine
    event that arrives afterwards. A denial of service on our own settlement.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment)

    post_paymob(api_client, transaction, signature="0" * 128)
    assert PaymentWebhookEvent.objects.count() == 0

    # And the genuine event still lands.
    assert post_paymob(api_client, transaction).status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_raising_the_amount_on_a_genuine_paymob_callback_is_refused(
    api_client, gateways_enabled, gateway_payment
):
    """
    The classic attack: replay a real callback with a bigger amount, or a real
    small payment's signature against a big order. Either way the HMAC covers
    amount_cents, so it stops matching.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    genuine = paymob_transaction(payment)
    signature = paymob_signature(genuine)
    tampered = paymob_transaction(payment, amount_cents=1)

    response = post_paymob(api_client, tampered, signature=signature)

    assert response.status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


# ---------------------------------------------------------------------------
# Content checks on correctly-signed events
# ---------------------------------------------------------------------------


def test_amount_mismatch_on_a_validly_signed_event_is_refused(
    api_client, gateways_enabled, gateway_payment
):
    """
    Correctly signed and still wrong. This is what a compromised provider key, or
    a Paymob account serving two systems, looks like: the signature verifies and
    the amount is not ours. 409, because it is worth alerting on.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment, amount_cents=1)

    response = post_paymob(api_client, transaction)

    assert response.status_code == 409
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION
    event = PaymentWebhookEvent.objects.get()
    assert event.processed is False
    assert "amount" in event.rejection_reason.lower()


def test_currency_mismatch_on_a_validly_signed_event_is_refused(
    api_client, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment, currency="USD")

    response = post_paymob(api_client, transaction)

    assert response.status_code == 409
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_wrong_merchant_reference_is_refused(api_client, gateways_enabled, gateway_payment):
    """
    Signed, right amount, right currency — but quoting somebody else's order
    reference. Matched by provider_reference, so without this check it would
    settle the wrong payment.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment)
    transaction["order"]["merchant_order_id"] = "ORD-20260101-99999-DEADBEEF"

    response = post_paymob(api_client, transaction)

    assert response.status_code == 409
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_an_event_for_an_unknown_payment_is_recorded_but_not_applied(
    api_client, gateways_enabled, gateway_payment
):
    """
    202: signed, but about nothing we hold. Accepted so the provider stops
    retrying for days over an event that will never apply, and recorded so staff
    can see it happened.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    orphan = paymob_transaction(payment, id=777000111)
    orphan["order"] = {"id": 1, "merchant_order_id": "ORD-NOBODY-00000-00000000"}

    response = post_paymob(api_client, orphan)

    assert response.status_code == 202
    event = PaymentWebhookEvent.objects.get(provider_event_id="paymob:777000111")
    assert event.processed is False
    assert event.payment is None
    assert "No payment matches" in event.rejection_reason
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION


def test_a_stale_event_is_recorded_but_not_applied(
    api_client, gateways_enabled, gateway_payment, settings
):
    """
    Providers retry for a long time. An event describing a state we moved on from
    weeks ago must not be replayed onto a live payment. (A *success* is the
    exception — it is applied however late; see test_payments_resilience.)
    """
    settings.PAYMENT_WEBHOOK_MAX_AGE_HOURS = 24
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    old = (timezone.now() - timedelta(days=5)).isoformat()

    response = post_paymob(api_client, paymob_transaction(payment, created_at=old, success=False))

    assert response.status_code == 202
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_ACTION
    assert "older than" in PaymentWebhookEvent.objects.get().rejection_reason


# ---------------------------------------------------------------------------
# Duplicates and ordering
# ---------------------------------------------------------------------------


def test_a_duplicate_webhook_is_a_no_op(api_client, gateways_enabled, gateway_payment):
    """
    Providers re-deliver until they see a 200, so this is the *normal* path for a
    second delivery. It must confirm the order once, not twice.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment)

    assert post_paymob(api_client, transaction).status_code == 200
    payment.refresh_from_db()
    capture_count = payment.transactions.filter(transaction_type="capture").count()

    assert post_paymob(api_client, transaction).status_code == 200

    assert PaymentWebhookEvent.objects.filter(provider_event_id="paymob:210000123").count() == 1
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.transactions.filter(transaction_type="capture").count() == capture_count


def test_a_duplicate_webhook_does_not_award_loyalty_points_twice(
    api_client, gateways_enabled, gateway_payment
):
    """
    Order confirmation has side effects beyond the payment. A re-delivered event
    that ran them again would hand out points the customer never earned.
    """
    from apps.loyalty.models import LoyaltyTransaction

    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment)

    post_paymob(api_client, transaction)
    first = LoyaltyTransaction.objects.filter(order=payment.order).count()
    post_paymob(api_client, transaction)

    assert LoyaltyTransaction.objects.filter(order=payment.order).count() == first


def test_an_out_of_order_failure_after_a_capture_is_refused(
    api_client, gateways_enabled, gateway_payment
):
    """
    Providers do not guarantee ordering. A `failed` retry landing after the
    `captured` that superseded it must not lose the fact that we hold the money.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    assert post_paymob(api_client, paymob_transaction(payment)).status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED

    # A different transaction id, so it is not deduplicated — a genuinely
    # separate, stale event.
    late = paymob_transaction(payment, id=210000999, success=False)
    response = post_paymob(api_client, late)

    assert response.status_code == 202
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert "Out-of-order" in PaymentWebhookEvent.objects.get(
        provider_event_id="paymob:210000999"
    ).rejection_reason


def test_webhook_endpoint_rejects_an_unknown_provider(api_client, gateways_enabled):
    assert (
        api_client.post(
            "/api/v1/payments/webhooks/stripe/", data="{}", content_type="application/json"
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "provider", ["cod", "instapay", "manual", "fawry", "valu", "sympl", "aman"]
)
def test_only_paymob_has_a_webhook_endpoint(api_client, gateways_enabled, provider):
    """
    Paymob is the only route that sends us anything.

    Cash on delivery and InstaPay are settled by a person, so a POST claiming to be
    their webhook could only be hostile; Fawry, valU, Sympl and Aman were removed
    entirely. None of them is in the allowlist, which is a stronger guarantee than
    relying on an adapter returning False.
    """
    response = api_client.post(
        f"/api/v1/payments/webhooks/{provider}/", data="{}", content_type="application/json"
    )
    assert response.status_code == 404


def test_webhook_for_a_disabled_gateway_is_accepted_and_changes_nothing(api_client, settings):
    """
    Paymob calling us while our end is switched off. Accepted so it stops
    retrying, logged loudly because the two ends disagree — and nothing settles.
    """
    from tests.conftest import payment_providers_with

    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "ENABLED": False}
    )

    response = api_client.post(
        PAYMOB_URL, data=json.dumps({"obj": {}}), content_type="application/json"
    )

    assert response.status_code == 202
    assert PaymentWebhookEvent.objects.count() == 0


def test_webhook_endpoint_needs_no_authentication(api_client, gateways_enabled, gateway_payment):
    """
    By necessity: the caller is Paymob, not a user. Which is exactly why the
    signature is the whole security boundary.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)

    response = post_paymob(api_client, paymob_transaction(payment))

    assert response.status_code == 200


@pytest.mark.parametrize("body", ["", "not json", "[]", '{"obj": null}'])
def test_malformed_webhook_bodies_are_refused(api_client, gateways_enabled, body):
    response = api_client.post(
        f"{PAYMOB_URL}?hmac=abc", data=body, content_type="application/json"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# What gets stored
# ---------------------------------------------------------------------------


def test_stored_webhook_payload_has_secrets_redacted(
    api_client, gateways_enabled, gateway_payment
):
    """
    A stored HMAC is a forgery oracle, and a stored PAN is card data. Both are
    redacted before the payload becomes a database row.
    """
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    transaction = paymob_transaction(payment)

    post_paymob(api_client, transaction)

    stored = json.dumps(PaymentWebhookEvent.objects.get().payload)
    assert "2346" not in stored  # the PAN
    assert paymob_signature(transaction) not in stored
    assert PAYMOB_TEST_CONFIG["HMAC_SECRET"] not in stored


def test_stored_transaction_response_never_contains_a_credential(
    gateways_enabled, gateway_payment, monkeypatch
):
    """The same rule for outbound calls: a provider echoing our key back must not persist it."""
    from apps.payments.providers import get_payment_provider

    payment = gateway_payment(provider="paymob", method="card")
    payment.method = "card"

    class Response:
        status_code = 201
        content = json.dumps(
            {
                "id": "int_1",
                "client_secret": "cs_1",
                "secret_key": PAYMOB_TEST_CONFIG["SECRET_KEY"],
                "api_key": PAYMOB_TEST_CONFIG["API_KEY"],
            }
        ).encode()

    monkeypatch.setattr(
        "apps.payments.providers.http.requests.request", lambda *a, **k: Response()
    )
    result = get_payment_provider("paymob").create_payment(payment=payment, order=payment.order)

    from apps.payments.services import _redact

    stored = json.dumps(_redact(result.raw))
    assert PAYMOB_TEST_CONFIG["SECRET_KEY"] not in stored
    assert PAYMOB_TEST_CONFIG["API_KEY"] not in stored


def test_webhook_event_records_the_provider_reference_even_when_unmatched(
    api_client, gateways_enabled, gateway_payment
):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    orphan = paymob_transaction(payment, id=777000222)
    orphan["order"] = {"id": 1, "merchant_order_id": "ORD-NOBODY-00000-00000000"}

    post_paymob(api_client, orphan)

    event = PaymentWebhookEvent.objects.get(provider_event_id="paymob:777000222")
    assert event.provider_reference == "777000222"
    assert event.signature_valid is True


# ---------------------------------------------------------------------------
# Amount arithmetic
# ---------------------------------------------------------------------------


def test_cents_conversion_is_exact_for_awkward_amounts(gateways_enabled, gateway_payment):
    """
    Money in minor units is where rounding bugs become undercharges. 250.05 EGP
    must be 25005 cents, not 25004.
    """
    from apps.payments.providers.paymob import PaymobPaymentProvider

    assert PaymobPaymentProvider._to_cents(Decimal("250.05")) == 25005
    assert PaymobPaymentProvider._to_cents(Decimal("0.01")) == 1
    assert PaymobPaymentProvider._to_cents(Decimal("1000")) == 100000
    # Quantised before conversion, so a stray third decimal cannot truncate.
    assert PaymobPaymentProvider._to_cents(Decimal("10.005")) == 1001


def test_a_webhook_cannot_settle_a_payment_for_a_different_order(
    api_client, gateways_enabled, gateway_payment
):
    """
    Two live payments, one event. It must settle exactly the one it names — the
    check that stops a cheap order's genuine callback paying for an expensive one.
    """
    cheap = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION, quantity=1)
    expensive = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION, quantity=3)
    Payment.objects.filter(pk=expensive.pk).update(provider_reference="210000888")
    expensive.refresh_from_db()

    post_paymob(api_client, paymob_transaction(cheap))

    cheap.refresh_from_db()
    expensive.refresh_from_db()
    assert cheap.status == PaymentStatus.CAPTURED
    assert expensive.status == PaymentStatus.REQUIRES_ACTION
