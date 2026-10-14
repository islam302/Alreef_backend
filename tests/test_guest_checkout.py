"""
Guest checkout: ordering without an account, and getting back to the order.

The security half matters more than the happy path. A guest order has no user
to filter on, so everything that used to be "filter by request.user" is replaced
by a signed token or the order number plus its phone, and these tests pin down
that neither opens anybody else's order.
"""
import uuid
from decimal import Decimal
from unittest import mock

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.coupons.models import Coupon, CouponDiscountType, CouponUsage
from apps.orders.models import Order, OrderStatus
from apps.shipping.models import City, Governorate

pytestmark = pytest.mark.django_db

CHECKOUT_URL = "/api/v1/checkout/"
SUMMARY_URL = "/api/v1/checkout/summary/"
CART_ITEMS_URL = "/api/v1/cart/items/"
LOOKUP_URL = "/api/v1/orders/lookup/"
GUEST_PHONE = "01001234567"
GUEST_PHONE_E164 = "+201001234567"


def _guest_with_cart(variant, quantity=1):
    client = APIClient()
    response = client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": quantity}, format="json")
    assert response.status_code == 200, response.content
    return client


def _guest_body(governorate, method, *, phone=GUEST_PHONE, email="guest@example.com", payment="cod", **extra):
    body = {
        "contact": {"full_name": "Guest Buyer", "phone": phone, "email": email},
        "address": {"governorate": governorate.id, "street": "12 Tahrir St", "building": "3"},
        "shipping_method_id": method.id,
        "payment_method": payment,
    }
    body.update(extra)
    return body


def _place(client, body, key=None):
    return client.post(CHECKOUT_URL, body, format="json", HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))


@pytest.fixture
def guest_order(product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"]))
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    return Order.objects.get(order_number=data["order"]["order_number"]), data["access_token"]


# ---------------------------------------------------------------------------
# Placing the order
# ---------------------------------------------------------------------------


def test_a_guest_can_order_cash_on_delivery_without_an_account(
    product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks,
):
    client = _guest_with_cart(product_with_variant_and_stock["variant"], quantity=2)

    with django_capture_on_commit_callbacks(execute=True):
        response = _place(client, _guest_body(governorate, shipping_setup["method"]))

    assert response.status_code == 201, response.content
    data = response.json()["data"]
    order = Order.objects.get(order_number=data["order"]["order_number"])
    assert order.user is None
    assert order.status == OrderStatus.CONFIRMED
    assert order.customer_phone == GUEST_PHONE_E164
    assert order.customer_email == "guest@example.com"
    assert order.shipping_address_snapshot["street"] == "12 Tahrir St"
    assert data["access_token"]
    # Same pricing as anybody else: 2 x 250 + 30 shipping (+ COD fee, if any).
    assert Decimal(data["order"]["subtotal"]) == Decimal("500.00")

    # The confirmation reaches them, and points at the tracking page, not at an
    # account page they cannot open.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["guest@example.com"]
    assert f"/track?order={order.order_number}" in mail.outbox[0].alternatives[0][0]


def test_a_guest_without_an_email_gets_an_order_and_no_email(
    product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks,
):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    with django_capture_on_commit_callbacks(execute=True):
        response = _place(client, _guest_body(governorate, shipping_setup["method"], email=""))
    assert response.status_code == 201, response.content
    assert mail.outbox == []


def test_the_guest_summary_prices_from_the_governorate(product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = client.post(
        SUMMARY_URL,
        {"governorate": governorate.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert Decimal(response.json()["data"]["shipping_total"]) == Decimal("30.00")


def test_guest_checkout_still_needs_an_idempotency_key(product_with_variant_and_stock, governorate, shipping_setup):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = client.post(CHECKOUT_URL, _guest_body(governorate, shipping_setup["method"]), format="json")
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_a_guest_with_an_empty_cart_cannot_order(governorate, shipping_setup):
    response = _place(APIClient(), _guest_body(governorate, shipping_setup["method"]))
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == "CART_EMPTY"


@pytest.mark.parametrize(
    "contact",
    [
        {"full_name": "Guest Buyer"},  # no phone
        {"full_name": "Guest Buyer", "phone": "12"},  # not a phone
        {"full_name": "", "phone": GUEST_PHONE},  # no name
    ],
)
def test_guest_contact_details_are_validated(product_with_variant_and_stock, governorate, shipping_setup, contact):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    body = _guest_body(governorate, shipping_setup["method"])
    body["contact"] = contact
    assert _place(client, body).status_code == 400
    assert not Order.objects.exists()


def test_a_guest_address_city_must_be_in_its_governorate(product_with_variant_and_stock, governorate, shipping_setup):
    elsewhere = Governorate.objects.create(code="alex", name_en="Alexandria", name_ar="الإسكندرية")
    city = City.objects.create(governorate=elsewhere, name_en="Smouha", name_ar="سموحة")
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    body = _guest_body(governorate, shipping_setup["method"])
    body["address"]["city"] = city.id
    assert _place(client, body).status_code == 400


def test_guest_checkout_can_be_switched_off(settings, product_with_variant_and_stock, governorate, shipping_setup):
    settings.GUEST_CHECKOUT_ENABLED = False
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    assert _place(client, _guest_body(governorate, shipping_setup["method"])).status_code == 401


def test_the_signed_in_flow_is_unchanged(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    authenticated_client.post(CART_ITEMS_URL, {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")
    response = _place(
        authenticated_client,
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"},
    )
    assert response.status_code == 201
    assert "access_token" not in response.json()["data"]
    assert Order.objects.get().user == address.user


# ---------------------------------------------------------------------------
# Security: a guest reaches their own order and nothing else
# ---------------------------------------------------------------------------


def test_a_guest_cannot_ship_to_somebody_elses_saved_address(
    product_with_variant_and_stock, address, governorate, shipping_setup,
):
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    body = _guest_body(governorate, shipping_setup["method"], address_id=address.id)

    response = _place(client, body)
    assert response.status_code == 404
    assert response.json()["errors"]["code"] == "ADDRESS_NOT_FOUND"

    summary = client.post(
        SUMMARY_URL,
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"},
        format="json",
    )
    assert summary.status_code == 404
    assert not Order.objects.exists()


def test_the_token_opens_its_own_order_without_the_private_details(guest_order):
    order, token = guest_order
    response = APIClient().post(LOOKUP_URL, {"token": token}, format="json")

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["order_number"] == order.order_number
    assert data["destination"]["governorate"] == "القاهرة"
    body = response.content.decode()
    for private in ("12 Tahrir St", "guest@example.com", GUEST_PHONE_E164, "Guest Buyer"):
        assert private not in body


def test_a_forged_or_edited_token_opens_nothing(guest_order, product_with_variant_and_stock, governorate, shipping_setup):
    from django.core import signing

    order, token = guest_order
    # A second guest order, whose id a forger would try to swap in.
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    other = _place(client, _guest_body(governorate, shipping_setup["method"], phone="01009998887"))
    other_number = other.json()["data"]["order"]["order_number"]
    other_order = Order.objects.get(order_number=other_number)

    forged = [
        token[:-2] + ("AA" if not token.endswith("AA") else "BB"),  # tampered signature
        signing.dumps({"o": other_order.pk, "n": other_number}, salt="wrong-salt"),  # wrong salt
        signing.dumps({"o": other_order.pk, "n": other_number}, salt="orders.guest-access.v1", key="not-our-key"),
        "not-a-token",
    ]
    for candidate in forged:
        response = APIClient().post(LOOKUP_URL, {"token": candidate}, format="json")
        assert response.status_code == 404, candidate
        assert response.json()["errors"]["code"] == "ORDER_NOT_FOUND"


def test_a_token_cannot_open_an_account_holders_order(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup,
):
    """Even a correctly signed token is refused for an order that belongs to an account."""
    from apps.orders.guest import issue_access_token

    authenticated_client.post(CART_ITEMS_URL, {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")
    _place(authenticated_client, {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": "cod"})
    account_order = Order.objects.get()

    response = APIClient().post(LOOKUP_URL, {"token": issue_access_token(account_order)}, format="json")
    assert response.status_code == 404

    by_phone = APIClient().post(
        LOOKUP_URL, {"order_number": account_order.order_number, "phone": str(address.phone)}, format="json",
    )
    assert by_phone.status_code == 404


def test_order_number_and_phone_open_the_order(guest_order):
    order, _ = guest_order
    # Any reasonable way of writing the same number.
    for phone in (GUEST_PHONE, GUEST_PHONE_E164, "+20 100 123 4567"):
        response = APIClient().post(LOOKUP_URL, {"order_number": order.order_number, "phone": phone}, format="json")
        assert response.status_code == 200, (phone, response.content)


def test_a_wrong_phone_and_an_unknown_number_look_exactly_alike(guest_order):
    order, _ = guest_order
    wrong_phone = APIClient().post(LOOKUP_URL, {"order_number": order.order_number, "phone": "01112223334"}, format="json")
    no_such_order = APIClient().post(LOOKUP_URL, {"order_number": "ORD-20000101-99999", "phone": GUEST_PHONE}, format="json")

    assert wrong_phone.status_code == no_such_order.status_code == 404
    assert wrong_phone.json() == no_such_order.json()


def test_the_lookup_is_throttled(guest_order):
    order, _ = guest_order
    client = APIClient()
    codes = [
        client.post(LOOKUP_URL, {"order_number": order.order_number, "phone": f"0100000{i:04d}"}, format="json").status_code
        for i in range(12)
    ]
    assert 429 in codes
    assert codes.index(429) <= 10


def test_one_order_number_stops_answering_after_too_many_wrong_phones(guest_order):
    """The per-IP throttle does not stop many addresses; this cap does."""
    from apps.orders import guest

    order, _ = guest_order
    for i in range(guest.LOOKUP_FAILURES_ALLOWED):
        assert guest.order_for_number_and_phone(order.order_number, f"0100000{i:04d}") is None
    with pytest.raises(guest.LookupLocked):
        guest.order_for_number_and_phone(order.order_number, GUEST_PHONE)


def test_the_phone_comparison_is_constant_time(guest_order):
    order, _ = guest_order
    from apps.orders import guest

    with mock.patch("apps.orders.guest.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as compare:
        guest.order_for_number_and_phone(order.order_number, GUEST_PHONE)
        guest.order_for_number_and_phone("ORD-20000101-99999", GUEST_PHONE)
    assert compare.call_count == 2


def test_a_guest_cannot_use_the_account_order_endpoints(guest_order):
    order, _ = guest_order
    client = APIClient()
    assert client.get(f"/api/v1/orders/{order.order_number}/").status_code == 401
    assert client.get("/api/v1/orders/").status_code == 401
    assert client.post(f"/api/v1/orders/{order.order_number}/cancel/", {"reason": "x"}, format="json").status_code == 401


def test_a_signed_in_customer_cannot_read_a_guest_order_by_number(guest_order, authenticated_client):
    order, _ = guest_order
    assert authenticated_client.get(f"/api/v1/orders/{order.order_number}/").status_code == 404


# ---------------------------------------------------------------------------
# Coupons: the per-customer limit follows a guest by phone
# ---------------------------------------------------------------------------


def test_a_guest_coupon_limit_is_keyed_by_phone(product_with_variant_and_stock, governorate, shipping_setup):
    coupon = Coupon.objects.create(
        code="FIRST", discount_type=CouponDiscountType.FIXED_AMOUNT, fixed_amount_value=Decimal("20.00"),
        is_active=True, per_user_limit=1,
    )
    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]

    first = _place(_guest_with_cart(variant), _guest_body(governorate, method, coupon_code="FIRST"))
    assert first.status_code == 201, first.content
    assert Decimal(first.json()["data"]["order"]["coupon_discount"]) == Decimal("20.00")
    assert CouponUsage.objects.filter(coupon=coupon).count() == 1

    # A fresh session — a new browser — with the same phone written differently.
    again = _place(_guest_with_cart(variant), _guest_body(governorate, method, phone=GUEST_PHONE_E164, coupon_code="FIRST"))
    assert again.status_code == 409
    assert again.json()["errors"]["code"] == "COUPON_USAGE_LIMIT_REACHED"

    someone_else = _place(_guest_with_cart(variant), _guest_body(governorate, method, phone="01009998887", coupon_code="FIRST"))
    assert someone_else.status_code == 201


# ---------------------------------------------------------------------------
# Paying online as a guest
# ---------------------------------------------------------------------------


def test_a_guest_can_pay_by_instapay_and_follow_the_payment(
    settings, product_with_variant_and_stock, governorate, shipping_setup,
):
    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.services import record_manual_receipt
    from tests.conftest import INSTAPAY_TEST_CONFIG, payment_providers_with

    settings.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    client = _guest_with_cart(product_with_variant_and_stock["variant"])
    response = _place(client, _guest_body(governorate, shipping_setup["method"], payment="instapay"))
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    assert data["payment"]["status"] == PaymentStatus.REQUIRES_ACTION

    looked_up = APIClient().post(LOOKUP_URL, {"token": data["access_token"]}, format="json").json()["data"]
    assert looked_up["status"] == OrderStatus.PENDING
    assert looked_up["payment"]["pay_to"] == "alreef@instapay"

    # Staff confirm the transfer: the order is confirmed, with no account to
    # award loyalty points to.
    record_manual_receipt(Payment.objects.get(pk=data["payment"]["id"]), note="seen")
    looked_up = APIClient().post(LOOKUP_URL, {"token": data["access_token"]}, format="json").json()["data"]
    assert looked_up["status"] == OrderStatus.CONFIRMED
    assert looked_up["payment"]["status"] == PaymentStatus.CAPTURED


def test_staff_see_a_guest_order_in_the_dashboard(guest_order, staff_client):
    """No account behind it must not break the screens staff work from."""
    order, _ = guest_order
    listing = staff_client.get("/api/v1/admin-api/orders/")
    assert listing.status_code == 200, listing.content
    detail = staff_client.get(f"/api/v1/admin-api/orders/{order.order_number}/")
    assert detail.status_code == 200, detail.content
    assert detail.json()["data"]["customer_phone"] == GUEST_PHONE_E164
