"""
Dashboard (admin_api) branches the main admin suite does not reach: list
filters, the return-request state machine, product images and variants,
payment actions, and junk query parameters.
"""
import datetime
import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog.models import ProductImage
from apps.orders.models import OrderStatus, ReturnRequest, ReturnRequestStatus
from apps.payments.models import Payment, PaymentStatus, Refund
from apps.reviews.models import Review, StoreReview

pytestmark = pytest.mark.django_db

API = "/api/v1/admin-api"


def _png(name="jar.png"):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 150, 30)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _request_return(client, order):
    response = client.post(
        f"/api/v1/orders/{order.order_number}/returns/",
        {"reason": "damaged", "description": "broken jar",
         "items": [{"order_item_id": order.items.first().id, "quantity": 1}]},
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]["id"]


# --------------------------------------------------------------------------
# Returns: the server, not just the dashboard, enforces the order of steps
# --------------------------------------------------------------------------


def test_return_approve_then_complete_refunds(staff_client, authenticated_client, delivered_order):
    rid = _request_return(authenticated_client, delivered_order)
    assert staff_client.get(f"{API}/returns/", {"status": "requested"}).json()["data"]["count"] == 1
    assert staff_client.post(f"{API}/returns/{rid}/decide/", {"approve": True}, format="json").status_code == 200
    done = staff_client.post(f"{API}/returns/{rid}/complete/", format="json")
    assert done.status_code == 200, done.content
    assert done.json()["data"]["status"] == ReturnRequestStatus.REFUNDED
    delivered_order.refresh_from_db()
    assert delivered_order.status in (OrderStatus.REFUNDED, OrderStatus.PARTIALLY_REFUNDED)


def test_return_cannot_be_decided_twice(staff_client, authenticated_client, delivered_order):
    rid = _request_return(authenticated_client, delivered_order)
    staff_client.post(f"{API}/returns/{rid}/decide/", {"approve": False}, format="json")
    again = staff_client.post(f"{API}/returns/{rid}/decide/", {"approve": True}, format="json")
    assert again.status_code == 409
    assert ReturnRequest.objects.get(pk=rid).status == ReturnRequestStatus.REJECTED


def test_unapproved_return_cannot_be_completed(staff_client, authenticated_client, delivered_order):
    rid = _request_return(authenticated_client, delivered_order)
    response = staff_client.post(f"{API}/returns/{rid}/complete/", format="json")
    assert response.status_code == 409
    assert not Refund.objects.exists()


def test_rejected_return_is_never_refunded_through_a_later_one(staff_client, authenticated_client, delivered_order):
    """
    Reject return #1, the customer files return #2 (the order is back in
    RETURN_REQUESTED), then staff hit `complete` on the REJECTED #1. That used
    to refund #1's items, because nothing but the dashboard's button state
    checked the return's own status.
    """
    first = _request_return(authenticated_client, delivered_order)
    staff_client.post(f"{API}/returns/{first}/decide/", {"approve": False}, format="json")
    _request_return(authenticated_client, delivered_order)

    response = staff_client.post(f"{API}/returns/{first}/complete/", format="json")
    assert response.status_code == 409
    assert not Refund.objects.exists()
    assert ReturnRequest.objects.get(pk=first).status == ReturnRequestStatus.REJECTED


# --------------------------------------------------------------------------
# Products: images, variants, filters
# --------------------------------------------------------------------------


def test_product_image_upload_and_primary_swap(staff_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    first = staff_client.post(f"{API}/products/{product.pk}/images/", {"image": _png(), "is_primary": True}, format="multipart")
    assert first.status_code == 201, first.content
    second = staff_client.post(f"{API}/products/{product.pk}/images/", {"image": _png("b.png"), "is_primary": True}, format="multipart")
    assert second.status_code == 201
    assert ProductImage.objects.get(pk=first.json()["data"]["id"]).is_primary is False

    patched = staff_client.patch(f"{API}/product-images/{first.json()['data']['id']}/", {"is_primary": True}, format="json")
    assert patched.status_code == 200
    assert ProductImage.objects.get(pk=second.json()["data"]["id"]).is_primary is False


@pytest.mark.parametrize("stock", ["abc", "-3"])
def test_add_variant_rejects_bad_initial_stock(staff_client, product_with_variant_and_stock, stock):
    product = product_with_variant_and_stock["product"]
    response = staff_client.post(
        f"{API}/products/{product.pk}/variants/",
        {"sku": f"V-{stock}", "name_suffix": "1kg", "price": "400.00", "initial_stock": stock},
        format="json",
    )
    assert response.status_code == 400


def test_add_variant_with_initial_stock(staff_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    response = staff_client.post(
        f"{API}/products/{product.pk}/variants/",
        {"sku": "V-1KG", "name_suffix": "1kg", "price": "400.00", "initial_stock": 5},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["data"]["stock"]["available_quantity"] == 5


def test_product_list_filters(staff_client, product_with_variant_and_stock, category, brand):
    product = product_with_variant_and_stock["product"]
    product.compare_at_price = Decimal("300.00")
    product.save()
    params = {
        "category": category.pk, "brand": brand.pk, "status": "active",
        "published": "true", "featured": "false", "on_sale": "true",
    }
    body = staff_client.get(f"{API}/products/", params).json()
    assert body["data"]["count"] == 1
    assert staff_client.get(f"{API}/products/", {"published": "false"}).json()["data"]["count"] == 0


def test_category_without_products_can_be_deleted(staff_client):
    created = staff_client.post(f"{API}/categories/", {"name_en": "Dates", "name_ar": "تمر", "slug": "dates"}, format="json")
    assert created.status_code == 201, created.content
    assert staff_client.delete(f"{API}/categories/{created.json()['data']['id']}/").status_code in (200, 204)


# --------------------------------------------------------------------------
# Orders, customers, audit: filters
# --------------------------------------------------------------------------


def test_order_filters(staff_client, delivered_order):
    today = datetime.date.today().isoformat()
    params = {"payment_method": delivered_order.payment_method, "date_from": today, "date_to": today}
    assert staff_client.get(f"{API}/orders/", params).json()["data"]["count"] == 1
    assert staff_client.get(f"{API}/orders/", {"date_from": "2999-01-01"}).json()["data"]["count"] == 0


def test_customer_filters_and_orders(staff_client, delivered_order, user):
    assert staff_client.get(f"{API}/customers/", {"is_active": "true", "has_orders": "true"}).json()["data"]["count"] == 1
    assert staff_client.get(f"{API}/customers/", {"is_active": "false"}).json()["data"]["count"] == 0
    orders = staff_client.get(f"{API}/customers/{user.pk}/orders/")
    assert orders.status_code == 200
    assert orders.json()["data"]["count"] == 1


def test_audit_log_filters(staff_client, delivered_order):
    today = datetime.date.today().isoformat()
    response = staff_client.get(f"{API}/audit-logs/", {"action": "status_change", "date_from": today, "date_to": today})
    assert response.status_code == 200


def test_sales_chart_with_junk_days(staff_client):
    assert staff_client.get(f"{API}/sales-chart/", {"days": "abc"}).status_code == 200


# --------------------------------------------------------------------------
# Inventory, marketing, reviews
# --------------------------------------------------------------------------


def test_inventory_movements_and_batches_filters(staff_client, product_with_variant_and_stock):
    item = product_with_variant_and_stock["item"]
    staff_client.post(f"{API}/inventory/{item.pk}/adjust/", {"quantity_delta": 2, "reason": "restock"}, format="json")
    response = staff_client.get(f"{API}/inventory-movements/", {"item": item.pk, "movement_type": "adjustment"})
    assert response.status_code == 200
    batches = staff_client.get(f"{API}/batches/", {"item": item.pk}).json()["data"]
    assert batches["count"] >= 1


def test_inventory_alerts_with_junk_expiry_days(staff_client, product_with_variant_and_stock):
    response = staff_client.get(f"{API}/inventory/alerts/", {"expiry_days": "soon"})
    assert response.status_code == 200


def test_coupon_active_filter(staff_client):
    staff_client.post(
        f"{API}/coupons/", {"code": "HONEY10", "discount_type": "percentage", "value": "10", "is_active": True},
        format="json",
    )
    assert staff_client.get(f"{API}/coupons/", {"is_active": "false"}).json()["data"]["count"] == 0


def test_review_filters_and_reject(staff_client, product_with_variant_and_stock, user):
    review = Review.objects.create(
        product=product_with_variant_and_stock["product"], user=user, rating=5, comment="ممتاز", approved=True,
    )
    assert staff_client.get(f"{API}/reviews/", {"approved": "true", "rating": 5}).json()["data"]["count"] == 1
    assert staff_client.post(f"{API}/reviews/{review.pk}/reject/").status_code == 200
    review.refresh_from_db()
    assert review.approved is False


def test_store_review_published_filter(staff_client):
    StoreReview.objects.create(customer_name="Mona A.", comment="Great", consent=True, published=True)
    assert staff_client.get(f"{API}/store-reviews/", {"published": "true"}).json()["data"]["count"] == 1
    assert staff_client.get(f"{API}/store-reviews/", {"published": "false"}).json()["data"]["count"] == 0


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------


def test_payment_filters(staff_client, gateway_payment):
    p = gateway_payment(provider="paymob", method="card")
    params = {"status": p.status, "provider": "paymob", "method": "card", "reference": p.merchant_reference}
    assert staff_client.get(f"{API}/payments/", params).json()["data"]["count"] == 1


@pytest.mark.parametrize("amount", ["abc", "-1", "99999"])
def test_admin_refund_rejects_bad_amount(staff_client, gateway_payment, amount):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    response = staff_client.post(f"{API}/payments/{p.pk}/refund/", {"amount": amount}, format="json")
    assert response.status_code == 400


def test_admin_full_refund_defaults_to_the_payment_amount(staff_client, gateway_payment):
    p = gateway_payment(provider="cod", method="cod", status=PaymentStatus.CAPTURED)
    response = staff_client.post(f"{API}/payments/{p.pk}/refund/", {"reason": "spoiled"}, format="json")
    assert response.status_code == 201, response.content
    # A double click with no key is the same refund, not two.
    staff_client.post(f"{API}/payments/{p.pk}/refund/", {"reason": "spoiled"}, format="json")
    assert Refund.objects.filter(payment=p).count() == 1


def test_admin_reconcile_and_settle(staff_client, gateways_enabled, gateway_payment):
    p = gateway_payment(provider="instapay", method="instapay")
    assert staff_client.post(f"{API}/payments/{p.pk}/reconcile/").status_code == 200
    assert staff_client.post(f"{API}/payments/{p.pk}/settle/", {"outcome": "maybe"}, format="json").status_code == 400
    ok = staff_client.post(f"{API}/payments/{p.pk}/settle/", {"outcome": "received", "note": "seen in app"}, format="json")
    assert ok.status_code == 200
    assert Payment.objects.get(pk=p.pk).status == PaymentStatus.CAPTURED


def test_admin_settle_refused(staff_client, gateway_payment):
    p = gateway_payment(provider="cod", method="cod")
    ok = staff_client.post(f"{API}/payments/{p.pk}/settle/", {"outcome": "refused"}, format="json")
    assert ok.status_code == 200
    assert Payment.objects.get(pk=p.pk).status == PaymentStatus.FAILED


def test_payment_methods_report(staff_client, gateways_enabled):
    body = staff_client.get(f"{API}/payment-methods/").json()["data"]
    assert {m["code"] for m in body["methods"]} >= {"cod", "card"}


def test_shipping_rate_zone_filter(staff_client, shipping_setup):
    response = staff_client.get(f"{API}/shipping/rates/", {"zone": shipping_setup["zone"].pk})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
