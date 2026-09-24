import datetime

import pytest

from apps.orders.models import Order
from apps.subscriptions.models import (
    Subscription,
    SubscriptionFrequency,
    SubscriptionItem,
    SubscriptionScheduleStatus,
    SubscriptionStatus,
)
from apps.subscriptions.services import (
    cancel_subscription,
    generate_order_for_subscription,
    pause_subscription,
    resume_subscription,
)

pytestmark = pytest.mark.django_db

SUBSCRIPTIONS_URL = "/api/v1/subscriptions/"
SUBSCRIPTIONS_CREATE_URL = "/api/v1/subscriptions/create/"


def _create_subscription(user, address, method, *, frequency=SubscriptionFrequency.MONTHLY, next_order_date=None):
    return Subscription.objects.create(
        user=user, name="Monthly Honey Box", address=address, shipping_method=method, payment_method="cod",
        frequency=frequency, next_order_date=next_order_date or datetime.date.today(),
    )


def test_create_subscription_via_endpoint(authenticated_client, product_with_variant_and_stock, address, shipping_setup):
    variant = product_with_variant_and_stock["variant"]
    payload = {
        "name": "Monthly Honey Box",
        "address_id": address.id,
        "shipping_method_id": shipping_setup["method"].id,
        "payment_method": "cod",
        "frequency": "monthly",
        "start_date": str(datetime.date.today()),
        "items": [{"variant_id": variant.id, "quantity": 2}],
    }

    response = authenticated_client.post(SUBSCRIPTIONS_CREATE_URL, payload, format="json")

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["status"] == SubscriptionStatus.ACTIVE
    assert len(body["items"]) == 1
    subscription = Subscription.objects.get(pk=body["id"])
    assert subscription.items.count() == 1


def test_pause_resume_cancel_transitions(authenticated_client, user, address, shipping_setup):
    subscription = _create_subscription(user, address, shipping_setup["method"])

    pause_response = authenticated_client.post(f"/api/v1/subscriptions/{subscription.id}/pause/")
    assert pause_response.status_code == 200
    assert pause_response.json()["data"]["status"] == SubscriptionStatus.PAUSED

    resume_response = authenticated_client.post(f"/api/v1/subscriptions/{subscription.id}/resume/")
    assert resume_response.status_code == 200
    assert resume_response.json()["data"]["status"] == SubscriptionStatus.ACTIVE

    cancel_response = authenticated_client.post(f"/api/v1/subscriptions/{subscription.id}/cancel/")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["data"]["status"] == SubscriptionStatus.CANCELLED

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELLED


def test_pause_resume_cancel_services_directly(user, address, shipping_setup):
    subscription = _create_subscription(user, address, shipping_setup["method"])

    pause_subscription(subscription)
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.PAUSED

    resume_subscription(subscription)
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.ACTIVE

    cancel_subscription(subscription)
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELLED


def test_generate_order_for_subscription_creates_order_and_advances_next_date(
    user, address, shipping_setup, product_with_variant_and_stock,
):
    variant = product_with_variant_and_stock["variant"]
    start_date = datetime.date.today()
    subscription = _create_subscription(user, address, shipping_setup["method"], next_order_date=start_date)
    SubscriptionItem.objects.create(subscription=subscription, variant=variant, quantity=1)

    schedule_entry = generate_order_for_subscription(subscription)

    assert schedule_entry.status == SubscriptionScheduleStatus.PROCESSED
    assert schedule_entry.order is not None
    assert Order.objects.filter(pk=schedule_entry.order_id).exists()

    subscription.refresh_from_db()
    assert subscription.last_order_date == start_date
    assert subscription.next_order_date == start_date + datetime.timedelta(days=30)  # monthly = 30 days


def test_generate_order_for_subscription_with_no_items_is_skipped_not_crashed(user, address, shipping_setup):
    subscription = _create_subscription(user, address, shipping_setup["method"])

    schedule_entry = generate_order_for_subscription(subscription)

    assert schedule_entry.status == SubscriptionScheduleStatus.SKIPPED
    assert schedule_entry.order is None
    assert schedule_entry.failure_reason
