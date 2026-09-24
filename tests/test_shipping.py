from decimal import Decimal

import pytest

from apps.core.error_codes import ErrorCodes
from apps.orders.models import Order
from apps.shipping.models import (
    City,
    RateCalculationType,
    Shipment,
    ShipmentStatus,
    ShippingMethod,
    ShippingRate,
)
from apps.users.models import User

pytestmark = pytest.mark.django_db

GOVERNORATES_URL = "/api/v1/shipping/governorates/"
CITIES_URL = "/api/v1/shipping/cities/"
QUOTES_URL = "/api/v1/shipping/quotes/"


def _track_url(tracking_number):
    return f"/api/v1/shipping/track/{tracking_number}/"


def test_list_governorates_is_public(api_client, governorate):
    response = api_client.get(GOVERNORATES_URL)

    assert response.status_code == 200
    # Enveloped like everything else. It was not, once: pagination_class=None
    # is right for a 27-row reference list, but the envelope is applied BY the
    # pagination class, so switching it off returned a bare array and the
    # storefront's address form read `undefined` — checkout unreachable.
    body = response.json()
    assert set(body.keys()) == {"success", "message", "data", "errors"}
    codes = [g["code"] for g in body["data"]]
    assert governorate.code in codes


def test_inactive_governorate_excluded_from_list(api_client, governorate):
    from apps.shipping.models import Governorate

    Governorate.objects.create(code="inactive-gov", name_en="Inactive", name_ar="غير نشط", is_active=False)

    response = api_client.get(GOVERNORATES_URL)

    codes = [g["code"] for g in response.json()["data"]]
    assert "inactive-gov" not in codes


def test_list_cities_filtered_by_governorate(api_client, governorate):
    from apps.shipping.models import Governorate

    other_gov = Governorate.objects.create(code="giza", name_en="Giza", name_ar="الجيزة", is_active=True)
    City.objects.create(governorate=governorate, name_en="Nasr City", name_ar="مدينة نصر", is_active=True)
    City.objects.create(governorate=other_gov, name_en="Dokki", name_ar="الدقي", is_active=True)

    response = api_client.get(CITIES_URL, {"governorate": governorate.code})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"success", "message", "data", "errors"}
    names = [c["name_en"] for c in body["data"]]
    assert names == ["Nasr City"]


def test_shipping_quote_flat_rate(api_client, shipping_setup):
    response = api_client.get(QUOTES_URL, {"governorate": shipping_setup["zone"].governorates.first().code, "subtotal": "100"})

    assert response.status_code == 200
    quotes = response.json()["data"]
    assert len(quotes) == 1
    assert quotes[0]["price"] == "30.00"
    assert quotes[0]["is_free"] is False


def test_shipping_quote_free_shipping_threshold_met(api_client, shipping_setup, governorate):
    method = ShippingMethod.objects.create(name="Express", method_type="express", is_active=True, sort_order=2)
    ShippingRate.objects.create(
        zone=shipping_setup["zone"], method=method, calculation_type=RateCalculationType.FLAT,
        base_price=Decimal("50.00"), free_shipping_threshold=Decimal("500.00"), is_active=True,
    )

    below = api_client.get(QUOTES_URL, {"governorate": governorate.code, "subtotal": "100"})
    above = api_client.get(QUOTES_URL, {"governorate": governorate.code, "subtotal": "600"})

    below_express = next(q for q in below.json()["data"] if q["method_name"] == "Express")
    above_express = next(q for q in above.json()["data"] if q["method_name"] == "Express")

    assert below_express["price"] == "50.00"
    assert below_express["is_free"] is False
    assert Decimal(above_express["price"]) == Decimal("0")
    assert above_express["is_free"] is True


def test_shipping_quote_unknown_governorate_fails(api_client):
    response = api_client.get(QUOTES_URL, {"governorate": "nowhere", "subtotal": "100"})

    assert response.status_code == 409
    assert response.json()["errors"]["code"] == ErrorCodes.SHIPPING_UNAVAILABLE


def _make_order(user, address):
    return Order.objects.create(
        order_number=f"ORD-TEST-{user.id}",
        user=user,
        customer_name="Jane Doe",
        customer_email=user.email,
        customer_phone="+201001234567",
        shipping_address_snapshot={},
        payment_method="cod",
        subtotal=Decimal("100.00"),
        grand_total=Decimal("130.00"),
    )


def test_shipment_tracking_requires_authentication(api_client):
    response = api_client.get(_track_url("TRACK-1"))

    assert response.status_code == 401


def test_shipment_tracking_returns_owned_shipment(authenticated_client, user, address, shipping_setup):
    order = _make_order(user, address)
    shipment = Shipment.objects.create(
        order=order, shipping_method=shipping_setup["method"], tracking_number="TRACK-OWN-1",
        status=ShipmentStatus.IN_TRANSIT,
    )

    response = authenticated_client.get(_track_url(shipment.tracking_number))

    assert response.status_code == 200
    assert response.json()["data"]["tracking_number"] == "TRACK-OWN-1"
    assert response.json()["data"]["status"] == ShipmentStatus.IN_TRANSIT


def test_shipment_tracking_other_users_order_returns_404(authenticated_client, address, shipping_setup):
    other_user = User.objects.create_user(email="other@example.com", password="SuperSecret123")
    order = _make_order(other_user, address)
    shipment = Shipment.objects.create(
        order=order, shipping_method=shipping_setup["method"], tracking_number="TRACK-OTHER-1",
    )

    response = authenticated_client.get(_track_url(shipment.tracking_number))

    assert response.status_code == 404
    assert response.json()["errors"]["code"] == ErrorCodes.NOT_FOUND
