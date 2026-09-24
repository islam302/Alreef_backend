"""
Tests for the staff API (`apps.admin_api`), mounted at /api/v1/admin-api/.

Two things are being protected here:

1. The permission boundary. Every route must be invisible to anonymous
   callers and to signed-in *customers* — a customer token reaching an
   admin route is the worst failure this app can have.
2. The business rules stay in the service layer. The admin routes must go
   through `apps.orders.services` / `apps.inventory.services` rather than
   writing rows directly, so the dashboard can never perform a transition
   the storefront considers illegal.

Requires Postgres at localhost:5433 (see docker-compose.yml).
"""
import datetime
from decimal import Decimal

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.catalog.models import Product, ProductStatus
from apps.coupons.models import Coupon
from apps.orders.models import Order, OrderStatus
from apps.reviews.models import Review
from apps.users.models import User

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="staff@alreef.com", password="StaffSecret123",
        first_name="Ahmed", last_name="Ali", is_staff=True,
    )


@pytest.fixture
def staff_client(staff_user):
    client = APIClient()
    refresh = RefreshToken.for_user(staff_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


# Every mutating route, so the permission test cannot silently miss one.
READ_ROUTES = [
    "/api/v1/admin-api/me/",
    "/api/v1/admin-api/overview/",
    "/api/v1/admin-api/sales-chart/",
    "/api/v1/admin-api/orders/",
    "/api/v1/admin-api/returns/",
    "/api/v1/admin-api/products/",
    "/api/v1/admin-api/categories/",
    "/api/v1/admin-api/customers/",
    "/api/v1/admin-api/inventory/",
    "/api/v1/admin-api/inventory/alerts/",
    "/api/v1/admin-api/inventory-movements/",
    "/api/v1/admin-api/batches/",
    "/api/v1/admin-api/coupons/",
    "/api/v1/admin-api/reviews/",
    "/api/v1/admin-api/payments/",
    "/api/v1/admin-api/audit-logs/",
    "/api/v1/admin-api/staff/",
]


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", READ_ROUTES)
def test_anonymous_is_rejected(api_client, path):
    assert api_client.get(path).status_code == 401


@pytest.mark.parametrize("path", READ_ROUTES)
def test_customer_is_forbidden(authenticated_client, path):
    """A perfectly valid customer token must not open any admin route."""
    assert authenticated_client.get(path).status_code == 403


@pytest.mark.parametrize("path", READ_ROUTES)
def test_staff_is_allowed(staff_client, path):
    assert staff_client.get(path).status_code == 200


def test_customer_cannot_mutate(authenticated_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    assert authenticated_client.patch(
        f"/api/v1/admin-api/products/{product.id}/", {"name_ar": "مخترق"}, format="json"
    ).status_code == 403
    product.refresh_from_db()
    assert product.name_ar != "مخترق"


def test_me_returns_the_staff_account(staff_client, staff_user):
    body = staff_client.get("/api/v1/admin-api/me/").json()
    assert body["success"] is True
    assert body["data"]["email"] == staff_user.email
    assert body["data"]["is_staff"] is True
    # The staff serializer must never leak a password hash.
    assert "password" not in body["data"]


# --------------------------------------------------------------------------
# Envelope and pagination
# --------------------------------------------------------------------------
def test_list_uses_the_standard_envelope(staff_client, product_with_variant_and_stock):
    body = staff_client.get("/api/v1/admin-api/products/").json()
    assert set(body) >= {"success", "message", "data", "errors"}
    assert body["success"] is True
    assert body["errors"] is None
    data = body["data"]
    assert set(data) >= {"results", "count", "num_pages", "current_page", "page_size"}
    assert data["count"] == 1


def test_detail_uses_the_standard_envelope(staff_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    body = staff_client.get(f"/api/v1/admin-api/products/{product.id}/").json()
    assert body["success"] is True
    assert body["data"]["sku"] == product.sku
    assert "variants" in body["data"] and "images" in body["data"]


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
def test_overview_reports_catalogue_and_order_counters(staff_client, product_with_variant_and_stock):
    data = staff_client.get("/api/v1/admin-api/overview/").json()["data"]
    for key in (
        "period_revenue", "period_order_count", "lifetime_revenue", "total_orders",
        "completed_orders", "cancelled_orders", "open_returns", "total_products",
        "published_products", "pending_reviews", "low_stock_count",
    ):
        assert key in data, f"overview is missing '{key}'"
    assert data["total_products"] == 1
    assert data["published_products"] == 1


def test_sales_chart_returns_one_point_per_day(staff_client):
    data = staff_client.get("/api/v1/admin-api/sales-chart/", {"days": 7}).json()["data"]
    assert data["days"] == 7
    assert len(data["series"]) == 7
    assert {"date", "revenue", "orders"} <= set(data["series"][0])
    assert "status_breakdown" in data and "payment_breakdown" in data


def test_sales_chart_clamps_an_absurd_range(staff_client):
    data = staff_client.get("/api/v1/admin-api/sales-chart/", {"days": 99999}).json()["data"]
    assert data["days"] == 365


def test_overview_survives_a_junk_days_param(staff_client):
    assert staff_client.get("/api/v1/admin-api/overview/", {"days": "abc"}).status_code == 200


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
def test_staff_sees_orders_belonging_to_other_users(staff_client, delivered_order):
    """The whole reason this API exists: the storefront scopes orders to the
    requesting user, so staff could not see a customer's order at all."""
    body = staff_client.get("/api/v1/admin-api/orders/").json()
    numbers = [row["order_number"] for row in body["data"]["results"]]
    assert delivered_order.order_number in numbers


def test_order_detail_exposes_items_history_and_legal_transitions(staff_client, delivered_order):
    data = staff_client.get(f"/api/v1/admin-api/orders/{delivered_order.order_number}/").json()["data"]
    assert data["order_number"] == delivered_order.order_number
    assert len(data["items"]) >= 1
    assert len(data["status_history"]) >= 1
    # DELIVERED may only move to RETURN_REQUESTED per ORDER_STATUS_TRANSITIONS.
    assert data["allowed_transitions"] == ["return_requested"]


def test_order_status_filter(staff_client, delivered_order):
    hit = staff_client.get("/api/v1/admin-api/orders/", {"status": "delivered"}).json()
    assert hit["data"]["count"] == 1
    miss = staff_client.get("/api/v1/admin-api/orders/", {"status": "cancelled"}).json()
    assert miss["data"]["count"] == 0


def test_order_search_by_number(staff_client, delivered_order):
    body = staff_client.get("/api/v1/admin-api/orders/", {"search": delivered_order.order_number}).json()
    assert body["data"]["count"] == 1


def test_illegal_transition_is_refused_by_the_state_machine(staff_client, delivered_order):
    """DELIVERED -> PENDING is not in ORDER_STATUS_TRANSITIONS."""
    response = staff_client.post(
        f"/api/v1/admin-api/orders/{delivered_order.order_number}/status/",
        {"status": "pending"}, format="json",
    )
    # ERROR_STATUS_MAP maps ORDER_INVALID_TRANSITION to 409 (docs/api/errors.md).
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["errors"]["code"] == "ORDER_INVALID_TRANSITION"
    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.DELIVERED


def test_unknown_status_is_rejected(staff_client, delivered_order):
    response = staff_client.post(
        f"/api/v1/admin-api/orders/{delivered_order.order_number}/status/",
        {"status": "teleported"}, format="json",
    )
    assert response.status_code == 400


def test_legal_transition_records_history_and_audit(staff_client, staff_user, product_with_variant_and_stock, address, shipping_setup, authenticated_client):
    import uuid

    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    created = authenticated_client.post(
        "/api/v1/checkout/",
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id,
         "payment_method": "cod", "coupon_code": ""},
        format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    number = created.json()["data"]["order"]["order_number"]
    order = Order.objects.get(order_number=number)

    target = "confirmed" if order.status == OrderStatus.PENDING else "processing"
    response = staff_client.post(
        f"/api/v1/admin-api/orders/{number}/status/",
        {"status": target, "note": "تم التأكيد تليفونياً"}, format="json",
    )
    assert response.status_code == 200, response.content

    order.refresh_from_db()
    assert order.status == target
    assert order.status_history.filter(to_status=target, changed_by=staff_user).exists()
    assert AuditLog.objects.filter(model_name="Order", object_id=number).exists()


def test_order_note_is_saved(staff_client, delivered_order):
    response = staff_client.patch(
        f"/api/v1/admin-api/orders/{delivered_order.order_number}/notes/",
        {"notes": "العميل طلب التسليم بعد 5 مساءً"}, format="json",
    )
    assert response.status_code == 200
    delivered_order.refresh_from_db()
    assert "5 مساءً" in delivered_order.notes


def test_orders_cannot_be_created_or_deleted(staff_client, delivered_order):
    """Orders are the financial record; the dashboard must not forge one."""
    assert staff_client.post("/api/v1/admin-api/orders/", {}, format="json").status_code in (403, 405)
    assert staff_client.delete(
        f"/api/v1/admin-api/orders/{delivered_order.order_number}/"
    ).status_code in (403, 405)


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------
def test_create_update_and_delete_a_product(staff_client, category, brand):
    created = staff_client.post(
        "/api/v1/admin-api/products/",
        {
            "name_en": "Clover Honey", "name_ar": "عسل البرسيم", "slug": "clover-honey",
            "sku": "HONEY-CLOVER-1", "category": category.id, "brand": brand.id,
            "price": "180.00", "status": "active", "published": True,
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    product_id = created.json()["data"]["id"]
    assert AuditLog.objects.filter(model_name="Product", object_id=str(product_id)).exists()

    updated = staff_client.patch(
        f"/api/v1/admin-api/products/{product_id}/", {"price": "199.00"}, format="json"
    )
    assert updated.status_code == 200
    assert Product.objects.get(pk=product_id).price.amount == Decimal("199.00")

    assert staff_client.delete(f"/api/v1/admin-api/products/{product_id}/").status_code == 200
    assert not Product.objects.filter(pk=product_id).exists()


def test_duplicate_sku_is_rejected_with_field_errors(staff_client, category, product_with_variant_and_stock):
    response = staff_client.post(
        "/api/v1/admin-api/products/",
        {
            "name_en": "Dup", "name_ar": "مكرر", "slug": "dup-sku",
            "sku": product_with_variant_and_stock["product"].sku,
            "category": category.id, "price": "10.00",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "sku" in response.json()["errors"]["fields"]


def test_product_list_filters_and_stock_rollup(staff_client, product_with_variant_and_stock):
    body = staff_client.get("/api/v1/admin-api/products/", {"status": "active"}).json()
    assert body["data"]["count"] == 1
    row = body["data"]["results"][0]
    assert row["stock_state"]["available_quantity"] == 10
    assert row["stock_state"]["is_out_of_stock"] is False
    assert row["variant_count"] == 1

    empty = staff_client.get("/api/v1/admin-api/products/", {"status": "archived"}).json()
    assert empty["data"]["count"] == 0


def test_adding_a_variant_creates_its_inventory_item(staff_client, product_with_variant_and_stock):
    product = product_with_variant_and_stock["product"]
    response = staff_client.post(
        f"/api/v1/admin-api/products/{product.id}/variants/",
        {"sku": "HONEY-SIDR-001-1KG", "name_suffix": "1kg", "price": "450.00"},
        format="json",
    )
    assert response.status_code == 201, response.content
    variant_id = response.json()["data"]["id"]
    from apps.catalog.models import ProductVariant

    variant = ProductVariant.objects.get(pk=variant_id)
    assert hasattr(variant, "inventory_item")


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
def test_category_tree_is_returned_in_tree_order(staff_client, category):
    child = staff_client.post(
        "/api/v1/admin-api/categories/",
        {"name_en": "Sidr", "name_ar": "سدر", "slug": "sidr", "parent": category.id},
        format="json",
    )
    assert child.status_code == 201, child.content

    rows = staff_client.get("/api/v1/admin-api/categories/").json()["data"]
    assert isinstance(rows, list)          # not paginated
    assert [row["slug"] for row in rows] == ["honey", "sidr"]
    assert rows[0]["level"] == 0 and rows[1]["level"] == 1


def test_category_with_products_cannot_be_deleted(staff_client, product_with_variant_and_stock, category):
    response = staff_client.delete(f"/api/v1/admin-api/categories/{category.id}/")
    assert response.status_code == 400
    from apps.categories.models import Category

    assert Category.objects.filter(pk=category.id).exists()


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
def test_customer_list_excludes_staff_and_annotates_spend(staff_client, staff_user, delivered_order, user):
    body = staff_client.get("/api/v1/admin-api/customers/").json()
    emails = [row["email"] for row in body["data"]["results"]]
    assert user.email in emails
    assert staff_user.email not in emails

    row = next(r for r in body["data"]["results"] if r["email"] == user.email)
    assert row["order_count"] == 1
    assert Decimal(row["total_spent"]) > 0


def test_customer_detail_includes_recent_orders_with_item_count(staff_client, delivered_order, user):
    data = staff_client.get(f"/api/v1/admin-api/customers/{user.id}/").json()["data"]
    assert data["email"] == user.email
    assert len(data["recent_orders"]) == 1
    # Regression: recent_orders used to be serialized without the annotation,
    # which silently dropped item_count from the payload.
    assert data["recent_orders"][0]["item_count"] == 1


def test_toggling_a_customer_blocks_and_restores_access(staff_client, user):
    disabled = staff_client.post(f"/api/v1/admin-api/customers/{user.id}/toggle-active/", {}, format="json")
    assert disabled.status_code == 200
    user.refresh_from_db()
    assert user.is_active is False

    staff_client.post(f"/api/v1/admin-api/customers/{user.id}/toggle-active/", {}, format="json")
    user.refresh_from_db()
    assert user.is_active is True


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
def test_stock_can_be_added_and_is_recorded_as_a_movement(staff_client, product_with_variant_and_stock):
    item = product_with_variant_and_stock["item"]
    response = staff_client.post(
        f"/api/v1/admin-api/inventory/{item.id}/adjust/",
        {"quantity_delta": 5, "reason": "توريد جديد", "batch_number": "BATCH-002"},
        format="json",
    )
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.available_quantity == 15
    assert item.movements.filter(movement_type="adjustment", quantity_delta=5).exists()


def test_stock_can_be_written_off_fefo(staff_client, product_with_variant_and_stock):
    item = product_with_variant_and_stock["item"]
    response = staff_client.post(
        f"/api/v1/admin-api/inventory/{item.id}/adjust/",
        {"quantity_delta": -4, "reason": "تالف"}, format="json",
    )
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.available_quantity == 6


def test_cannot_write_off_more_than_is_available(staff_client, product_with_variant_and_stock):
    item = product_with_variant_and_stock["item"]
    response = staff_client.post(
        f"/api/v1/admin-api/inventory/{item.id}/adjust/",
        {"quantity_delta": -999, "reason": "جرد"}, format="json",
    )
    # INSUFFICIENT_STOCK maps to 409 Conflict, same as it does at checkout.
    assert response.status_code == 409
    assert response.json()["errors"]["code"] == "INSUFFICIENT_STOCK"
    item.refresh_from_db()
    assert item.available_quantity == 10


def test_zero_adjustment_is_rejected(staff_client, product_with_variant_and_stock):
    item = product_with_variant_and_stock["item"]
    response = staff_client.post(
        f"/api/v1/admin-api/inventory/{item.id}/adjust/",
        {"quantity_delta": 0, "reason": "لا شيء"}, format="json",
    )
    assert response.status_code == 400


def test_inventory_state_filter(staff_client, product_with_variant_and_stock):
    assert staff_client.get("/api/v1/admin-api/inventory/", {"state": "in"}).json()["data"]["count"] == 1
    assert staff_client.get("/api/v1/admin-api/inventory/", {"state": "out"}).json()["data"]["count"] == 0

    item = product_with_variant_and_stock["item"]
    staff_client.post(
        f"/api/v1/admin-api/inventory/{item.id}/adjust/",
        {"quantity_delta": -10, "reason": "جرد"}, format="json",
    )
    assert staff_client.get("/api/v1/admin-api/inventory/", {"state": "out"}).json()["data"]["count"] == 1


def test_inventory_alerts_group_by_problem(staff_client, product_with_variant_and_stock):
    data = staff_client.get("/api/v1/admin-api/inventory/alerts/").json()["data"]
    assert set(data) == {"low_stock", "out_of_stock", "expiring_soon", "expired"}
    assert isinstance(data["expiring_soon"], list)


def test_expiring_batches_are_reported(staff_client, product_with_variant_and_stock):
    batch = product_with_variant_and_stock["batch"]
    batch.expiration_date = datetime.date.today() + datetime.timedelta(days=10)
    batch.save(update_fields=["expiration_date"])

    data = staff_client.get("/api/v1/admin-api/inventory/alerts/").json()["data"]
    assert any(row["batch_number"] == batch.batch_number for row in data["expiring_soon"])


def test_inventory_settings_can_be_changed(staff_client, product_with_variant_and_stock):
    item = product_with_variant_and_stock["item"]
    response = staff_client.patch(
        f"/api/v1/admin-api/inventory/{item.id}/settings/",
        {"low_stock_threshold": 20, "allow_backorder": True}, format="json",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.low_stock_threshold == 20
    assert item.allow_backorder is True
    # 10 available against a threshold of 20 is now "low".
    assert item.is_low_stock is True


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------
def test_coupon_lifecycle(staff_client):
    created = staff_client.post(
        "/api/v1/admin-api/coupons/",
        {"code": "RAMADAN25", "discount_type": "percentage", "percentage_value": "25.00", "is_active": True},
        format="json",
    )
    assert created.status_code == 201, created.content
    coupon_id = created.json()["data"]["id"]

    patched = staff_client.patch(
        f"/api/v1/admin-api/coupons/{coupon_id}/", {"is_active": False}, format="json"
    )
    assert patched.status_code == 200
    assert Coupon.objects.get(pk=coupon_id).is_active is False

    assert staff_client.delete(f"/api/v1/admin-api/coupons/{coupon_id}/").status_code == 200


def test_percentage_coupon_requires_a_percentage(staff_client):
    response = staff_client.post(
        "/api/v1/admin-api/coupons/",
        {"code": "BROKEN", "discount_type": "percentage"}, format="json",
    )
    assert response.status_code == 400
    assert "percentage_value" in response.json()["errors"]["fields"]


def test_expiration_must_follow_the_start_date(staff_client):
    response = staff_client.post(
        "/api/v1/admin-api/coupons/",
        {
            "code": "BACKWARDS", "discount_type": "fixed_amount", "fixed_amount_value": "50.00",
            "start_date": "2026-12-01T00:00:00Z", "expiration_date": "2026-01-01T00:00:00Z",
        },
        format="json",
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------
def test_review_moderation_toggles_public_visibility(staff_client, product_with_variant_and_stock, user):
    review = Review.objects.create(
        product=product_with_variant_and_stock["product"], user=user,
        rating=5, title="ممتاز", comment="عسل أصلي", approved=False,
    )

    pending = staff_client.get("/api/v1/admin-api/reviews/", {"approved": "false"}).json()
    assert pending["data"]["count"] == 1

    assert staff_client.post(f"/api/v1/admin-api/reviews/{review.id}/approve/", {}, format="json").status_code == 200
    review.refresh_from_db()
    assert review.approved is True

    assert staff_client.post(f"/api/v1/admin-api/reviews/{review.id}/reject/", {}, format="json").status_code == 200
    review.refresh_from_db()
    assert review.approved is False


def test_review_text_cannot_be_edited_by_staff(staff_client, product_with_variant_and_stock, user):
    """Staff moderate reviews; they must not rewrite what a customer said."""
    review = Review.objects.create(
        product=product_with_variant_and_stock["product"], user=user,
        rating=2, comment="مش عاجبني", approved=False,
    )
    staff_client.patch(f"/api/v1/admin-api/reviews/{review.id}/", {"comment": "رائع"}, format="json")
    review.refresh_from_db()
    assert review.comment == "مش عاجبني"


# --------------------------------------------------------------------------
# Returns
# --------------------------------------------------------------------------
def test_return_request_can_be_rejected(staff_client, authenticated_client, delivered_order):
    created = authenticated_client.post(
        f"/api/v1/orders/{delivered_order.order_number}/returns/",
        {
            "reason": "damaged",
            "description": "البرطمان مكسور",
            "items": [{"order_item_id": delivered_order.items.first().id, "quantity": 1}],
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    return_id = created.json()["data"]["id"]

    listed = staff_client.get("/api/v1/admin-api/returns/").json()
    assert listed["data"]["count"] == 1
    assert listed["data"]["results"][0]["order_number"] == delivered_order.order_number

    decided = staff_client.post(
        f"/api/v1/admin-api/returns/{return_id}/decide/",
        {"approve": False, "note": "المنتج سليم"}, format="json",
    )
    assert decided.status_code == 200
    assert decided.json()["data"]["status"] == "rejected"

    delivered_order.refresh_from_db()
    assert delivered_order.status == OrderStatus.DELIVERED


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
def test_audit_log_lists_admin_actions(staff_client, category, brand):
    staff_client.post(
        "/api/v1/admin-api/products/",
        {
            "name_en": "Audited", "name_ar": "مسجّل", "slug": "audited",
            "sku": "AUDIT-1", "category": category.id, "price": "10.00",
        },
        format="json",
    )
    body = staff_client.get("/api/v1/admin-api/audit-logs/", {"model_name": "Product"}).json()
    assert body["data"]["count"] >= 1
    row = body["data"]["results"][0]
    assert row["action"] == "create"
    assert row["actor_name"]
