"""
Order, payment and stock integrity: the regressions found by the end-to-end
review. Each test names the hole it closes; the fix lives with a why-comment
next to the code it changed.
"""
import datetime
import uuid
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.coupons.models import Coupon, CouponDiscountType, CouponUsage
from apps.inventory.models import Batch, StockReservation, StockReservationStatus
from apps.orders.models import Order, OrderStatus, ReturnItem, ReturnReason, ReturnRequest, ReturnRequestStatus
from apps.orders.services import cancel_order, complete_return_and_refund, transition_status
from apps.payments.models import Payment, PaymentStatus
from tests.conftest import INSTAPAY_TEST_CONFIG, payment_providers_with
from tests.test_cod_deposit import _cod_order, deposit_on  # noqa: F401  (fixture)
from tests.test_guest_checkout import GUEST_PHONE, _guest_body, _guest_with_cart, _place

pytestmark = pytest.mark.django_db

API = "/api/v1"
SEND = "apps.notifications.tasks.send_email_task.delay"


@pytest.fixture
def instapay_on(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    settings.FRONTEND_URL = "https://shop.example.test"
    return settings


def _transfer_order(product, governorate, shipping_setup, **extra):
    client = _guest_with_cart(product["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"], payment="instapay", **extra))
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    return Order.objects.get(order_number=data["order"]["order_number"]), Payment.objects.get(pk=data["payment"]["id"])


def _settle(staff_client, payment, outcome="received"):
    return staff_client.post(f"{API}/admin-api/payments/{payment.id}/settle/", {"outcome": outcome}, format="json")


def _set_status(staff_client, order, status, note=""):
    return staff_client.post(
        f"{API}/admin-api/orders/{order.order_number}/status/", {"status": status, "note": note}, format="json"
    )


def _deliver(order):
    for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
        order = transition_status(order, status)
    return order


# ---------------------------------------------------------------------------
# 1. Confirming an unpaid transfer / deposit order from the order records the
#    money as received (the payments screen is a record, not a required step).
#    It can still never skip confirmation, and a card payment is never settled
#    by hand.
# ---------------------------------------------------------------------------


def test_staff_confirming_a_transfer_order_records_the_transfer(instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)

    response = _set_status(staff_client, order, "confirmed")
    assert response.status_code == 200, response.content
    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert payment.status == PaymentStatus.CAPTURED
    assert set(StockReservation.objects.filter(order=order).values_list("status", flat=True)) == {
        StockReservationStatus.CONSUMED}

    # Settling it afterwards from the payments screen changes nothing.
    assert _settle(staff_client, payment).status_code == 200
    assert Payment.objects.filter(order=order, status=PaymentStatus.CAPTURED).count() == 1


def test_staff_confirming_a_cod_order_records_its_deposit_not_the_cash(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):  # noqa: F811
    order, _, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    response = _set_status(staff_client, order, "confirmed")
    assert response.status_code == 200, response.content
    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert order.payments.exclude(provider="cod").get().status == PaymentStatus.CAPTURED
    # The rest is cash at the door: still to collect on delivery.
    assert order.payments.get(provider="cod").status == PaymentStatus.PENDING


def test_an_unpaid_transfer_order_cannot_skip_confirmation(instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    response = _set_status(staff_client, order, "processing")
    assert response.status_code == 409
    payment.refresh_from_db()
    assert payment.status != PaymentStatus.CAPTURED


def test_a_card_payment_is_never_settled_by_confirming(instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    Payment.objects.filter(pk=payment.pk).update(provider="paymob")

    response = _set_status(staff_client, order, "confirmed")
    assert response.status_code == 409
    assert response.json()["errors"]["code"] == "ORDER_PAYMENT_NOT_SETTLED"
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status != PaymentStatus.CAPTURED
    assert order.status == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# 2. shipping_method_id 0 is not free shipping
# ---------------------------------------------------------------------------


def test_shipping_method_zero_is_refused(product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    summary = client.post(
        f"{API}/checkout/summary/",
        {"governorate": governorate.id, "shipping_method_id": 0, "payment_method": "cod"}, format="json",
    )
    assert summary.status_code == 400
    body = _guest_body(governorate, shipping_setup["method"])
    body["shipping_method_id"] = 0
    assert _place(client, body).status_code == 400
    assert not Order.objects.exists()


# ---------------------------------------------------------------------------
# 3. The admin status change goes through each status's own flow
# ---------------------------------------------------------------------------


def test_admin_status_cancelled_runs_cancel_order(staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    Coupon.objects.create(code="TEN", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True)
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"], coupon_code="TEN"))
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    batch = product_with_variant_and_stock["batch"]
    batch.refresh_from_db()
    assert batch.quantity == 9 and CouponUsage.objects.filter(order=order).exists()

    response = _set_status(staff_client, order, "cancelled", note="duplicate")
    assert response.status_code == 200, response.content
    order.refresh_from_db()
    batch.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED and order.cancel_reason == "duplicate"
    assert batch.quantity == 10  # restocked
    assert not CouponUsage.objects.filter(order=order).exists()
    assert order.payments.get().status == PaymentStatus.CANCELLED


@pytest.mark.parametrize("status", ["returned", "refunded", "partially_refunded", "return_requested"])
def test_admin_status_refuses_statuses_with_their_own_flow(staff_client, delivered_order, status):
    response = _set_status(staff_client, delivered_order, status)
    assert response.status_code == 409
    assert response.json()["errors"]["code"] == "ORDER_STATUS_HAS_OWN_FLOW"
    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.DELIVERED


# ---------------------------------------------------------------------------
# 4 / 5. Batches: no double booking, one rule for the last day
# ---------------------------------------------------------------------------


def test_a_batch_is_never_reserved_beyond_its_free_units(product_with_variant_and_stock):
    from apps.inventory.services import consume_reservation, reserve_stock

    variant, item, a = (product_with_variant_and_stock[k] for k in ("variant", "item", "batch"))
    b = Batch.objects.create(
        item=item, batch_number="B2", quantity=10, initial_quantity=10,
        expiration_date=timezone.localdate() + datetime.timedelta(days=700), is_active=True,
    )
    first = reserve_stock(variant=variant, quantity=8)
    second = reserve_stock(variant=variant, quantity=5)
    assert [(r.batch_id, r.quantity) for r in second] == [(a.pk, 2), (b.pk, 3)]
    for reservation in first + second:
        consume_reservation(reservation)
    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.quantity, b.quantity) == (0, 7)


def test_a_batch_expiring_today_is_not_reserved_and_is_swept(product_with_variant_and_stock):
    from apps.inventory.services import consume_reservation, get_expired_batches, reserve_stock

    variant, item, a = (product_with_variant_and_stock[k] for k in ("variant", "item", "batch"))
    Batch.objects.filter(pk=a.pk).update(expiration_date=timezone.localdate())
    b = Batch.objects.create(
        item=item, batch_number="B2", quantity=10, initial_quantity=10,
        expiration_date=timezone.localdate() + datetime.timedelta(days=700), is_active=True,
    )
    [reservation] = reserve_stock(variant=variant, quantity=1)
    assert reservation.batch_id == b.pk
    consume_reservation(reservation)  # no NO_VALID_BATCH
    assert list(get_expired_batches()) == [Batch.objects.get(pk=a.pk)]


# ---------------------------------------------------------------------------
# 6. A late transfer can still be recorded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("written_off", [PaymentStatus.EXPIRED, PaymentStatus.FAILED])
def test_a_late_transfer_is_recorded_and_confirms(instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup, written_off):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    Payment.objects.filter(pk=payment.pk).update(status=written_off)

    assert _settle(staff_client, payment).status_code == 200
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert order.status == OrderStatus.CONFIRMED


def test_a_late_transfer_on_a_cancelled_order_is_flagged_for_refund(instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    cancel_order(order, reason="no transfer")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CANCELLED  # item 8: closed with its order

    assert _settle(staff_client, payment).status_code == 200
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED and payment.metadata["needs_refund"] is True
    assert order.status == OrderStatus.CANCELLED


def test_a_refused_cod_payment_still_cannot_be_recorded_as_collected(gateway_payment, staff_client):
    payment = gateway_payment(provider="cod", method="cod", status=PaymentStatus.FAILED)
    Order.objects.filter(pk=payment.order_id).update(delivered_at=timezone.now())
    assert _settle(staff_client, payment).status_code == 409


# ---------------------------------------------------------------------------
# 7. An expired transfer lets go of its order
# ---------------------------------------------------------------------------


def _age(payment, days=15):
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - datetime.timedelta(days=days))


def test_an_expired_transfer_cancels_its_pending_order(instapay_on, product_with_variant_and_stock, governorate, shipping_setup):
    from apps.payments.tasks import EXPIRED_TRANSFER_CANCEL_REASON, expire_stale_payments_task

    Coupon.objects.create(code="TEN", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, is_active=True)
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup, coupon_code="TEN")
    _age(payment)

    assert expire_stale_payments_task() == 1
    order.refresh_from_db()
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.EXPIRED
    assert order.status == OrderStatus.CANCELLED and order.cancel_reason == EXPIRED_TRANSFER_CANCEL_REASON
    assert not StockReservation.objects.filter(order=order, status=StockReservationStatus.ACTIVE).exists()
    assert not CouponUsage.objects.filter(order=order).exists()


def test_an_expired_deposit_cancels_the_cod_order_and_its_cash_payment(deposit_on, product_with_variant_and_stock, governorate, shipping_setup):  # noqa: F811
    from apps.payments.tasks import expire_stale_payments_task

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    _age(Payment.objects.get(pk=data["payment"]["id"]))
    expire_stale_payments_task()
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED
    assert order.payments.get(provider="cod").status == PaymentStatus.CANCELLED


def test_the_expiry_sweep_leaves_a_confirmed_order_alone(instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup):
    from apps.payments.tasks import _cancel_order_of_expired_transfer

    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.CONFIRMED)
    Payment.objects.filter(pk=payment.pk).update(status=PaymentStatus.EXPIRED)
    _cancel_order_of_expired_transfer(Payment.objects.get(pk=payment.pk))
    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED


# ---------------------------------------------------------------------------
# 8. Cancelling closes the payments that were still open
# ---------------------------------------------------------------------------


def test_cancelling_a_deposit_order_closes_both_payments(deposit_on, product_with_variant_and_stock, governorate, shipping_setup):  # noqa: F811
    order, _, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    cancel_order(order, reason="العميل لغى")
    assert set(order.payments.values_list("status", flat=True)) == {PaymentStatus.CANCELLED}


# ---------------------------------------------------------------------------
# 9. Return lines are validated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("items", [
    [{"quantity": 1}],
    [{"order_item_id": "ITEM", "quantity": 1}],
    [{"order_item_id": "OI", "quantity": "abc"}],
    [{"order_item_id": "OI", "quantity": -3}],
    [{"order_item_id": "OI", "quantity": 0}],
    [{"order_item_id": "OI", "quantity": 1}, {"order_item_id": "OI", "quantity": 1}],
])
def test_a_return_with_bad_items_is_a_400(authenticated_client, delivered_order, items):
    item_id = delivered_order.items.first().id
    items = [{**e, "order_item_id": item_id} if e.get("order_item_id") == "OI" else e for e in items]
    response = authenticated_client.post(
        f"{API}/orders/{delivered_order.order_number}/returns/", {"reason": "damaged", "items": items}, format="json"
    )
    assert response.status_code == 400, response.content
    assert not ReturnRequest.objects.exists()


def test_a_valid_return_is_accepted(authenticated_client, delivered_order):
    item_id = delivered_order.items.first().id
    response = authenticated_client.post(
        f"{API}/orders/{delivered_order.order_number}/returns/",
        {"reason": "damaged", "items": [{"order_item_id": item_id, "quantity": 1}]}, format="json",
    )
    assert response.status_code == 201, response.content


# ---------------------------------------------------------------------------
# A. Completing a return refunds what was paid, from the money held
# ---------------------------------------------------------------------------


def _approved_return(order, user, quantity=1):
    order = transition_status(order, OrderStatus.RETURN_REQUESTED)
    rr = ReturnRequest.objects.create(order=order, user=user, reason=ReturnReason.DAMAGED, status=ReturnRequestStatus.APPROVED)
    ReturnItem.objects.create(return_request=rr, order_item=order.items.first(), quantity=quantity)
    return rr


def test_a_return_on_a_deposit_order_completes(deposit_on, staff_client, staff_user, product_with_variant_and_stock, governorate, shipping_setup):  # noqa: F811
    from apps.payments.models import Refund

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    _settle(staff_client, Payment.objects.get(pk=data["payment"]["id"]))
    order.refresh_from_db()
    order = _deliver(order)

    rr = complete_return_and_refund(return_request=_approved_return(order, staff_user), processed_by=staff_user)
    assert rr.status == ReturnRequestStatus.REFUNDED
    refunds = Refund.objects.filter(order=order)
    assert sum(r.amount.amount for r in refunds) == Decimal("285.00")  # 250 + 14% VAT paid on it
    # The cash payment first, then the deposit for the rest. It used to try all
    # 250 against the 100 deposit and roll the return back.
    cash = order.payments.get(provider="cod").amount.amount
    assert cash < Decimal("285.00")
    by_route = {r.payment.provider: r.amount.amount for r in refunds}
    assert by_route == {"cod": cash, "vodafone_cash": Decimal("285.00") - cash}


def test_a_return_refunds_the_discounted_price(authenticated_client, user, product_with_variant_and_stock, address, shipping_setup):
    from apps.payments.models import Refund

    Coupon.objects.create(code="TWENTY", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=20, is_active=True)
    authenticated_client.post(f"{API}/cart/items/", {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")
    response = authenticated_client.post(
        f"{API}/checkout/",
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod", "coupon_code": "TWENTY"},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    order = _deliver(Order.objects.get(order_number=response.json()["data"]["order"]["order_number"]))
    complete_return_and_refund(return_request=_approved_return(order, user), processed_by=user)
    assert Refund.objects.get(order=order).amount.amount == Decimal("228.00")  # 250 less 20%, plus the 14% VAT paid on the 200


# ---------------------------------------------------------------------------
# B. A capture never fails over an expired batch
# ---------------------------------------------------------------------------


def _card_payment_with_active_hold(gateway_payment):
    payment = gateway_payment(provider="paymob", method="card", status=PaymentStatus.REQUIRES_ACTION)
    # Put the hold back to active, like a real card order waiting to be paid.
    for reservation in StockReservation.objects.filter(order=payment.order):
        Batch.objects.filter(pk=reservation.batch_id).update(quantity=10)
    StockReservation.objects.filter(order=payment.order).update(status=StockReservationStatus.ACTIVE)
    return payment


def test_a_capture_survives_an_expired_batch_and_moves_the_hold(gateways_enabled, gateway_payment, product_with_variant_and_stock):
    from apps.payments.services import mark_payment_captured

    payment = _card_payment_with_active_hold(gateway_payment)
    a = product_with_variant_and_stock["batch"]
    Batch.objects.filter(pk=a.pk).update(expiration_date=timezone.localdate())
    b = Batch.objects.create(
        item=product_with_variant_and_stock["item"], batch_number="B2", quantity=10, initial_quantity=10,
        expiration_date=timezone.localdate() + datetime.timedelta(days=700), is_active=True,
    )

    mark_payment_captured(payment, from_gateway=True, note="webhook")
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.order.status == OrderStatus.CONFIRMED
    reservation = StockReservation.objects.get(order=payment.order)
    assert reservation.status == StockReservationStatus.CONSUMED and reservation.batch_id == b.pk
    b.refresh_from_db()
    assert b.quantity == 8


def test_a_capture_with_no_replacement_batch_is_recorded_and_flagged(gateways_enabled, gateway_payment, product_with_variant_and_stock):
    from apps.audit.models import AuditLog
    from apps.payments.services import mark_payment_captured

    payment = _card_payment_with_active_hold(gateway_payment)
    Batch.objects.update(expiration_date=timezone.localdate())

    mark_payment_captured(payment, from_gateway=True, note="webhook")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED
    reservation = StockReservation.objects.get(order=payment.order)
    assert reservation.status == StockReservationStatus.ACTIVE  # left for a person
    assert AuditLog.objects.filter(model_name="StockReservation", object_id=str(reservation.pk)).exists()


# ---------------------------------------------------------------------------
# 10 / H. "One per customer" survives signing in
# ---------------------------------------------------------------------------


def test_a_coupon_spent_as_a_guest_is_not_spent_again_after_signing_in(
    authenticated_client, address, product_with_variant_and_stock, governorate, shipping_setup,
):
    Coupon.objects.create(code="ONCE", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=10, per_user_limit=1, is_active=True)
    guest = _guest_with_cart(product_with_variant_and_stock["variant"])
    assert _place(guest, _guest_body(governorate, shipping_setup["method"], phone=GUEST_PHONE, coupon_code="ONCE")).status_code == 201

    # The account's address carries the same phone the guest order went to.
    authenticated_client.post(f"{API}/cart/items/", {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")
    response = authenticated_client.post(
        f"{API}/checkout/",
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod", "coupon_code": "ONCE"},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 409, response.content
    assert response.json()["errors"]["code"] == "COUPON_USAGE_LIMIT_REACHED"


# ---------------------------------------------------------------------------
# 11. Idempotency keys belong to whoever sent them
# ---------------------------------------------------------------------------


def test_an_idempotency_key_does_not_replay_another_customers_checkout(product_with_variant_and_stock, governorate, shipping_setup):
    key = str(uuid.uuid4())
    first = _place(_guest_with_cart(product_with_variant_and_stock["variant"]), _guest_body(governorate, shipping_setup["method"]), key=key)
    assert first.status_code == 201

    # A different session sending the same key and body gets nothing of the first's.
    stranger = _place(APIClient(), _guest_body(governorate, shipping_setup["method"]), key=key)
    body = stranger.json()
    assert "access_token" not in (body.get("data") or {})
    assert first.json()["data"]["order"]["order_number"] not in stranger.content.decode()


# ---------------------------------------------------------------------------
# 12 / 13. Money in two places, names in Arabic
# ---------------------------------------------------------------------------


def test_totals_are_two_decimal_places(product_with_variant_and_stock, governorate, shipping_setup):
    variant = product_with_variant_and_stock["variant"]
    variant.price = Decimal("99.99")
    variant.save(update_fields=["price"])
    Coupon.objects.create(code="FIFTEEN", discount_type=CouponDiscountType.PERCENTAGE, percentage_value=15, is_active=True)
    client = _guest_with_cart(variant)
    totals = client.post(
        f"{API}/checkout/summary/",
        {"governorate": governorate.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod", "coupon_code": "FIFTEEN"},
        format="json",
    ).json()["data"]
    for key in ("subtotal", "coupon_discount", "tax_total", "shipping_total", "grand_total"):
        assert len(totals[key].split(".")[1]) == 2, (key, totals[key])
    assert totals["coupon_discount"] == "15.00"  # 14.9985
    assert totals["tax_total"] == "11.90"  # 14% of the 84.99 paid: 11.8986


def test_the_order_line_keeps_the_arabic_name(product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"]))
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    assert order.items.get().product_name_snapshot == "عسل السدر"


# ---------------------------------------------------------------------------
# 14. The payment-failed email
# ---------------------------------------------------------------------------


def test_a_refused_deposit_email_is_guest_safe_and_in_pounds(deposit_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks):  # noqa: F811
    from django.template.loader import render_to_string

    order, data, _ = _cod_order(product_with_variant_and_stock, governorate, shipping_setup)
    with mock.patch(SEND) as send, django_capture_on_commit_callbacks(execute=True):
        _settle(staff_client, Payment.objects.get(pk=data["payment"]["id"]), outcome="refused")
    mail = next(c.kwargs for c in send.call_args_list if c.kwargs.get("template_name") == "payment_failed")
    context = mail["context"]
    assert context["amount"] == "100.00" and context["is_deposit"] is True
    assert "/track" in context["order_url"]
    html = render_to_string("emails/payment_failed.html", context)
    assert "ج.م" in html and "EGP" not in html and "/account/orders" not in html
    assert "دفعة التأكيد" in html


# ---------------------------------------------------------------------------
# D. Emails are stored lower-case
# ---------------------------------------------------------------------------


def test_a_social_sign_up_can_later_sign_in_with_a_password():
    from apps.social_auth.services import resolve_user

    user, _ = resolve_user("google", "uid-1", "Ahmed.Ali@Gmail.com", True, "Ahmed", "Ali")
    assert user.email == "ahmed.ali@gmail.com"
    user.set_password("SuperSecret123!x")
    user.save()
    response = APIClient().post(f"{API}/auth/login/", {"email": "Ahmed.Ali@Gmail.com", "password": "SuperSecret123!x"}, format="json")
    assert response.status_code == 200, response.content


def test_registering_an_address_that_differs_only_in_case_is_refused():
    from apps.users.models import User

    User.objects.filter(pk=User.objects.create_user(email="x@example.com").pk).update(email="Sara@Example.com")  # an old row
    response = APIClient().post(
        f"{API}/auth/register/",
        {"email": "sara@example.com", "password": "AnotherPass123!", "first_name": "S", "last_name": "X"}, format="json",
    )
    assert response.status_code == 409
    assert User.objects.filter(email__iexact="sara@example.com").count() == 1


def test_the_email_migration_lowercases_and_reports_duplicates():
    import importlib

    from django.apps import apps as django_apps

    from apps.users.models import User

    migration = importlib.import_module("apps.users.migrations.0003_lowercase_emails")
    single = User.objects.create_user(email="one@example.com")
    User.objects.filter(pk=single.pk).update(email="One@Example.com")
    a = User.objects.create_user(email="dup1@example.com")
    b = User.objects.create_user(email="dup2@example.com")
    User.objects.filter(pk=a.pk).update(email="Dup@Example.com")
    User.objects.filter(pk=b.pk).update(email="dup@example.com")

    migration.lowercase_emails(django_apps, None)
    assert User.objects.get(pk=single.pk).email == "one@example.com"
    assert {User.objects.get(pk=a.pk).email, User.objects.get(pk=b.pk).email} == {"Dup@Example.com", "dup@example.com"}


# ---------------------------------------------------------------------------
# F. Loyalty points do not outlive a cancelled order
# ---------------------------------------------------------------------------


def test_cancelling_takes_back_the_points_the_order_earned(authenticated_client, user, product_with_variant_and_stock, address, shipping_setup):
    from apps.loyalty.models import LoyaltyAccount

    authenticated_client.post(f"{API}/cart/items/", {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")
    response = authenticated_client.post(
        f"{API}/checkout/",
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    earned = LoyaltyAccount.objects.get(user=user).points_balance
    assert earned > 0

    cancel_order(order, reason="changed my mind")
    assert LoyaltyAccount.objects.get(user=user).points_balance == 0


# ---------------------------------------------------------------------------
# G. The shipped email carries the tracking
# ---------------------------------------------------------------------------


def test_the_shipped_email_names_the_carrier_and_tracking(shipping_setup, django_capture_on_commit_callbacks):
    from apps.shipping.models import Shipment

    order = Order.objects.create(
        order_number="ORD-TEST-1", currency="EGP", customer_name="Jane", customer_email="jane@example.com",
        customer_phone="+201001234567", payment_method="cod", subtotal=Decimal("10"), grand_total=Decimal("10"),
        status=OrderStatus.PACKED,
    )
    Shipment.objects.create(
        order=order, shipping_method=shipping_setup["method"], carrier_code="bosta",
        tracking_number="BX123", tracking_url="https://track.example.test/BX123",
    )
    with mock.patch(SEND) as send, django_capture_on_commit_callbacks(execute=True):
        transition_status(order, OrderStatus.SHIPPED)
    context = next(c.kwargs for c in send.call_args_list if c.kwargs.get("template_name") == "order_shipped")["context"]
    assert (context["carrier"], context["tracking_number"], context["tracking_url"]) == (
        "bosta", "BX123", "https://track.example.test/BX123",
    )


# ---------------------------------------------------------------------------
# I. Small ones
# ---------------------------------------------------------------------------


def test_the_order_number_carries_the_cairo_date(db):
    from apps.orders.services import generate_order_number

    late_utc = datetime.datetime(2026, 1, 1, 23, 30, tzinfo=datetime.timezone.utc)  # 01:30 on the 2nd in Cairo
    with mock.patch("django.utils.timezone.now", return_value=late_utc):
        assert generate_order_number().startswith("ORD-20260102-")


def test_an_impossible_date_filter_is_a_400(staff_client):
    response = staff_client.get(f"{API}/admin-api/orders/", {"date_from": "2026-13-45"})
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == "VALIDATION_ERROR"


def test_scoped_promotions_cost_no_query_per_line(product_with_variant_and_stock, category, django_assert_max_num_queries):
    from types import SimpleNamespace

    from djmoney.money import Money

    from apps.promotions.models import Promotion
    from apps.promotions.services import compute_promotions_for_items

    promotion = Promotion.objects.create(name="Honey week", promotion_type="percentage", percentage_value=10, is_active=True)
    promotion.categories.add(category)
    product = product_with_variant_and_stock["product"]
    lines = [
        SimpleNamespace(variant=SimpleNamespace(product=product), quantity=1,
                        unit_price_snapshot=Money(250, "EGP"), line_total=Money(250, "EGP"))
        for _ in range(8)
    ]
    with django_assert_max_num_queries(4):  # promotions + their three scope sets
        discount, applied, _ = compute_promotions_for_items(lines, "EGP")
    assert discount.amount == Decimal("200.00")


# ---------------------------------------------------------------------------
# Throttling: browsing is not the anonymous limit
# ---------------------------------------------------------------------------


def test_anonymous_browsing_is_not_throttled_at_sixty_a_minute(category, product_with_variant_and_stock):
    client = APIClient()
    slug = product_with_variant_and_stock["product"].slug
    urls = [f"{API}/categories/", f"{API}/products/{slug}/", f"{API}/products/", f"{API}/payments/methods/"]
    for i in range(120):
        response = client.get(urls[i % len(urls)])
        assert response.status_code == 200, (i, urls[i % len(urls)], response.status_code)


def test_login_is_still_throttled():
    client = APIClient()
    codes = [
        client.post(f"{API}/auth/login/", {"email": "nobody@example.com", "password": "wrong"}, format="json").status_code
        for _ in range(25)
    ]
    assert 429 in codes


def test_free_text_search_keeps_its_own_limit(db):
    client = APIClient()
    codes = [client.get(f"{API}/products/", {"search": "عسل"}).status_code for _ in range(65)]
    assert 429 in codes
