"""
Staff shipping endpoints: delivery fees per governorate, and the shipment
(carrier + tracking number) staff book on an order.

What is protected:

1. The permission boundary — customers and anonymous callers never reach a
   fee or a shipment.
2. Fees cannot go negative, and changing one governorate's fee never moves
   its neighbours' (zones are shared, so this is the easy thing to get wrong).
3. What staff type on an order is exactly what the customer's tracking
   routes return.

Requires Postgres at localhost:5433 (see docker-compose.yml).
"""
from decimal import Decimal

import pytest
from djmoney.money import Money

from apps.audit.models import AuditLog
from apps.orders.models import Order, OrderStatus
from apps.shipping.models import (
    Governorate,
    RateCalculationType,
    Shipment,
    ShipmentStatus,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)
from apps.shipping.services import get_available_quotes

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin-api"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def shared_zone(db):
    """Two governorates sharing one zone with Standard + Express, like the
    seeded "Rest of Egypt"."""
    aswan = Governorate.objects.create(code="aswan", name_en="Aswan", name_ar="أسوان")
    luxor = Governorate.objects.create(code="luxor", name_en="Luxor", name_ar="الأقصر")
    zone = ShippingZone.objects.create(name="Upper Egypt")
    zone.governorates.set([aswan, luxor])
    standard = ShippingMethod.objects.create(name="Standard", sort_order=1)
    express = ShippingMethod.objects.create(name="Express", method_type="express", sort_order=2)
    ShippingRate.objects.create(
        zone=zone, method=standard, calculation_type=RateCalculationType.FLAT,
        base_price=Money(65, "EGP"), free_shipping_threshold=Money(1500, "EGP"),
    )
    ShippingRate.objects.create(zone=zone, method=express, base_price=Money(90, "EGP"))
    return {"aswan": aswan, "luxor": luxor, "zone": zone, "standard": standard, "express": express}


@pytest.fixture
def order(user):
    return Order.objects.create(
        order_number="ORD-SHIP-1", user=user, status=OrderStatus.PACKED,
        customer_name="Jane Doe", customer_email=user.email, customer_phone="+201001234567",
        shipping_address_snapshot={}, shipping_method_name="Standard", payment_method="cod",
        subtotal=Decimal("100.00"), grand_total=Decimal("130.00"),
    )


def _quote(governorate, method, subtotal=100):
    quotes = get_available_quotes(governorate=governorate, subtotal=Money(subtotal, "EGP"))
    return next(q.price.amount for q in quotes if q.method == method)


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
READ_ROUTES = [
    f"{ADMIN}/shipping/governorates/",
    f"{ADMIN}/shipping/zones/",
    f"{ADMIN}/shipping/rates/",
    f"{ADMIN}/shipping/methods/",
]


@pytest.mark.parametrize("path", READ_ROUTES)
def test_anonymous_cannot_read_shipping(api_client, path):
    assert api_client.get(path).status_code == 401


@pytest.mark.parametrize("path", READ_ROUTES)
def test_customer_cannot_read_shipping(authenticated_client, path):
    assert authenticated_client.get(path).status_code == 403


@pytest.mark.parametrize("path", READ_ROUTES)
def test_staff_can_read_shipping(staff_client, path):
    response = staff_client.get(path)
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_customer_cannot_change_a_fee(authenticated_client, api_client, shared_zone):
    body = {"method": shared_zone["standard"].id, "base_price": "0"}
    url = f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/rate/"
    assert api_client.post(url, body, format="json").status_code == 401
    assert authenticated_client.post(url, body, format="json").status_code == 403
    assert _quote(shared_zone["aswan"], shared_zone["standard"]) == Decimal("65.00")


def test_customer_cannot_book_a_shipment(authenticated_client, order):
    response = authenticated_client.post(
        f"{ADMIN}/orders/{order.order_number}/shipment/", {"tracking_number": "X1"}, format="json"
    )
    assert response.status_code == 403
    assert not Shipment.objects.exists()


# --------------------------------------------------------------------------
# Fees per governorate
# --------------------------------------------------------------------------
def test_governorate_list_shows_each_fee(staff_client, shared_zone):
    rows = staff_client.get(f"{ADMIN}/shipping/governorates/").json()["data"]
    aswan = next(r for r in rows if r["code"] == "aswan")

    assert aswan["zone"]["name"] == "Upper Egypt"
    assert aswan["zone"]["zone_size"] == 2
    prices = {r["method_name"]: r["base_price"] for r in aswan["rates"]}
    assert prices == {"Standard": "65.00", "Express": "90.00"}


def test_governorate_list_is_searchable(staff_client, shared_zone):
    rows = staff_client.get(f"{ADMIN}/shipping/governorates/", {"search": "أسوان"}).json()["data"]
    assert [r["code"] for r in rows] == ["aswan"]


def test_changing_one_governorate_leaves_its_neighbours_alone(staff_client, shared_zone):
    response = staff_client.post(
        f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/rate/",
        {"method": shared_zone["standard"].id, "base_price": "80", "free_shipping_threshold": "2000"},
        format="json",
    )

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["zone"]["zone_size"] == 1
    assert _quote(shared_zone["aswan"], shared_zone["standard"]) == Decimal("80.00")
    # Its other method came along unchanged, and Luxor kept the old fees.
    assert _quote(shared_zone["aswan"], shared_zone["express"]) == Decimal("90.00")
    assert _quote(shared_zone["luxor"], shared_zone["standard"]) == Decimal("65.00")
    # Over the new threshold it is free; quotes still offer each method once.
    assert _quote(shared_zone["aswan"], shared_zone["standard"], subtotal=2000) == Decimal("0")
    quotes = get_available_quotes(governorate=shared_zone["aswan"], subtotal=Money(100, "EGP"))
    assert len(quotes) == 2


def test_apply_to_zone_changes_every_governorate_in_it(staff_client, shared_zone):
    response = staff_client.post(
        f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/rate/",
        {"method": shared_zone["standard"].id, "base_price": "70", "apply_to_zone": True},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert ShippingZone.objects.count() == 1
    assert _quote(shared_zone["luxor"], shared_zone["standard"]) == Decimal("70.00")


def test_a_governorate_with_no_zone_gets_one(staff_client, shared_zone):
    giza = Governorate.objects.create(code="giza", name_en="Giza", name_ar="الجيزة")
    response = staff_client.post(
        f"{ADMIN}/shipping/governorates/{giza.id}/rate/",
        {"method": shared_zone["standard"].id, "base_price": "40"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert _quote(giza, shared_zone["standard"]) == Decimal("40.00")


@pytest.mark.parametrize("field", ["base_price", "free_shipping_threshold"])
def test_a_negative_fee_is_rejected(staff_client, shared_zone, field):
    body = {"method": shared_zone["standard"].id, "base_price": "50", field: "-1"}
    response = staff_client.post(
        f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/rate/", body, format="json"
    )

    assert response.status_code == 400
    assert _quote(shared_zone["aswan"], shared_zone["standard"]) == Decimal("65.00")


def test_a_negative_rate_is_rejected_on_the_rate_route(staff_client, shared_zone):
    rate = ShippingRate.objects.get(zone=shared_zone["zone"], method=shared_zone["standard"])
    response = staff_client.patch(f"{ADMIN}/shipping/rates/{rate.id}/", {"base_price": "-5"}, format="json")

    assert response.status_code == 400
    rate.refresh_from_db()
    assert rate.base_price.amount == Decimal("65.00")


def test_rate_route_updates_and_audits(staff_client, staff_user, shared_zone):
    rate = ShippingRate.objects.get(zone=shared_zone["zone"], method=shared_zone["standard"])
    response = staff_client.patch(f"{ADMIN}/shipping/rates/{rate.id}/", {"base_price": "55"}, format="json")

    assert response.status_code == 200, response.content
    rate.refresh_from_db()
    assert rate.base_price.amount == Decimal("55.00")
    assert AuditLog.objects.filter(actor=staff_user, model_name="ShippingRate", object_id=str(rate.id)).exists()


def test_a_fee_change_is_audited(staff_client, staff_user, shared_zone):
    staff_client.post(
        f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/rate/",
        {"method": shared_zone["standard"].id, "base_price": "80"},
        format="json",
    )

    log = AuditLog.objects.get(actor=staff_user, model_name="ShippingRate")
    assert log.changes["governorate"] == "aswan"
    assert log.changes["base_price"] == "80.00"


def test_turning_a_governorate_off_stops_delivery_there(staff_client, api_client, staff_user, shared_zone):
    response = staff_client.patch(
        f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/", {"is_active": False}, format="json"
    )

    assert response.status_code == 200, response.content
    public = api_client.get("/api/v1/shipping/governorates/").json()["data"]
    assert "aswan" not in [g["code"] for g in public]
    assert AuditLog.objects.filter(actor=staff_user, model_name="Governorate").exists()


def test_governorate_names_are_not_editable(staff_client, shared_zone):
    staff_client.patch(
        f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/", {"name_ar": "مكان تاني"}, format="json"
    )
    shared_zone["aswan"].refresh_from_db()
    assert shared_zone["aswan"].name_ar == "أسوان"


def test_governorates_cannot_be_created_or_deleted(staff_client, shared_zone):
    assert staff_client.post(f"{ADMIN}/shipping/governorates/", {"code": "x"}, format="json").status_code == 405
    assert staff_client.delete(f"{ADMIN}/shipping/governorates/{shared_zone['aswan'].id}/").status_code == 405


def test_a_zone_cannot_take_a_governorate_from_another(staff_client, shared_zone):
    response = staff_client.post(
        f"{ADMIN}/shipping/zones/", {"name": "Aswan only", "governorates": [shared_zone["aswan"].id]}, format="json"
    )
    assert response.status_code == 400


def test_shipping_methods_cannot_be_deleted(staff_client, shared_zone):
    response = staff_client.delete(f"{ADMIN}/shipping/methods/{shared_zone['standard'].id}/")
    assert response.status_code == 405
    assert ShippingMethod.objects.filter(pk=shared_zone["standard"].pk).exists()


# --------------------------------------------------------------------------
# Shipment on an order
# --------------------------------------------------------------------------
def test_staff_books_a_shipment_and_the_customer_sees_it(staff_client, authenticated_client, order, shared_zone):
    response = staff_client.post(
        f"{ADMIN}/orders/{order.order_number}/shipment/",
        {
            "carrier_code": "bosta", "tracking_number": "BST123456789EG",
            "tracking_url": "https://bosta.co/tracking-shipments?shipment-number=BST123456789EG",
            "status": "in_transit",
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    shipments = response.json()["data"]["shipments"]
    assert len(shipments) == 1
    assert shipments[0]["tracking_number"] == "BST123456789EG"
    assert shipments[0]["shipping_method_name"] == "Standard"
    assert shipments[0]["shipped_at"] is not None

    listed = authenticated_client.get(f"/api/v1/shipping/orders/{order.order_number}/shipments/").json()["data"]
    assert listed[0]["carrier_code"] == "bosta"
    assert listed[0]["tracking_url"].startswith("https://bosta.co/")

    tracked = authenticated_client.get("/api/v1/shipping/track/BST123456789EG/").json()["data"]
    assert tracked["status"] == "in_transit"
    assert [e["status"] for e in tracked["events"]] == ["in_transit"]


def test_a_second_save_updates_the_same_shipment(staff_client, staff_user, order, shared_zone):
    url = f"{ADMIN}/orders/{order.order_number}/shipment/"
    staff_client.post(url, {"carrier_code": "aramex", "tracking_number": "ARX1"}, format="json")
    response = staff_client.post(url, {"status": "delivered", "note": "Received by the customer"}, format="json")

    assert response.status_code == 200, response.content
    shipment = Shipment.objects.get(order=order)
    assert shipment.tracking_number == "ARX1"
    assert shipment.status == ShipmentStatus.DELIVERED
    assert shipment.delivered_at is not None
    assert [e.status for e in shipment.events.all()] == ["pending", "delivered"]
    assert AuditLog.objects.filter(actor=staff_user, model_name="Order", object_id=str(order.pk)).count() == 2


def test_another_customer_does_not_see_the_shipment(staff_client, other_client, order, shared_zone):
    staff_client.post(
        f"{ADMIN}/orders/{order.order_number}/shipment/", {"tracking_number": "PRIV-1"}, format="json"
    )
    assert other_client.get(f"/api/v1/shipping/orders/{order.order_number}/shipments/").json()["data"] == []
    assert other_client.get("/api/v1/shipping/track/PRIV-1/").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"tracking_number": "has space"},
        {"tracking_number": "a/b"},
        {"tracking_url": "javascript:alert(1)"},
        {"tracking_url": "ftp://example.com/x"},
        {"status": "teleported"},
        {"carrier_code": "x" * 33},
    ],
)
def test_bad_shipment_input_is_rejected(staff_client, order, shared_zone, body):
    response = staff_client.post(f"{ADMIN}/orders/{order.order_number}/shipment/", body, format="json")
    assert response.status_code == 400
    assert not Shipment.objects.exists()


def test_a_tracking_number_cannot_be_reused_on_another_order(staff_client, order, user, shared_zone):
    other = Order.objects.create(
        order_number="ORD-SHIP-2", user=user, customer_name="Jane", customer_email=user.email,
        customer_phone="+201001234567", payment_method="cod", subtotal=Decimal("1"), grand_total=Decimal("1"),
    )
    staff_client.post(f"{ADMIN}/orders/{order.order_number}/shipment/", {"tracking_number": "DUP-1"}, format="json")
    response = staff_client.post(
        f"{ADMIN}/orders/{other.order_number}/shipment/", {"tracking_number": "DUP-1"}, format="json"
    )
    assert response.status_code == 400


def test_a_cancelled_order_cannot_be_shipped(staff_client, order, shared_zone):
    order.status = OrderStatus.CANCELLED
    order.save(update_fields=["status"])
    response = staff_client.post(
        f"{ADMIN}/orders/{order.order_number}/shipment/", {"tracking_number": "C-1"}, format="json"
    )
    assert response.status_code == 400
    assert not Shipment.objects.exists()


def test_order_detail_lists_shipments(staff_client, order, shared_zone):
    assert staff_client.get(f"{ADMIN}/orders/{order.order_number}/").json()["data"]["shipments"] == []
