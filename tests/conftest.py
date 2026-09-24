"""
Shared pytest fixtures for the Alreef backend test suite.

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/ -v          (Windows)
    ./.venv/Scripts/python.exe -m pytest tests/ -v           (Git Bash)

Requires Postgres reachable at localhost:5433 and Redis at localhost:6379
(see docker-compose.yml). config/settings/testing.py hardcodes the DB
host/port so no env vars are required.
"""
import datetime
from decimal import Decimal

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.brands.models import Brand
from apps.categories.models import Category
from apps.catalog.models import Product, ProductStatus, ProductType, ProductVariant
from apps.inventory.models import Batch
from apps.inventory.services import get_or_create_item
from apps.shipping.models import (
    Governorate,
    RateCalculationType,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)
from apps.users.models import Address
from apps.users.models import User


@pytest.fixture(autouse=True)
def _clear_cache():
    """
    Idempotency keys and DRF ScopedRateThrottle counters both live in the
    default cache. Without resetting it between tests, the "auth" (20/min)
    and "checkout" (30/min) throttle scopes would bleed across tests in the
    same run and cause unrelated failures.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="jane@example.com",
        password="SuperSecret123",
        first_name="Jane",
        last_name="Doe",
    )


@pytest.fixture
def authenticated_client(user):
    """A DRF APIClient carrying a real JWT access token issued for `user`."""
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def other_user(db):
    """A second, unrelated customer. The whole point of the authorization
    suite: everything `user` owns must be invisible to this one."""
    return User.objects.create_user(
        email="mallory@example.com",
        password="SuperSecret123",
        first_name="Mallory",
        last_name="Other",
    )


@pytest.fixture
def other_client(other_user):
    client = APIClient()
    refresh = RefreshToken.for_user(other_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="staff@example.com", password="SuperSecret123", is_staff=True,
    )


@pytest.fixture
def staff_client(staff_user):
    client = APIClient()
    refresh = RefreshToken.for_user(staff_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def governorate(db):
    return Governorate.objects.create(code="cairo", name_en="Cairo", name_ar="القاهرة", is_active=True)


@pytest.fixture
def address(user, governorate):
    return Address.objects.create(
        user=user,
        full_name="Jane Doe",
        phone="+201001234567",
        governorate=governorate,
        street="123 Nile St",
        building="4",
        is_default=True,
    )


@pytest.fixture
def category(db):
    return Category.objects.create(name_en="Honey", name_ar="عسل", slug="honey", is_active=True)


@pytest.fixture
def brand(db):
    return Brand.objects.create(name="Alreef", slug="alreef", is_active=True)


@pytest.fixture
def shipping_setup(governorate):
    """A ShippingZone covering `governorate` with one flat-rate ShippingMethod."""
    zone = ShippingZone.objects.create(name="Greater Cairo", is_active=True)
    zone.governorates.add(governorate)
    method = ShippingMethod.objects.create(
        name="Standard", method_type="standard", is_active=True, sort_order=1,
    )
    rate = ShippingRate.objects.create(
        zone=zone, method=method, calculation_type=RateCalculationType.FLAT,
        base_price=Decimal("30.00"), is_active=True,
    )
    return {"zone": zone, "method": method, "rate": rate}


@pytest.fixture
def product_with_variant_and_stock(category, brand):
    """A published Product + active ProductVariant with 10 units of real stock
    (one Batch expiring a year from now) via apps.inventory.services."""
    product = Product.objects.create(
        name_en="Sidr Honey", name_ar="عسل السدر", slug="sidr-honey", sku="HONEY-SIDR-001",
        category=category, brand=brand, product_type=ProductType.SIMPLE,
        status=ProductStatus.ACTIVE, published=True, price=Decimal("250.00"),
    )
    variant = ProductVariant.objects.create(
        product=product, sku="HONEY-SIDR-001-500G", name_suffix="500g",
        price=Decimal("250.00"), is_active=True, is_default=True,
    )
    item = get_or_create_item(variant)
    batch = Batch.objects.create(
        item=item, batch_number="BATCH-001", quantity=10, initial_quantity=10,
        expiration_date=datetime.date.today() + datetime.timedelta(days=365), is_active=True,
    )
    return {"product": product, "variant": variant, "item": item, "batch": batch}


@pytest.fixture
def delivered_order(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    """Places a real COD order through the API and marches it through the
    legal state machine (CONFIRMED -> ... -> DELIVERED). Shared by
    test_reviews.py and test_returns.py, both of which need a genuinely
    delivered order containing `product_with_variant_and_stock`."""
    import uuid

    from apps.orders.models import Order, OrderStatus
    from apps.orders.services import transition_status

    variant = product_with_variant_and_stock["variant"]
    method = shipping_setup["method"]
    authenticated_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    response = authenticated_client.post(
        "/api/v1/checkout/",
        {"address_id": address.id, "shipping_method_id": method.id, "payment_method": "cod", "coupon_code": ""},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
        order = transition_status(order, status)
    return order


# ---------------------------------------------------------------------------
# Payment gateways
# ---------------------------------------------------------------------------
# No real gateway is ever contacted in the suite. These fixtures enable a
# gateway with fixture credentials and replace the HTTP layer with a recorder,
# so an adapter's request can be inspected and its responses scripted.
#
# The credentials here are obviously fake on purpose. If a test ever passes
# because a real key leaked into the environment, that is a finding, not a pass.

PAYMOB_TEST_CONFIG = {
    "ENABLED": True,
    "LIVE": False,
    "SECRET_KEY": "egy_sk_test_secretkey",
    "PUBLIC_KEY": "egy_pk_test_publickey",
    "API_KEY": "accept_api_key_value",
    "HMAC_SECRET": "HMACSECRET123",
    "INTEGRATION_ID_CARD": "4001",
    "INTEGRATION_ID_WALLET": "4002",
    # Empty by default, mirroring reality: Paymob lists InstaPay as coming soon,
    # so the manual-transfer route is the one that serves InstaPay. Tests that
    # care about the Paymob route set it explicitly.
    "INTEGRATION_ID_INSTAPAY": "",
    "IFRAME_ID": "",
    "BASE_URL": "https://accept.paymob.com",
    "TIMEOUT_SECONDS": 5,
}

INSTAPAY_TEST_CONFIG = {
    "ENABLED": True,
    "ADDRESS": "alreef@instapay",
    "ACCOUNT_NAME": "شركة الريف للمواد الغذائية",
    "CONFIRM_WINDOW_HOURS": 24,
}


def payment_providers_with(**overrides):
    from django.conf import settings as django_settings

    merged = {**django_settings.PAYMENT_PROVIDERS}
    merged.update(overrides)
    return merged


@pytest.fixture
def gateways_enabled(settings):
    """Every method this shop accepts, live, with fixture credentials."""
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB=PAYMOB_TEST_CONFIG, INSTAPAY=INSTAPAY_TEST_CONFIG
    )
    settings.BACKEND_PUBLIC_URL = "https://api.example.test"
    settings.FRONTEND_URL = "https://shop.example.test"
    return settings


@pytest.fixture
def gateway_payment(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    """
    A factory for a real Payment row attached to a real Order.

    The order is placed through checkout as COD — so the totals, the address
    snapshot and the stock reservation are all genuine — and the Payment is then
    relabelled onto the gateway under test. That is far closer to production than
    hand-building a Payment, and it means an amount check in a test is comparing
    against a total the server actually computed.
    """
    import uuid as _uuid

    from apps.payments.models import Payment

    def make(*, provider="paymob", method="card", status=None, quantity=2):
        variant = product_with_variant_and_stock["variant"]
        authenticated_client.post(
            "/api/v1/cart/items/", {"variant_id": variant.id, "quantity": quantity}, format="json"
        )
        response = authenticated_client.post(
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
        payment = Payment.objects.get(pk=response.json()["data"]["payment"]["id"])

        updates = {"provider": provider, "method": method}
        if status is not None:
            updates["status"] = status
        if provider == "paymob":
            updates["provider_reference"] = "210000123"
        elif provider == "instapay":
            updates["provider_reference"] = "IPN-AABBCCDDEEFF"
        Payment.objects.filter(pk=payment.pk).update(**updates)

        if provider != "cod":
            # The order was placed as COD, which confirms it immediately. A card
            # or InstaPay order is NOT confirmed until the payment is, so put the
            # order back where a real one of those would be — otherwise a test
            # asserting "the order stayed unconfirmed" would pass for the wrong
            # reason.
            from apps.orders.models import Order, OrderStatus

            Order.objects.filter(pk=payment.order_id).update(
                status=OrderStatus.PENDING, confirmed_at=None
            )

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        return payment

    return make
