"""
Authorization: can one customer reach another customer's data?

A permission class answers "may you call this endpoint at all". It says
nothing about *which row* you may have. `IsAuthenticated` on an order-detail
view is satisfied by any logged-in account on earth; the thing that actually
keeps Mallory out of Jane's order is the `.filter(user=request.user)` on the
queryset. That filter is one line, it is easy to drop in a refactor, and
nothing else in the test suite notices when it goes.

So every test here is the same shape: Jane creates something real through the
API, Mallory asks for it by id, and the answer must not be her data. 404 is
the preferred answer over 403 — telling a stranger "that exists but is not
yours" is itself a disclosure — but either is accepted, because the security
property is "not 200 with Jane's contents", and pinning the exact code would
make this suite brittle for no gain.

The suite also covers the two boundaries that are not per-row: staff-only
routes must reject ordinary customers, and a customer must not be able to
promote themselves.
"""
import uuid
from datetime import date, timedelta

import pytest

from apps.users.models import Address

pytestmark = pytest.mark.django_db

DENIED = {401, 403, 404}


def _place_order(client, variant, address, method):
    """A real COD order through the real endpoints — not an ORM fixture, so
    the object under test is reached exactly the way an attacker would."""
    client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    response = client.post(
        "/api/v1/checkout/",
        {
            "address_id": address.id,
            "shipping_method_id": method.id,
            "payment_method": "cod",
            "coupon_code": "",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]


# ---------------------------------------------------------------------------
# Orders, returns and payments
# ---------------------------------------------------------------------------


def test_order_detail_is_invisible_to_another_customer(
    authenticated_client, other_client, product_with_variant_and_stock, address, shipping_setup
):
    data = _place_order(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup["method"],
    )
    order_number = data["order"]["order_number"]

    # Jane can read her own order — proves the id is real and the route works.
    assert authenticated_client.get(f"/api/v1/orders/{order_number}/").status_code == 200

    response = other_client.get(f"/api/v1/orders/{order_number}/")
    assert response.status_code in DENIED, response.content


def test_order_list_never_contains_another_customers_orders(
    authenticated_client, other_client, product_with_variant_and_stock, address, shipping_setup
):
    data = _place_order(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup["method"],
    )
    order_number = data["order"]["order_number"]

    listing = other_client.get("/api/v1/orders/")
    assert listing.status_code == 200
    numbers = [row["order_number"] for row in listing.json()["data"]["results"]]
    assert order_number not in numbers


def test_another_customer_cannot_cancel_an_order(
    authenticated_client, other_client, product_with_variant_and_stock, address, shipping_setup
):
    """Cancelling is a write against money and stock — the most valuable thing
    on this endpoint to get wrong."""
    data = _place_order(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup["method"],
    )
    order_number = data["order"]["order_number"]

    response = other_client.post(
        f"/api/v1/orders/{order_number}/cancel/", {"reason": "mine now"}, format="json"
    )
    assert response.status_code in DENIED, response.content

    from apps.orders.models import Order, OrderStatus

    assert Order.objects.get(order_number=order_number).status != OrderStatus.CANCELLED


def test_another_customer_cannot_open_a_return_on_someone_elses_order(
    other_client, delivered_order
):
    response = other_client.post(
        f"/api/v1/orders/{delivered_order.order_number}/returns/",
        {"reason": "damaged", "description": "not my order", "items": []},
        format="json",
    )
    assert response.status_code in DENIED, response.content


def test_payment_detail_and_status_are_scoped_to_the_paying_customer(
    authenticated_client, other_client, product_with_variant_and_stock, address, shipping_setup
):
    data = _place_order(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup["method"],
    )
    payment_id = (data.get("payment") or {}).get("id")
    if payment_id is None:  # pragma: no cover - contract guard
        pytest.skip("checkout did not return a payment id")

    assert authenticated_client.get(f"/api/v1/payments/{payment_id}/").status_code == 200
    assert other_client.get(f"/api/v1/payments/{payment_id}/").status_code in DENIED
    assert other_client.get(f"/api/v1/payments/{payment_id}/status/").status_code in DENIED


def test_refunds_are_staff_only(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """A customer refunding their own payment would be a self-service
    withdrawal."""
    data = _place_order(
        authenticated_client,
        product_with_variant_and_stock["variant"],
        address,
        shipping_setup["method"],
    )
    payment_id = (data.get("payment") or {}).get("id")
    if payment_id is None:  # pragma: no cover - contract guard
        pytest.skip("checkout did not return a payment id")

    response = authenticated_client.post(
        f"/api/v1/payments/{payment_id}/refund/",
        {"amount": "1.00", "reason": "test"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code in DENIED, response.content


# ---------------------------------------------------------------------------
# Addresses — read, write and delete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_addresses_are_not_reachable_by_id_from_another_account(
    other_client, address, method
):
    url = f"/api/v1/addresses/{address.id}/"
    call = getattr(other_client, method)
    response = call(url, {"street": "hijacked"}, format="json") if method == "patch" else call(url)

    assert response.status_code in DENIED, response.content
    address.refresh_from_db()
    assert address.street != "hijacked"
    assert Address.objects.filter(pk=address.id).exists()


def test_checkout_rejects_an_address_belonging_to_someone_else(
    other_client, address, shipping_setup, product_with_variant_and_stock
):
    """The nastiest variant: the attacker does not need to *read* the address.
    Passing its id at checkout would ship goods to it and, depending on the
    summary, leak the recipient back in the response."""
    other_client.post(
        "/api/v1/cart/items/",
        {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1},
        format="json",
    )
    response = other_client.post(
        "/api/v1/checkout/",
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
            "coupon_code": "",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code not in {200, 201}, response.content


def test_checkout_summary_rejects_an_address_belonging_to_someone_else(
    other_client, address, shipping_setup, product_with_variant_and_stock
):
    other_client.post(
        "/api/v1/cart/items/",
        {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1},
        format="json",
    )
    response = other_client.post(
        "/api/v1/checkout/summary/",
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
        },
        format="json",
    )
    assert response.status_code != 200, response.content


# ---------------------------------------------------------------------------
# Carts
# ---------------------------------------------------------------------------


def test_cart_items_cannot_be_touched_across_sessions(
    api_client, other_client, product_with_variant_and_stock
):
    """Two shoppers, two carts, one guessed line id."""
    from rest_framework.test import APIClient

    variant = product_with_variant_and_stock["variant"]
    victim = APIClient()
    victim.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 3}, format="json")
    item_id = victim.get("/api/v1/cart/").json()["data"]["items"][0]["id"]

    assert other_client.patch(
        f"/api/v1/cart/items/{item_id}/", {"quantity": 99}, format="json"
    ).status_code in DENIED | {400}
    assert other_client.delete(f"/api/v1/cart/items/{item_id}/").status_code in DENIED | {400}

    still_there = victim.get("/api/v1/cart/").json()["data"]["items"]
    assert len(still_there) == 1
    assert still_there[0]["quantity"] == 3


def test_cart_merge_will_not_claim_a_guest_cart_from_another_session(
    other_client, product_with_variant_and_stock
):
    """A cart id is not a secret — it is in the body of every cart response,
    so it reaches logs, analytics and screenshots. Possession of one must not
    be enough to take the basket; the claim has to come from the session that
    built it.
    """
    from rest_framework.test import APIClient

    variant = product_with_variant_and_stock["variant"]
    victim = APIClient()
    guest = victim.post(
        "/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 4}, format="json"
    )
    guest_cart_id = guest.json()["data"]["id"]

    response = other_client.post(
        "/api/v1/cart/merge/", {"guest_cart_id": guest_cart_id}, format="json"
    )

    # The endpoint answers with the attacker's own (empty) cart rather than
    # erroring, which is fine — what matters is that it is not the victim's.
    assert response.status_code == 200
    assert response.json()["data"]["items"] == [], response.content

    from apps.carts.models import Cart, CartStatus

    victim_cart = Cart.objects.get(pk=guest_cart_id)
    assert victim_cart.user_id is None
    assert victim_cart.status == CartStatus.OPEN
    assert victim_cart.items.count() == 1


def test_cart_merge_still_works_within_one_session(
    product_with_variant_and_stock, user
):
    """The guard must not break the real flow: same browser, guest cart, then
    sign in."""
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    variant = product_with_variant_and_stock["variant"]
    client = APIClient()
    guest = client.post(
        "/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 4}, format="json"
    )
    guest_cart_id = guest.json()["data"]["id"]

    # Same client — so the same session cookie — now carrying a token.
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    response = client.post(
        "/api/v1/cart/merge/", {"guest_cart_id": guest_cart_id}, format="json"
    )

    assert response.status_code == 200, response.content
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 4


# ---------------------------------------------------------------------------
# Subscriptions, notifications, loyalty, store credit
# ---------------------------------------------------------------------------


def test_subscription_routes_are_scoped_to_their_owner(
    other_client, user, address, shipping_setup
):
    from apps.subscriptions.models import Subscription

    subscription = Subscription.objects.create(
        user=user,
        name="Monthly honey",
        address=address,
        shipping_method=shipping_setup["method"],
        payment_method="cod",
        next_order_date=date.today() + timedelta(days=30),
    )

    assert other_client.get(f"/api/v1/subscriptions/{subscription.id}/").status_code in DENIED
    for action in ("pause", "resume", "cancel"):
        response = other_client.post(f"/api/v1/subscriptions/{subscription.id}/{action}/", {}, format="json")
        assert response.status_code in DENIED, f"{action}: {response.content}"


def test_notifications_cannot_be_read_or_marked_by_another_account(other_client, user):
    from apps.notifications.models import Notification

    notification = Notification.objects.create(
        user=user, title="Your order shipped", body="ORD-1 is on its way"
    )

    listing = other_client.get("/api/v1/notifications/")
    assert listing.status_code == 200
    body = listing.json()["data"]
    rows = body["results"] if isinstance(body, dict) else body
    assert all(row["id"] != notification.id for row in rows)

    response = other_client.post(f"/api/v1/notifications/{notification.id}/read/", {}, format="json")
    assert response.status_code in DENIED, response.content
    notification.refresh_from_db()
    assert notification.read_at is None


def test_loyalty_and_store_credit_report_only_the_callers_own_balance(
    authenticated_client, other_client, user
):
    """These endpoints take no id at all — they derive the account from the
    token. The risk is the opposite one: a shared or cached account object."""
    from apps.loyalty import services as loyalty_services

    account = loyalty_services.get_or_create_account(user)
    account.points_balance = 500
    account.save(update_fields=["points_balance"])

    mine = authenticated_client.get("/api/v1/loyalty/").json()["data"]
    theirs = other_client.get("/api/v1/loyalty/").json()["data"]

    assert mine["points_balance"] == 500
    assert theirs["points_balance"] == 0


# ---------------------------------------------------------------------------
# Staff boundary
# ---------------------------------------------------------------------------


ADMIN_ROUTES = [
    "/api/v1/admin-api/me/",
    "/api/v1/admin-api/overview/",
    "/api/v1/admin-api/dashboard/",
    "/api/v1/admin-api/sales-chart/",
    "/api/v1/admin-api/orders/",
    "/api/v1/admin-api/customers/",
    "/api/v1/admin-api/products/",
    "/api/v1/admin-api/staff/",
    "/api/v1/admin-api/audit-logs/",
    "/api/v1/admin-api/payments/",
    "/api/v1/admin-api/inventory/",
    "/api/v1/admin-api/coupons/",
]


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_routes_reject_anonymous_callers(api_client, path):
    assert api_client.get(path).status_code in {401, 403}


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_routes_reject_an_ordinary_signed_in_customer(authenticated_client, path):
    """The dashboard's own login check is a courtesy. This is the boundary."""
    assert authenticated_client.get(path).status_code in {401, 403}


def test_staff_listing_is_read_only_even_for_staff(staff_client, user):
    """Granting privileges stays in Django admin. If this viewset ever gains
    write methods, one compromised staff login becomes every staff login."""
    response = staff_client.post(
        "/api/v1/admin-api/staff/",
        {"email": "newstaff@example.com", "is_staff": True},
        format="json",
    )
    assert response.status_code in {403, 405}, response.content

    patch = staff_client.patch(
        f"/api/v1/admin-api/staff/{user.id}/", {"is_staff": True}, format="json"
    )
    assert patch.status_code in {403, 404, 405}, patch.content
    user.refresh_from_db()
    assert user.is_staff is False


def test_a_customer_cannot_promote_themselves_through_the_profile_endpoint(
    authenticated_client, user
):
    """`PATCH /auth/me/` writes whatever the serializer exposes. If is_staff
    or is_superuser ever lands in that field list, self-service admin."""
    response = authenticated_client.patch(
        "/api/v1/auth/me/",
        {"is_staff": True, "is_superuser": True, "first_name": "Jane"},
        format="json",
    )
    assert response.status_code == 200, response.content

    user.refresh_from_db()
    assert user.is_staff is False
    assert user.is_superuser is False


def test_a_customer_cannot_change_their_own_email_or_verified_flags(
    authenticated_client, user
):
    """Email is the identity the reset flow keys on, and the verified flags
    are what other features trust."""
    original = user.email
    authenticated_client.patch(
        "/api/v1/auth/me/",
        {"email": "attacker@example.com", "email_verified": True, "phone_verified": True},
        format="json",
    )
    user.refresh_from_db()
    assert user.email == original
    assert user.email_verified is False
    assert user.phone_verified is False


# ---------------------------------------------------------------------------
# The payment endpoints added with the gateway work
# ---------------------------------------------------------------------------
# Same shape as everything above: Jane creates something real, Mallory asks for
# it by id, and the staff-only routes are tried by a customer.


def _cod_payment_for(client, product_with_variant_and_stock, address, shipping_setup):
    import uuid as _uuid

    from apps.payments.models import Payment

    variant = product_with_variant_and_stock["variant"]
    client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    response = client.post(
        "/api/v1/checkout/",
        {
            "address_id": address.id,
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(_uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    return Payment.objects.get(pk=response.json()["data"]["payment"]["id"])


def test_payment_detail_is_not_readable_by_another_customer(
    authenticated_client, other_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    response = other_client.get(f"/api/v1/payments/{payment.id}/")

    assert response.status_code == 404
    assert str(payment.amount.amount) not in response.content.decode()


def test_payment_status_is_not_pollable_by_another_customer(
    authenticated_client, other_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    The status endpoint reconciles against the gateway, so leaking it would let a
    stranger both read a payment and make us call a provider about it.
    """
    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    response = other_client.get(f"/api/v1/payments/{payment.id}/status/")

    assert response.status_code == 404


def test_payment_status_needs_authentication(
    api_client, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    assert api_client.get(f"/api/v1/payments/{payment.id}/status/").status_code == 401


def test_a_customer_cannot_refund_their_own_payment(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """The obvious self-service attack: pay, receive, refund yourself."""
    import uuid as _uuid

    from apps.payments.services import record_cod_collection

    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )
    record_cod_collection(payment)

    response = authenticated_client.post(
        f"/api/v1/payments/{payment.id}/refund/",
        {"amount": "10.00", "reason": "mine now"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(_uuid.uuid4()),
    )

    assert response.status_code == 403
    payment.refresh_from_db()
    assert payment.refunds.count() == 0


def test_a_customer_cannot_reconcile_a_payment(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    response = authenticated_client.post(f"/api/v1/payments/{payment.id}/reconcile/")

    assert response.status_code == 403


def test_a_customer_cannot_declare_their_own_cash_collected(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    """
    Marking COD collected is what makes an order paid, and for COD there is no
    gateway to corroborate it — so it must be staff-only, or a customer could
    mark their own unpaid order paid and then ask for a refund.
    """
    from apps.payments.models import PaymentStatus

    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    response = authenticated_client.post(
        f"/api/v1/payments/{payment.id}/settle/", {"outcome": "received"}, format="json"
    )

    assert response.status_code == 403
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


def test_staff_can_record_a_cod_collection(
    staff_client, authenticated_client, product_with_variant_and_stock, address, shipping_setup
):
    from apps.payments.models import PaymentStatus

    payment = _cod_payment_for(
        authenticated_client, product_with_variant_and_stock, address, shipping_setup
    )

    response = staff_client.post(
        f"/api/v1/payments/{payment.id}/settle/",
        {"outcome": "received", "note": "collected at door"},
        format="json",
    )

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED


def test_payment_methods_endpoint_is_public_and_leaks_no_configuration(api_client):
    """
    Public by design — the storefront needs it before anybody signs in. It must
    say which methods work and never why the others do not, because that names
    our own settings.
    """
    response = api_client.get("/api/v1/payments/methods/")

    assert response.status_code == 200
    body = response.content.decode()
    for forbidden in ("SECRET", "HMAC", "API_KEY", "INTEGRATION_ID", "ENABLED", "MERCHANT_CODE"):
        assert forbidden not in body

    codes = [item["code"] for item in response.json()["data"]]
    assert "cod" in codes
    # Gateways are off by default in the test settings, so nothing else shows.
    assert "card" not in codes
