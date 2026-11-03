"""
Cash on delivery with a deposit.

A COD order asks for a deposit (COD_DEPOSIT_AMOUNT, 100 EGP) sent by Vodafone
Cash or InstaPay before it is confirmed. The deposit is part of the total, not
added to it: the courier collects what is left. Staff confirm the transfer in
the dashboard, and that is what confirms the order. With neither transfer route
configured, no deposit is asked for and COD works as it always did.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.orders.models import Order, OrderStatus
from apps.payments.models import Payment, PaymentStatus
from tests.conftest import INSTAPAY_TEST_CONFIG, payment_providers_with
from tests.test_guest_checkout import _guest_body, _guest_with_cart, _place

pytestmark = pytest.mark.django_db

API = "/api/v1"
WALLET = {"ENABLED": True, "NUMBER": "010 1234 5678", "ACCOUNT_NAME": "شركة الريف", "CONFIRM_WINDOW_HOURS": 24}


def _providers(**overrides):
    from django.conf import settings

    cod = {**settings.PAYMENT_PROVIDERS["COD"], "DEPOSIT_AMOUNT": "100.00"}
    return payment_providers_with(COD=cod, **overrides)


@pytest.fixture
def deposit_on(settings):
    settings.PAYMENT_PROVIDERS = _providers(VODAFONE_CASH=WALLET, INSTAPAY=INSTAPAY_TEST_CONFIG)
    settings.FRONTEND_URL = "https://shop.example.test"
    return settings


def _cod_order(product_with_variant_and_stock, governorate, shipping_setup, **extra):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"], **extra))
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    return Order.objects.get(order_number=data["order"]["order_number"]), data, client


def test_the_methods_endpoint_announces_the_deposit(api_client, deposit_on):
    cod = next(m for m in api_client.get(f"{API}/payments/methods/").json()["data"] if m["code"] == "cod")
    assert cod["deposit_amount"] == "100.00"
    assert [r["code"] for r in cod["deposit_routes"]] == ["vodafone_cash", "instapay"]


def test_no_route_configured_means_no_deposit(settings, api_client, product_with_variant_and_stock, governorate, shipping_setup):
    settings.PAYMENT_PROVIDERS = _providers()
    cod = next(m for m in api_client.get(f"{API}/payments/methods/").json()["data"] if m["code"] == "cod")
    assert cod["deposit_amount"] is None and cod["deposit_routes"] == []

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    assert order.status == OrderStatus.CONFIRMED  # as before deposits
    assert list(order.payments.values_list("provider", flat=True)) == ["cod"]
    assert order.payments.get().amount.amount == order.grand_total.amount


def test_summary_splits_the_total(deposit_on, product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    totals = client.post(
        f"{API}/checkout/summary/",
        {"governorate": governorate.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"},
        format="json",
    ).json()["data"]
    assert totals["deposit_amount"] == "100.00"
    assert Decimal(totals["due_on_delivery"]) == Decimal(totals["grand_total"]) - 100


def test_a_cod_order_waits_for_its_deposit(deposit_on, product_with_variant_and_stock, governorate, shipping_setup):
    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)

    assert order.status == OrderStatus.PENDING
    deposit = Payment.objects.get(pk=data["payment"]["id"])
    cod = order.payments.get(provider="cod")
    assert deposit.provider == "vodafone_cash" and deposit.status == PaymentStatus.REQUIRES_ACTION
    assert deposit.metadata["role"] == "deposit"
    # Part of the total, never on top of it.
    assert deposit.amount.amount == Decimal("100.00")
    assert cod.amount.amount + deposit.amount.amount == order.grand_total.amount
    # What the customer is shown: the deposit, where to send it, what is left.
    payment = data["payment"]
    assert payment["role"] == "deposit"
    assert payment["pay_to"] == "01012345678"  # the wallet number, normalised
    assert Decimal(payment["due_on_delivery"]) == cod.amount.amount
    assert "دفعة تأكيد 100.00" in payment["instructions_ar"]


def test_the_customer_picks_instapay(deposit_on, product_with_variant_and_stock, governorate, shipping_setup):
    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup, deposit_route="instapay")
    assert Payment.objects.get(pk=data["payment"]["id"]).provider == "instapay"


def test_an_unavailable_route_is_refused(settings, product_with_variant_and_stock, governorate, shipping_setup):
    settings.PAYMENT_PROVIDERS = _providers(VODAFONE_CASH=WALLET)
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"], deposit_route="instapay"))
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_the_deposit_email_names_the_rest(deposit_on, product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks):
    from unittest import mock

    with mock.patch("apps.notifications.tasks.send_email_task.delay") as send, django_capture_on_commit_callbacks(execute=True):
        order, _, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    mail = next(c.kwargs for c in send.call_args_list if c.kwargs.get("template_name") == "payment_instructions")
    assert mail["context"]["amount"] == "100.00"
    assert Decimal(mail["context"]["due_on_delivery"]) == order.grand_total.amount - 100


def test_staff_confirming_the_deposit_confirms_the_order(
    deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks
):
    from unittest import mock

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    with mock.patch("apps.notifications.tasks.send_email_task.delay") as send, django_capture_on_commit_callbacks(execute=True):
        response = staff_client.post(f"{API}/admin-api/payments/{data['payment']['id']}/settle/", {"outcome": "received"}, format="json")
    assert response.status_code == 200, response.content

    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert order.payments.get(provider="cod").status == PaymentStatus.PENDING  # still collected at the door
    templates = [c.kwargs.get("template_name") for c in send.call_args_list]
    # Confirmed, but not "paid": the rest is still to come.
    assert "payment_confirmation" not in templates


def test_settling_the_deposit_twice_captures_once(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    url = f"{API}/admin-api/payments/{data['payment']['id']}/settle/"
    assert staff_client.post(url, {"outcome": "received"}, format="json").status_code == 200
    assert staff_client.post(url, {"outcome": "received"}, format="json").status_code == 200
    deposit = Payment.objects.get(pk=data["payment"]["id"])
    assert deposit.transactions.filter(transaction_type="capture").count() == 1


def test_delivery_collects_only_what_is_left(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    from apps.orders.services import transition_status

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    staff_client.post(f"{API}/admin-api/payments/{data['payment']['id']}/settle/", {"outcome": "received"}, format="json")
    order.refresh_from_db()
    for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED):
        order = transition_status(order, status)

    cod = order.payments.get(provider="cod")
    assert cod.status == PaymentStatus.CAPTURED
    captured = sum(p.amount.amount for p in order.payments.filter(status=PaymentStatus.CAPTURED))
    assert captured == order.grand_total.amount  # deposit + cash = the total, exactly once


def test_cancelling_after_the_deposit_flags_a_refund(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    from apps.orders.services import cancel_order

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    staff_client.post(f"{API}/admin-api/payments/{data['payment']['id']}/settle/", {"outcome": "received"}, format="json")
    order.refresh_from_db()
    cancel_order(order, reason="العميل لغى")

    deposit = Payment.objects.get(pk=data["payment"]["id"])
    assert deposit.metadata.get("needs_refund") is True


def test_a_customer_cannot_cancel_once_the_deposit_is_in(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    from apps.core.error_codes import ApplicationError
    from apps.payments.services import assert_customer_may_cancel

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    assert_customer_may_cancel(order)  # nothing received yet: free to cancel
    staff_client.post(f"{API}/admin-api/payments/{data['payment']['id']}/settle/", {"outcome": "received"}, format="json")
    with pytest.raises(ApplicationError):
        assert_customer_may_cancel(order)


def test_the_order_page_shows_the_deposit_then_the_rest(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    from apps.orders.serializers import payment_summary

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    summary = payment_summary(order)
    assert summary["role"] == "deposit" and summary["status"] == "requires_action"
    assert summary["deposit"]["amount"] == "100.00"

    staff_client.post(f"{API}/admin-api/payments/{data['payment']['id']}/settle/", {"outcome": "received"}, format="json")
    summary = payment_summary(order)
    assert summary["provider"] == "cod"
    assert summary["deposit"]["status"] == "captured"


def test_a_cancelled_transfer_order_is_flagged_too(settings, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    """Not only deposits: a full InstaPay transfer confirmed and then cancelled used to be left unflagged."""
    from apps.orders.services import cancel_order

    settings.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup, payment="instapay")
    staff_client.post(f"{API}/admin-api/payments/{data['payment']['id']}/settle/", {"outcome": "received"}, format="json")
    order.refresh_from_db()
    cancel_order(order, reason="x")
    assert Payment.objects.get(pk=data["payment"]["id"]).metadata.get("needs_refund") is True


def test_a_small_order_never_asks_for_more_than_it_costs(settings, deposit_on, product_with_variant_and_stock, governorate, shipping_setup):
    cod = {**settings.PAYMENT_PROVIDERS["COD"], "DEPOSIT_AMOUNT": "100000"}
    settings.PAYMENT_PROVIDERS = {**settings.PAYMENT_PROVIDERS, "COD": cod}
    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    deposit = Payment.objects.get(pk=data["payment"]["id"])
    assert deposit.amount.amount == order.grand_total.amount
    assert order.payments.get(provider="cod").amount.amount == 0
    assert timezone.now()  # placed without error
