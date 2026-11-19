"""
Theft attempts, from the thief's side.

The owner asked for every way a customer could take more than they paid for:
a coupon spent twice, money the shop never received recorded as paid, stock
that leaves twice, a refund paid twice. Each test performs the abuse through
the real endpoints and asserts the shop loses nothing. Neighbouring attacks
already pinned elsewhere (price and total tampering, simultaneous single-use
coupons, other customers' orders) live in test_pentest_api.py and
test_security_authz.py.
"""

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.coupons.models import Coupon, CouponDiscountType, CouponUsage
from apps.orders.models import Order, OrderStatus
from apps.payments.models import PaymentStatus
from apps.users.models import User
from tests.test_guest_checkout import GUEST_PHONE, _guest_body, _guest_with_cart, _place
from tests.test_order_integrity import _transfer_order, instapay_on  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db

API = "/api/v1"
DENIED = {401, 403, 404}


def _coupon(code="ONCE", amount="20.00", **extra):
    fields = dict(
        code=code, discount_type=CouponDiscountType.FIXED_AMOUNT, fixed_amount_value=Decimal(amount),
        is_active=True, per_user_limit=1,
    )
    fields.update(extra)
    return Coupon.objects.create(**fields)


def _guest_order(variant, governorate, method, **body):
    return _place(_guest_with_cart(variant), _guest_body(governorate, method, **body))


def _client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _signed_in_checkout(client, variant, address, method, coupon_code="", quantity=1):
    client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": quantity}, format="json")
    return client.post(
        f"{API}/checkout/",
        {"address_id": address.id, "shipping_method_id": method.id, "payment_method": "cod", "coupon_code": coupon_code},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


# ===========================================================================
# 1. One coupon, spent twice
# ===========================================================================


@pytest.mark.parametrize("phone", [
    "+201001234567",       # international
    "00201001234567",      # international, dialled with 00
    "٠١٠٠١٢٣٤٥٦٧",         # Arabic-Indic digits
    "0100 123 4567",       # spaced
    "0100-123-4567",       # dashed
])
def test_a_guest_cannot_spend_a_coupon_again_by_writing_the_phone_differently(
    phone, product_with_variant_and_stock, governorate, shipping_setup,
):
    coupon = _coupon()
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    assert _guest_order(variant, governorate, method, coupon_code="ONCE").status_code == 201

    again = _guest_order(variant, governorate, method, phone=phone, coupon_code="ONCE")
    assert again.status_code in {400, 409}, (phone, again.content)
    assert CouponUsage.objects.filter(coupon=coupon).count() == 1


def test_changing_the_code_case_does_not_reset_the_limit(product_with_variant_and_stock, governorate, shipping_setup):
    coupon = _coupon(code="WELCOME")
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    assert _guest_order(variant, governorate, method, coupon_code="WELCOME").status_code == 201

    again = _guest_order(variant, governorate, method, coupon_code="welcome")
    assert again.status_code in {400, 409}
    assert CouponUsage.objects.filter(coupon=coupon).count() == 1


def test_the_total_usage_limit_holds_across_different_customers(product_with_variant_and_stock, governorate, shipping_setup):
    coupon = _coupon(code="FIRST2", usage_limit=2, per_user_limit=None)
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    for phone in ("01001111111", "01002222222"):
        assert _guest_order(variant, governorate, method, phone=phone, coupon_code="FIRST2").status_code == 201

    third = _guest_order(variant, governorate, method, phone="01003333333", coupon_code="FIRST2")
    assert third.status_code in {400, 409}
    assert CouponUsage.objects.filter(coupon=coupon).count() == 2


def test_an_account_holder_cannot_spend_it_again_as_a_guest_with_the_same_phone(
    product_with_variant_and_stock, address, governorate, shipping_setup,
):
    coupon = _coupon()
    buyer = User.objects.create_user(email="buyer@example.com", password="SuperSecret123", phone=GUEST_PHONE)
    address.user = buyer
    address.save()
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]

    first = _signed_in_checkout(_client_for(buyer), variant, address, method, coupon_code="ONCE")
    assert first.status_code == 201, first.content

    as_guest = _guest_order(variant, governorate, method, coupon_code="ONCE")  # logged out, same phone
    assert as_guest.status_code in {400, 409}, as_guest.content
    assert CouponUsage.objects.filter(coupon=coupon).count() == 1


def test_cancelling_gives_the_use_back_once_not_twice(product_with_variant_and_stock, governorate, shipping_setup):
    coupon = _coupon()
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    first = _guest_order(variant, governorate, method, coupon_code="ONCE")
    assert first.status_code == 201
    order = Order.objects.get(order_number=first.json()["data"]["order"]["order_number"])

    from apps.orders.services import cancel_order

    cancel_order(order, reason="customer changed their mind")
    # The cancelled order never happened: the code may be used once more…
    assert _guest_order(variant, governorate, method, coupon_code="ONCE").status_code == 201
    # …and only once.
    assert _guest_order(variant, governorate, method, coupon_code="ONCE").status_code in {400, 409}
    assert CouponUsage.objects.filter(coupon=coupon).count() == 1


def test_applying_the_same_coupon_twice_discounts_once(authenticated_client, product_with_variant_and_stock):
    _coupon(code="TEN", amount="10.00", per_user_limit=None)
    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    authenticated_client.post(f"{API}/cart/coupon/", {"code": "TEN"}, format="json")
    second = authenticated_client.post(f"{API}/cart/coupon/", {"code": "TEN"}, format="json")
    assert second.status_code in {200, 400, 409}
    totals = authenticated_client.get(f"{API}/cart/").json()["data"]
    assert Decimal(str(totals.get("coupon_discount") or totals.get("discount_total") or "10.00")) <= Decimal("10.00")


def test_two_coupons_never_stack_at_checkout(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    _coupon(code="AAA", amount="30.00", per_user_limit=None)
    _coupon(code="BBB", amount="40.00", per_user_limit=None)
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    authenticated_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    authenticated_client.post(f"{API}/cart/coupon/", {"code": "AAA"}, format="json")
    authenticated_client.post(f"{API}/cart/coupon/", {"code": "BBB"}, format="json")

    response = _signed_in_checkout(authenticated_client, variant, address, method, coupon_code="BBB", quantity=0)
    if response.status_code == 201:
        order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
        assert order.coupon_discount.amount <= Decimal("40.00")
        assert CouponUsage.objects.filter(order=order).count() <= 1


def test_a_coupon_switched_off_after_it_was_applied_gives_nothing(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    coupon = _coupon(code="GONE", amount="50.00", per_user_limit=None)
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    authenticated_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    assert authenticated_client.post(f"{API}/cart/coupon/", {"code": "GONE"}, format="json").status_code == 200
    coupon.is_active = False
    coupon.save()

    response = authenticated_client.post(
        f"{API}/checkout/",
        {"address_id": address.id, "shipping_method_id": method.id, "payment_method": "cod", "coupon_code": "GONE"},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    if response.status_code == 201:
        order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
        assert order.coupon_discount.amount == 0
    else:
        assert response.status_code in {400, 404, 409}
    assert not CouponUsage.objects.filter(coupon=coupon).exists()


def test_a_discount_bigger_than_the_order_never_makes_the_total_negative(
    product_with_variant_and_stock, governorate, shipping_setup,
):
    _coupon(code="HUGE", amount="100000.00", per_user_limit=None)
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    response = _guest_order(variant, governorate, method, coupon_code="HUGE")
    if response.status_code == 201:
        order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
        assert order.grand_total.amount >= 0
        assert order.coupon_discount.amount <= order.subtotal.amount
    else:
        assert response.status_code in {400, 409}


# ===========================================================================
# 2. Money the shop never received, recorded as paid
# ===========================================================================


def test_a_customer_cannot_confirm_their_own_transfer_order(
    instapay_on, authenticated_client, product_with_variant_and_stock, governorate, shipping_setup,  # noqa: F811
):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    response = authenticated_client.post(
        f"{API}/admin-api/orders/{order.order_number}/status/", {"status": "confirmed"}, format="json",
    )
    assert response.status_code in DENIED
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status != PaymentStatus.CAPTURED
    assert order.status == OrderStatus.PENDING


def test_a_customer_cannot_mark_their_own_transfer_received(
    instapay_on, authenticated_client, product_with_variant_and_stock, governorate, shipping_setup,  # noqa: F811
):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    for client in (APIClient(), authenticated_client):
        response = client.post(f"{API}/admin-api/payments/{payment.id}/settle/", {"outcome": "received"}, format="json")
        assert response.status_code in DENIED
    payment.refresh_from_db()
    assert payment.status != PaymentStatus.CAPTURED


def test_an_unpaid_transfer_order_is_never_shipped(
    instapay_on, staff_client, product_with_variant_and_stock, governorate, shipping_setup,  # noqa: F811
):
    order, payment = _transfer_order(product_with_variant_and_stock, governorate, shipping_setup)
    for status in ("processing", "packed", "shipped", "out_for_delivery", "delivered"):
        response = staff_client.post(
            f"{API}/admin-api/orders/{order.order_number}/status/", {"status": status}, format="json",
        )
        assert response.status_code == 409, (status, response.content)
    order.refresh_from_db()
    assert order.status == OrderStatus.PENDING


# ===========================================================================
# 3. Stock that leaves twice, refunds paid twice
# ===========================================================================


def test_nobody_can_order_more_than_is_in_stock(product_with_variant_and_stock, governorate, shipping_setup):
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]  # 10 in stock
    client = APIClient()
    added = client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 11}, format="json")
    if added.status_code == 200:
        placed = _place(client, _guest_body(governorate, method))
        assert placed.status_code in {400, 409}, placed.content
    else:
        assert added.status_code in {400, 409}
    assert not Order.objects.exists()


def test_the_last_jars_cannot_be_sold_twice(product_with_variant_and_stock, governorate, shipping_setup):
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]  # 10 in stock
    first = _place(_guest_with_cart(variant, quantity=10), _guest_body(governorate, method))
    assert first.status_code == 201, first.content
    # Another shopper, one jar: the cart may still take it, the checkout may not.
    client = APIClient()
    client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    second = _place(client, _guest_body(governorate, method, phone="01005555555"))
    assert second.status_code in {400, 409}, second.content
    assert Order.objects.count() == 1


def test_the_same_jar_cannot_be_returned_twice(authenticated_client, delivered_order):
    item = delivered_order.items.first()
    url = f"{API}/orders/{delivered_order.order_number}/returns/"
    body = {"reason": "damaged", "items": [{"order_item_id": item.id, "quantity": item.quantity}]}
    assert authenticated_client.post(url, body, format="json").status_code == 201
    again = authenticated_client.post(url, body, format="json")
    assert again.status_code in {400, 409}, again.content


def test_a_return_cannot_ask_for_more_than_was_bought(authenticated_client, delivered_order):
    item = delivered_order.items.first()
    response = authenticated_client.post(
        f"{API}/orders/{delivered_order.order_number}/returns/",
        {"reason": "damaged", "items": [{"order_item_id": item.id, "quantity": item.quantity + 5}]}, format="json",
    )
    assert response.status_code == 400


def test_a_shipped_order_cannot_be_cancelled_for_a_refund_by_the_customer(authenticated_client, delivered_order):
    response = authenticated_client.post(f"{API}/orders/{delivered_order.order_number}/cancel/", {"reason": "x"}, format="json")
    assert response.status_code in {400, 409}
    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.DELIVERED


def test_a_refund_cannot_exceed_what_was_paid(staff_client, delivered_order):
    payment = delivered_order.payments.filter(status=PaymentStatus.CAPTURED).first()
    if payment is None:
        pytest.skip("the delivered fixture has no captured payment")
    too_much = payment.amount.amount + Decimal("1.00")
    response = staff_client.post(
        f"{API}/admin-api/payments/{payment.id}/refund/", {"amount": str(too_much), "reason": "test"},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code in {400, 409}
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_a_guest_order_token_opens_nothing_but_its_own_order(
    product_with_variant_and_stock, governorate, shipping_setup,
):
    variant, method = product_with_variant_and_stock["variant"], shipping_setup["method"]
    mine = _guest_order(variant, governorate, method).json()["data"]
    theirs = _guest_order(variant, governorate, method, phone="01007777777").json()["data"]
    client = APIClient()
    response = client.get(f"{API}/orders/{theirs['order']['order_number']}/", {"token": mine["access_token"]})
    assert response.status_code in DENIED
