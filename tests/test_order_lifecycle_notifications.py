"""
What happens around an order besides the order itself: the coupon use it spends,
the emails it sends, and the SMS codes that must not end up in a log.

Each test here failed before the change it guards:

* A coupon's use was recorded only when the order was confirmed, so pending
  card/InstaPay orders were invisible to the limit and a single-use code could
  be spent by every customer who started paying at once.
* Emails were queued inside the checkout transaction: a rolled-back order still
  sent "confirmed", and a broker outage failed the checkout itself.
* Subjects were English; staff cancelling an order sent nothing; a COD order
  was "confirmed" a second time when its cash was collected.
* The console SMS provider logged OTP codes, and production answered "OTP sent"
  with no way to send one.
"""
import uuid
from decimal import Decimal
from unittest import mock

import pytest
from django.core import mail
from django.db import transaction

from apps.coupons.models import Coupon, CouponDiscountType, CouponUsage
from apps.orders.models import Order, OrderStatus
from apps.users.models import Address
from tests.conftest import INSTAPAY_TEST_CONFIG, payment_providers_with

pytestmark = pytest.mark.django_db

CHECKOUT_URL = "/api/v1/checkout/"
CART_ITEMS_URL = "/api/v1/cart/items/"
SEND = "apps.notifications.tasks.send_email_task.delay"


def _checkout(client, variant, address, shipping_setup, *, method="cod", coupon_code="", quantity=1):
    client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": quantity}, format="json")
    return client.post(
        CHECKOUT_URL,
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": method,
            "coupon_code": coupon_code,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


@pytest.fixture
def instapay(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    return settings


@pytest.fixture
def other_address(other_user, governorate):
    return Address.objects.create(
        user=other_user, full_name="Mallory Other", phone="+201009876543", governorate=governorate,
        street="9 Other St", is_default=True,
    )


def _single_use_coupon(**extra):
    return Coupon.objects.create(
        code="ONCE10", discount_type=CouponDiscountType.FIXED_AMOUNT, fixed_amount_value=Decimal("10.00"),
        is_active=True, usage_limit=1, **extra,
    )


# ---------------------------------------------------------------------------
# 1. Coupon uses are reserved at checkout and released on cancel
# ---------------------------------------------------------------------------


def test_a_pending_online_order_holds_the_last_use_of_a_coupon(
    instapay, authenticated_client, other_client, product_with_variant_and_stock, address, other_address,
    shipping_setup,
):
    coupon = _single_use_coupon()
    variant = product_with_variant_and_stock["variant"]

    first = _checkout(authenticated_client, variant, address, shipping_setup, method="instapay", coupon_code="ONCE10")
    assert first.status_code == 201, first.content
    assert first.json()["data"]["order"]["status"] == OrderStatus.PENDING
    order = Order.objects.get(order_number=first.json()["data"]["order"]["order_number"])
    assert CouponUsage.objects.filter(coupon=coupon, order=order).count() == 1

    # A second customer starting to pay with the same single-use code is refused,
    # even though the first one has not paid yet.
    second = _checkout(other_client, variant, other_address, shipping_setup, method="instapay", coupon_code="ONCE10")
    assert second.status_code == 409, second.content
    assert second.json()["errors"]["code"] == "COUPON_USAGE_LIMIT_REACHED"


def test_the_per_customer_limit_counts_orders_still_being_paid_for(
    instapay, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    Coupon.objects.create(
        code="MINE", discount_type=CouponDiscountType.FIXED_AMOUNT, fixed_amount_value=Decimal("10.00"),
        is_active=True, per_user_limit=1,
    )
    variant = product_with_variant_and_stock["variant"]
    assert _checkout(authenticated_client, variant, address, shipping_setup, method="instapay", coupon_code="MINE").status_code == 201

    again = _checkout(authenticated_client, variant, address, shipping_setup, method="instapay", coupon_code="MINE")
    assert again.status_code == 409


def test_cancelling_the_order_gives_the_use_back(
    instapay, authenticated_client, other_client, product_with_variant_and_stock, address, other_address,
    shipping_setup,
):
    from apps.orders.services import cancel_order

    coupon = _single_use_coupon()
    variant = product_with_variant_and_stock["variant"]
    first = _checkout(authenticated_client, variant, address, shipping_setup, method="instapay", coupon_code="ONCE10")
    order = Order.objects.get(order_number=first.json()["data"]["order"]["order_number"])
    assert CouponUsage.objects.filter(coupon=coupon, order=order).count() == 1

    cancel_order(order, reason="Customer changed their mind")
    assert not CouponUsage.objects.filter(coupon=coupon).exists()

    second = _checkout(other_client, variant, other_address, shipping_setup, method="instapay", coupon_code="ONCE10")
    assert second.status_code == 201, second.content


def test_confirmation_does_not_record_the_use_a_second_time(
    instapay, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    from apps.payments.models import Payment
    from apps.payments.services import record_manual_receipt

    coupon = _single_use_coupon()
    variant = product_with_variant_and_stock["variant"]
    response = _checkout(authenticated_client, variant, address, shipping_setup, method="instapay", coupon_code="ONCE10")
    payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])

    record_manual_receipt(payment, note="seen in the app")

    payment.order.refresh_from_db()
    assert payment.order.status == OrderStatus.CONFIRMED
    assert CouponUsage.objects.filter(coupon=coupon).count() == 1


# ---------------------------------------------------------------------------
# 2. Emails leave only after the order is committed
# ---------------------------------------------------------------------------


def test_a_rolled_back_confirmation_sends_no_email(
    instapay, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from apps.checkout.services import confirm_order_after_payment

    response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup, method="instapay")
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])

    with mock.patch(SEND) as send, django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                confirm_order_after_payment(order)
                raise RuntimeError("something later in the same transaction failed")

    assert send.call_count == 0
    order.refresh_from_db()
    assert order.status == OrderStatus.PENDING


def test_a_broker_outage_costs_the_email_not_the_order(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from kombu.exceptions import OperationalError

    with mock.patch(SEND, side_effect=OperationalError("redis is down")), \
            mock.patch("apps.notifications.services.logger") as log, \
            django_capture_on_commit_callbacks(execute=True):
        response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)

    assert response.status_code == 201, response.content
    assert Order.objects.filter(order_number=response.json()["data"]["order"]["order_number"]).exists()
    assert log.exception.called


# ---------------------------------------------------------------------------
# 3 + 4. Arabic emails, sent once, including for a cancellation
# ---------------------------------------------------------------------------


def _templates(send):
    return [c.kwargs.get("template_name") for c in send.call_args_list]


def test_the_confirmation_email_is_arabic_and_right_to_left(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)
    number = response.json()["data"]["order"]["order_number"]

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == f"طلبك اتأكد — رقم {number}"
    html = message.alternatives[0][0]
    assert 'dir="rtl"' in html and 'lang="ar"' in html
    assert number in html
    assert f"/account/orders/{number}" in html
    # The plain-text part is text, not the HTML source.
    assert "<div" not in message.body


def test_a_caller_english_subject_is_replaced_by_the_arabic_one():
    """The payment service still passes "Payment failed"; the customer reads Arabic."""
    from apps.notifications.services import send_email_now

    send_email_now(
        to="a@example.com", subject="Payment failed", template_name="payment_failed",
        context={"order_number": "ORD-1", "amount": "10.00", "currency": "EGP"},
    )
    assert mail.outbox[-1].subject == "الدفع ماكملش — طلب رقم ORD-1"


@pytest.mark.parametrize(
    "template_name",
    [
        "order_confirmation", "order_cancelled", "order_shipped", "order_delivered", "payment_confirmation",
        "payment_failed", "refund", "verify_email", "reset_password", "welcome", "abandoned_cart", "promotion",
    ],
)
def test_every_email_template_renders_arabic_with_a_subject(template_name):
    from django.template.loader import render_to_string

    from apps.notifications.services import EMAIL_SUBJECTS

    html = render_to_string(
        f"emails/{template_name}.html",
        {"order_number": "ORD-1", "frontend_url": "https://shop.test", "site_name": "Alreef"},
    )
    assert 'dir="rtl"' in html
    if template_name != "promotion":  # A campaign brings its own subject.
        assert template_name in EMAIL_SUBJECTS


def test_staff_cancelling_an_order_tells_the_customer_without_the_internal_reason(
    authenticated_client, staff_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)
    number = response.json()["data"]["order"]["order_number"]
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        cancelled = staff_client.post(
            f"/api/v1/admin-api/orders/{number}/cancel/", {"reason": "suspected fake number"}, format="json",
        )
    assert cancelled.status_code == 200, cancelled.content

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == f"طلبك اتلغى — رقم {number}"
    assert message.to == [address.user.email]
    assert "suspected fake number" not in message.alternatives[0][0]


def test_a_cod_order_is_confirmed_by_email_once_even_after_the_cash_is_collected(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from apps.orders.services import transition_status

    with mock.patch(SEND) as send, django_capture_on_commit_callbacks(execute=True):
        response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)
        order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
        # Delivery records the cash collection, which re-runs confirm_order.
        for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            order = transition_status(order, status)

    sent = _templates(send)
    assert sent.count("order_confirmation") == 1
    assert "payment_confirmation" not in sent
    assert sent.count("order_delivered") == 1


def test_staff_confirming_a_transfer_order_by_hand_sends_the_confirmation(
    instapay, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    """Moving PENDING -> CONFIRMED from the dashboard used to send nothing at all."""
    from apps.orders.services import transition_status

    response = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup, method="instapay")
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])

    with mock.patch(SEND) as send, django_capture_on_commit_callbacks(execute=True):
        transition_status(order, OrderStatus.CONFIRMED)

    assert _templates(send) == ["order_confirmation"]


# ---------------------------------------------------------------------------
# 5. One-time codes stay out of logs, and nobody is told a code was sent
#    when it cannot be
# ---------------------------------------------------------------------------


def _logged(log):
    return " ".join(str(arg) for call in log.info.call_args_list for arg in call.args)


def test_the_console_provider_masks_codes_outside_debug(settings):
    from apps.notifications.providers.sms_base import ConsoleSMSProvider

    settings.DEBUG = False
    with mock.patch("apps.notifications.providers.sms_base.logger") as log:
        ConsoleSMSProvider().send(to="+201001234567", message="كود التأكيد بتاعك: 482913")
    assert "482913" not in _logged(log)
    assert "+201001234567" in _logged(log)


def test_the_console_provider_shows_the_code_to_a_developer(settings):
    from apps.notifications.providers.sms_base import ConsoleSMSProvider

    settings.DEBUG = True
    with mock.patch("apps.notifications.providers.sms_base.logger") as log:
        ConsoleSMSProvider().send(to="+201001234567", message="code 482913")
    assert "482913" in _logged(log)


def test_phone_otp_says_unavailable_when_no_sms_would_arrive(settings, authenticated_client, user):
    from apps.users.models import PhoneOTP

    settings.SMS_CONSOLE_IS_DELIVERY = False
    user.phone = "+201001234567"
    user.save(update_fields=["phone"])

    response = authenticated_client.post("/api/v1/auth/verify-phone/", {}, format="json")

    assert response.status_code == 503
    assert response.json()["errors"]["code"] == "AUTH_OTP_UNAVAILABLE"
    assert not PhoneOTP.objects.filter(user=user).exists()


def test_production_settings_declare_the_console_sms_provider_undeliverable():
    source = open("config/settings/production.py", encoding="utf-8").read()
    assert "SMS_CONSOLE_IS_DELIVERY = False" in source
