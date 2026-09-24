"""
Bounds on inputs that had none.

Three findings, one root cause: a field that accepted whatever arrived. Two were
reachable without an account at all, and one of them reached Postgres and came
back as a 500, which is a validation failure wearing the wrong clothes.
"""
import json

import pytest

API = "/api/v1"
TRACK = "/api/v1/admin-api/events/track/"


# ------------------------------------------------------------- cart quantity
@pytest.mark.django_db
def test_quantity_above_the_cap_is_a_field_error(api_client, product_with_variant_and_stock, settings):
    variant = product_with_variant_and_stock["variant"]
    over = settings.MAX_CART_ITEM_QUANTITY + 1
    r = api_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": over}, format="json")
    assert r.status_code == 400, r.content
    assert "quantity" in json.dumps(r.json()).lower()


@pytest.mark.django_db
def test_quantity_at_the_cap_is_accepted(api_client, product_with_variant_and_stock, settings):
    variant = product_with_variant_and_stock["variant"]
    r = api_client.post(
        f"{API}/cart/items/",
        {"variant_id": variant.id, "quantity": settings.MAX_CART_ITEM_QUANTITY},
        format="json",
    )
    assert r.status_code == 200, r.content


@pytest.mark.django_db
@pytest.mark.parametrize("quantity", [2**31, 2**31 + 1, 2_000_000_000, 10**18])
def test_a_quantity_too_large_for_the_column_never_reaches_the_database(
    api_client, product_with_variant_and_stock, quantity
):
    """
    2**31 used to get past validation and be refused by Postgres instead:
    `psycopg.errors.NumericValueOutOfRange: integer out of range`, returned to
    the customer as a 500 with no indication of which field was wrong.
    """
    variant = product_with_variant_and_stock["variant"]
    r = api_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": quantity}, format="json")
    assert r.status_code == 400, f"quantity={quantity} returned {r.status_code}"


@pytest.mark.django_db
def test_updating_a_line_is_bounded_too(api_client, product_with_variant_and_stock, settings):
    """The add path and the update path are separate serializers; both need the cap."""
    variant = product_with_variant_and_stock["variant"]
    added = api_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    item_id = added.json()["data"]["items"][0]["id"]

    over = settings.MAX_CART_ITEM_QUANTITY + 1
    r = api_client.patch(f"{API}/cart/items/{item_id}/", {"quantity": over}, format="json")
    assert r.status_code == 400, r.content

    huge = api_client.patch(f"{API}/cart/items/{item_id}/", {"quantity": 2**31}, format="json")
    assert huge.status_code == 400, huge.content


@pytest.mark.django_db
def test_zero_still_removes_a_line(api_client, product_with_variant_and_stock):
    """The cap must not have broken the documented way to delete a line."""
    variant = product_with_variant_and_stock["variant"]
    added = api_client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": 2}, format="json")
    item_id = added.json()["data"]["items"][0]["id"]
    r = api_client.patch(f"{API}/cart/items/{item_id}/", {"quantity": 0}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["data"]["items"] == []


# --------------------------------------------------------- analytics payload
@pytest.mark.django_db
def test_a_megabyte_of_metadata_is_refused(api_client, settings):
    """Measured before the fix: 1,011,890 characters stored, answered 202."""
    from apps.analytics.models import AnalyticsEvent

    big = {f"k{i}": "x" * 1000 for i in range(1000)}
    r = api_client.post(TRACK, {"event_type": "product_view", "metadata": big}, format="json")
    assert r.status_code == 400, f"a 1 MB payload was accepted with {r.status_code}"
    assert AnalyticsEvent.objects.count() == 0


@pytest.mark.django_db
def test_metadata_within_the_limits_still_works(api_client, settings):
    from apps.analytics.models import AnalyticsEvent

    r = api_client.post(
        TRACK,
        {"event_type": "product_view", "metadata": {"source": "homepage", "position": 3}},
        format="json",
    )
    assert r.status_code == 202, r.content
    assert AnalyticsEvent.objects.count() == 1


@pytest.mark.django_db
def test_too_many_metadata_keys_are_refused(api_client, settings):
    many = {f"k{i}": 1 for i in range(settings.ANALYTICS_METADATA_MAX_KEYS + 1)}
    r = api_client.post(TRACK, {"event_type": "product_view", "metadata": many}, format="json")
    assert r.status_code == 400, r.content


@pytest.mark.django_db
def test_deeply_nested_metadata_is_refused(api_client, settings):
    """A small payload can still be expensive to walk, so depth is capped separately."""
    nested = value = {}
    for _ in range(settings.ANALYTICS_METADATA_MAX_DEPTH + 2):
        value["next"] = {}
        value = value["next"]
    r = api_client.post(TRACK, {"event_type": "product_view", "metadata": nested}, format="json")
    assert r.status_code == 400, r.content


# ------------------------------------------------- analytics order ownership
@pytest.mark.django_db
def test_an_anonymous_caller_cannot_attribute_an_event_to_a_real_order(api_client, delivered_order):
    from apps.analytics.models import AnalyticsEvent

    r = api_client.post(
        TRACK, {"event_type": "product_view", "order_id": delivered_order.id}, format="json"
    )
    # Still 202: this is fire-and-forget telemetry, and a 403 would tell the
    # caller the order exists.
    assert r.status_code == 202, r.content
    assert not AnalyticsEvent.objects.filter(order_id=delivered_order.id).exists()


@pytest.mark.django_db
def test_another_customer_cannot_attribute_an_event_to_my_order(other_client, delivered_order):
    from apps.analytics.models import AnalyticsEvent

    r = other_client.post(
        TRACK, {"event_type": "product_view", "order_id": delivered_order.id}, format="json"
    )
    assert r.status_code == 202, r.content
    assert not AnalyticsEvent.objects.filter(order_id=delivered_order.id).exists()


@pytest.mark.django_db
def test_the_owner_can_still_attribute_an_event_to_their_own_order(
    authenticated_client, delivered_order
):
    """The fix must not have thrown away the legitimate case."""
    from apps.analytics.models import AnalyticsEvent

    r = authenticated_client.post(
        TRACK, {"event_type": "product_view", "order_id": delivered_order.id}, format="json"
    )
    assert r.status_code == 202, r.content
    assert AnalyticsEvent.objects.filter(order_id=delivered_order.id).exists()
