"""
Vodafone Cash as a transfer to the shop's wallet, confirmed by staff.

The same contract as InstaPay, and the same risk: nothing verifies the money but
a person, so an order paid this way stays unconfirmed — stock reserved, not
consumed, not shipped — until somebody records the transfer as received. And the
route steps aside on its own once Paymob's wallet integration is configured.
"""
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.payments import config as payment_config
from apps.payments.models import Payment, PaymentStatus
from tests.conftest import PAYMOB_TEST_CONFIG, payment_providers_with
from tests.test_guest_checkout import _guest_body, _guest_with_cart, _place

pytestmark = pytest.mark.django_db

API = "/api/v1"
WALLET = {"ENABLED": True, "NUMBER": "010 1234 5678", "ACCOUNT_NAME": "شركة الريف", "CONFIRM_WINDOW_HOURS": 24}


@pytest.fixture
def wallet_on(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(VODAFONE_CASH=WALLET)
    settings.FRONTEND_URL = "https://shop.example.test"
    return settings


def _checkout(client, variant, address, shipping_setup, quantity=1):
    client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": quantity}, format="json")
    return client.post(
        f"{API}/checkout/",
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "mobile_wallet"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


@pytest.fixture
def wallet_order(wallet_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup, 2)
    assert response.status_code == 201, response.content
    return response.json()["data"]


# ---------------------------------------------------------------------------
# Offered only when it can work, and only when Paymob is not doing the job
# ---------------------------------------------------------------------------


def _offered(client):
    return {m["code"]: m for m in client.get(f"{API}/payments/methods/").json()["data"]}


def test_not_offered_until_switched_on(api_client, settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(VODAFONE_CASH={**WALLET, "ENABLED": False})
    assert "mobile_wallet" not in _offered(api_client)


def test_not_offered_without_a_wallet_number(api_client, settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(VODAFONE_CASH={**WALLET, "NUMBER": " "})
    assert "mobile_wallet" not in _offered(api_client)
    assert "NUMBER" in payment_config.availability("mobile_wallet").reason


def test_offered_as_vodafone_cash_without_a_redirect(api_client, wallet_on):
    method = _offered(api_client)["mobile_wallet"]
    assert method["label_ar"] == "فودافون كاش"
    assert method["redirects"] is False
    assert payment_config.gateway_for_method("mobile_wallet") == "vodafone_cash"


def test_paymob_takes_over_the_method_once_configured(api_client, settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(VODAFONE_CASH=WALLET, PAYMOB=PAYMOB_TEST_CONFIG)
    method = _offered(api_client)["mobile_wallet"]
    assert method["redirects"] is True
    assert payment_config.gateway_for_method("mobile_wallet") == "paymob"
    # Offered once, not twice.
    codes = [m["code"] for m in api_client.get(f"{API}/payments/methods/").json()["data"]]
    assert codes.count("mobile_wallet") == 1


# ---------------------------------------------------------------------------
# Checkout: the customer is told exactly what to send, and nothing is confirmed
# ---------------------------------------------------------------------------


def test_checkout_hands_over_the_wallet_number_amount_and_reference(wallet_order):
    payment, order = wallet_order["payment"], wallet_order["order"]
    assert payment["provider"] == "vodafone_cash"
    assert payment["status"] == PaymentStatus.REQUIRES_ACTION
    assert payment["pay_to"] == "01012345678"  # spaces dropped: it is copied into a phone
    assert payment["pay_reference"] == order["order_number"]
    assert "01012345678" in payment["instructions_ar"]
    assert order["order_number"] in payment["instructions_ar"]
    assert order["status"] == "pending"


def test_stock_is_reserved_not_consumed_until_staff_confirm(wallet_order, product_with_variant_and_stock):
    batch = product_with_variant_and_stock["batch"]
    batch.refresh_from_db()
    assert batch.quantity == 10
    assert product_with_variant_and_stock["item"].available_quantity == 8


def test_the_transfer_details_are_emailed(
    wallet_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    with mock.patch("apps.notifications.tasks.send_email_task.delay") as send, \
            django_capture_on_commit_callbacks(execute=True):
        response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)
    assert response.status_code == 201
    calls = [c.kwargs for c in send.call_args_list if c.kwargs.get("template_name") == "payment_instructions"]
    assert len(calls) == 1
    context = calls[0]["context"]
    assert context["route"] == "vodafone_cash"
    assert context["pay_to"] == "01012345678"
    assert context["reference"] == response.json()["data"]["order"]["order_number"]

    from django.template.loader import render_to_string

    html = render_to_string("emails/payment_instructions.html", context)
    assert "فودافون كاش" in html and "01012345678" in html


def test_the_order_page_keeps_showing_where_to_pay(wallet_order, authenticated_client):
    number = wallet_order["order"]["order_number"]
    payment = authenticated_client.get(f"{API}/orders/{number}/").json()["data"]["payment"]
    assert payment["provider"] == "vodafone_cash"
    assert payment["pay_to"] == "01012345678"


def test_a_guest_can_pay_by_vodafone_cash(wallet_on, product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"], payment="mobile_wallet"))
    assert response.status_code == 201, response.content
    assert response.json()["data"]["payment"]["pay_to"] == "01012345678"


# ---------------------------------------------------------------------------
# Staff decide
# ---------------------------------------------------------------------------


def _settle(staff_client, payment_id, outcome):
    return staff_client.post(f"{API}/admin-api/payments/{payment_id}/settle/", {"outcome": outcome}, format="json")


def test_staff_confirming_the_transfer_pays_and_confirms_the_order(
    wallet_order, staff_client, product_with_variant_and_stock
):
    response = _settle(staff_client, wallet_order["payment"]["id"], "received")

    assert response.status_code == 200
    payment = Payment.objects.get(pk=wallet_order["payment"]["id"])
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == "confirmed"
    batch = product_with_variant_and_stock["batch"]
    batch.refresh_from_db()
    assert batch.quantity == 8  # now it leaves the shelf

    from apps.audit.models import AuditLog

    assert AuditLog.objects.filter(
        model_name="Payment", object_id=str(payment.pk), changes__method="vodafone_cash_confirmation"
    ).exists()


def test_staff_recording_no_transfer_fails_the_payment(wallet_order, staff_client):
    _settle(staff_client, wallet_order["payment"]["id"], "refused")
    payment = Payment.objects.get(pk=wallet_order["payment"]["id"])
    assert payment.status == PaymentStatus.FAILED
    assert payment.order.status == "pending"


def test_a_customer_cannot_settle_their_own_transfer(wallet_order, authenticated_client):
    response = _settle(authenticated_client, wallet_order["payment"]["id"], "received")
    assert response.status_code in (401, 403)
    assert Payment.objects.get(pk=wallet_order["payment"]["id"]).status == PaymentStatus.REQUIRES_ACTION


def test_a_refund_is_a_manual_transfer_back_and_is_recorded(wallet_order, staff_client):
    from djmoney.money import Money

    from apps.payments.services import create_refund

    _settle(staff_client, wallet_order["payment"]["id"], "received")
    payment = Payment.objects.get(pk=wallet_order["payment"]["id"])
    can_refund, note = payment_config.refund_available("mobile_wallet")
    assert can_refund and "فودافون كاش" in note

    refund = create_refund(payment=payment, amount=Money(payment.amount.amount, "EGP"), reason="رجّع", idempotency_key="v1")
    assert refund.status == "processed" and refund.provider_refund_id.startswith("MANUAL-VFC-")


def test_still_recorded_after_the_route_is_switched_off(wallet_order, staff_client, settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(VODAFONE_CASH={**WALLET, "ENABLED": False})
    _settle(staff_client, wallet_order["payment"]["id"], "received")
    assert Payment.objects.get(pk=wallet_order["payment"]["id"]).status == PaymentStatus.CAPTURED


# ---------------------------------------------------------------------------
# Nothing outside a person can move it
# ---------------------------------------------------------------------------


def test_there_is_no_webhook_for_it(api_client, wallet_on):
    response = api_client.post(f"{API}/payments/webhooks/vodafone_cash/", {"paid": True}, format="json")
    assert response.status_code == 404


def test_the_customer_can_cancel_while_it_is_unpaid(wallet_order, authenticated_client):
    number = wallet_order["order"]["order_number"]
    response = authenticated_client.post(f"{API}/orders/{number}/cancel/", {"reason": "غيرت رأيي"}, format="json")
    assert response.status_code == 200


def test_the_unpaid_order_sweep_leaves_it_for_staff(wallet_order):
    from apps.orders.models import Order
    from apps.payments.tasks import release_unpaid_orders_task

    Order.objects.filter(order_number=wallet_order["order"]["order_number"]).update(
        created_at=timezone.now() - timedelta(days=3)
    )
    release_unpaid_orders_task()
    assert Order.objects.get(order_number=wallet_order["order"]["order_number"]).status == "pending"


def test_an_abandoned_transfer_expires_after_two_weeks(wallet_order):
    from apps.payments.tasks import expire_stale_payments_task

    Payment.objects.filter(pk=wallet_order["payment"]["id"]).update(created_at=timezone.now() - timedelta(days=15))
    expire_stale_payments_task()
    assert Payment.objects.get(pk=wallet_order["payment"]["id"]).status == PaymentStatus.EXPIRED


def test_staff_see_the_route_and_its_settings(staff_client, settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(VODAFONE_CASH={**WALLET, "NUMBER": ""})
    methods = staff_client.get(f"{API}/admin-api/payment-methods/").json()["data"]
    wallet = next(m for m in methods["methods"] if m["code"] == "mobile_wallet") if isinstance(methods, dict) \
        else next(m for m in methods if m["code"] == "mobile_wallet")
    assert wallet["available"] is False
    assert "NUMBER" in wallet["missing_settings"]
