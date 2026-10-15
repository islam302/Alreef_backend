"""
The Paymob adapter's server-to-server calls (void, refund, inquiry) and its
parsing edge cases. The HTTP client is replaced with a scripted stub, so no
request ever leaves the test.
"""
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from djmoney.money import Money

from apps.payments.providers import get_payment_provider
from apps.payments.providers.http import GatewayError
from apps.payments.providers.paymob import PaymobPaymentProvider, _dig, _hmac_value, _parse_event_time


class StubClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, path, **kw):
        self.calls.append((path, kw))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _provider(client):
    provider = get_payment_provider("paymob")
    provider.client = client
    return provider


def _payment(reference="555"):
    return SimpleNamespace(id=1, provider_reference=reference, merchant_reference="ORD-1-ABC")


def test_helpers():
    assert _dig({"a": {"b": 1}}, "a.b") == 1
    assert _dig({"a": 3}, "a.b") is None
    assert _hmac_value(None) == ""
    assert _hmac_value(True) == "true" and _hmac_value(False) == "false"
    assert _parse_event_time("") is None
    assert _parse_event_time("not a date") is None
    assert _parse_event_time("2026-01-01T10:00:00").tzinfo is not None
    assert _parse_event_time("2026-01-01T10:00:00+02:00").utcoffset().total_seconds() == 7200


def test_capture_and_authorize_are_not_supported(gateways_enabled):
    provider = _provider(StubClient())
    with pytest.raises(NotImplementedError):
        provider.capture(payment=_payment())
    with pytest.raises(NotImplementedError):
        provider.authorize(payment=_payment())


def test_cancel_paths(gateways_enabled):
    assert _provider(StubClient()).cancel(payment=_payment(reference="")) is False
    ok = StubClient({"token": "t"}, {})
    assert _provider(ok).cancel(payment=_payment()) is True
    assert ok.calls[1][1]["params"] == {"token": "t"}
    failing = StubClient({"token": "t"}, GatewayError("nope", status_code=400))
    assert _provider(failing).cancel(payment=_payment()) is False


def test_auth_token_missing_raises(gateways_enabled):
    with pytest.raises(GatewayError):
        _provider(StubClient({})).cancel(payment=_payment())


def test_refund_auth_failure_is_a_clean_no(gateways_enabled):
    result = _provider(StubClient(GatewayError("auth down", status_code=500))).refund(
        payment=_payment(), amount=Money(Decimal("10"), "EGP")
    )
    assert result.success is False and "auth down" in result.error


def test_refund_rejected_with_status_code(gateways_enabled):
    client = StubClient({"token": "t"}, GatewayError("rejected", status_code=400, payload={"x": 1}))
    result = _provider(client).refund(payment=_payment(), amount=Money(Decimal("10"), "EGP"))
    assert result.success is False and result.raw == {"x": 1}


def test_refund_timeout_propagates(gateways_enabled):
    client = StubClient({"token": "t"}, GatewayError("timeout"))
    with pytest.raises(GatewayError):
        _provider(client).refund(payment=_payment(), amount=Money(Decimal("10"), "EGP"))


@pytest.mark.parametrize("response", [{"success": False}, {"pending": True}])
def test_refund_not_accepted(gateways_enabled, response):
    client = StubClient({"token": "t"}, response)
    result = _provider(client).refund(payment=_payment(), amount=Money(Decimal("10"), "EGP"))
    assert result.success is False


def test_refund_accepted(gateways_enabled):
    client = StubClient({"token": "t"}, {"success": True, "id": 99})
    result = _provider(client).refund(payment=_payment(), amount=Money(Decimal("10.50"), "EGP"))
    assert result.success and result.provider_refund_id == "99"
    assert client.calls[1][1]["json_body"]["amount_cents"] == 1050


def test_get_status_inquires_by_merchant_reference(gateways_enabled):
    client = StubClient({"token": "t"}, {"success": True, "is_auth": True, "is_capture": False})
    result = _provider(client).get_status(payment=_payment())
    assert result.status == "authorized"
    assert client.calls[1][1]["json_body"] == {"merchant_order_id": "ORD-1-ABC"}


def test_classify_non_dict_is_failed():
    assert PaymobPaymentProvider.classify_transaction(None) == "failed"


def test_verify_webhook_without_secret(gateways_enabled, settings):
    provider = _provider(StubClient())
    provider.config = {**provider.config, "HMAC_SECRET": ""}
    assert provider.verify_webhook(headers={}, body=b"{}", query_params={}) is False


def test_verify_webhook_non_dict_body(gateways_enabled):
    assert _provider(StubClient()).verify_webhook(headers={}, body=b"[1,2]", query_params={}) is False


def test_parse_webhook_with_bad_amount_and_data(gateways_enabled):
    body = json.dumps({"obj": {"id": 7, "amount_cents": "x", "data": {"message": "Declined"}, "success": False}}).encode()
    parsed = _provider(StubClient()).parse_webhook_event(headers={}, body=body, query_params={})
    assert parsed["amount"] is None
    assert parsed["failure_reason"] == "Declined"
    assert parsed["status"] == "failed"
    assert parsed["merchant_reference"] == ""
