"""
Staff-facing endpoints, all mounted under /api/v1/admin-api/ and all gated
behind IsAdminUser.

Business rules are NOT reimplemented here. Order transitions go through
`apps.orders.services`, refunds through `apps.payments.services`, stock
through `apps.inventory.services` / `apps.admin_api.services` - so the
dashboard and the storefront can never disagree about what is legal.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, DecimalField, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from djmoney.money import Money
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status as http_status
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView

from apps.admin_api import services as admin_services
from apps.admin_api.mixins import (
    DeletableEnvelopeViewSet,
    EnvelopeViewSet,
    ReadOnlyEnvelopeViewSet,
)
from apps.admin_api.serializers import (
    AdminAuditLogSerializer,
    AdminBatchSerializer,
    AdminCategorySerializer,
    AdminCouponSerializer,
    AdminCustomerDetailSerializer,
    AdminCustomerListSerializer,
    AdminInventoryItemSerializer,
    AdminInventoryMovementSerializer,
    AdminOrderDetailSerializer,
    AdminOrderListSerializer,
    AdminPaymentSerializer,
    AdminProductImageSerializer,
    AdminProductListSerializer,
    AdminProductSerializer,
    AdminProductVariantSerializer,
    AdminRefundSerializer,
    AdminReturnRequestSerializer,
    AdminReviewSerializer,
    AdminStaffSerializer,
    OrderCancelAdminSerializer,
    OrderNoteSerializer,
    OrderStatusChangeSerializer,
    ReturnDecisionSerializer,
    StockAdjustmentSerializer,
)
from apps.audit.models import AuditAction, AuditLog
from apps.catalog.models import Product, ProductImage, ProductVariant
from apps.categories.models import Category
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.coupons.models import Coupon
from apps.inventory.models import Batch, InventoryItem, InventoryMovement
from apps.inventory.services import get_expired_batches, get_or_create_item
from apps.orders.models import Order, OrderStatus, ReturnRequest
from apps.orders.services import complete_return_and_refund, decide_return_request, transition_status
from apps.payments.models import Payment
from apps.reviews.models import Review
from apps.users.models import User


def _parse_date_range(request):
    """Shared ?date_from=&date_to= handling. Returns (from, to) as dates or None."""
    return parse_date(request.query_params.get("date_from", "") or ""), parse_date(
        request.query_params.get("date_to", "") or ""
    )


# ==========================================================================
# Dashboard
# ==========================================================================
@extend_schema(tags=["Admin"], responses={200: OpenApiTypes.OBJECT})
class AdminOverviewView(APIView):
    """KPI block for the dashboard home."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        return success_response(admin_services.get_overview(days=max(1, min(days, 365))))


@extend_schema(tags=["Admin"], responses={200: OpenApiTypes.OBJECT})
class AdminSalesChartView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        return success_response({
            **admin_services.get_sales_timeseries(days=days),
            "status_breakdown": admin_services.get_status_breakdown(),
            "payment_breakdown": admin_services.get_payment_method_breakdown(days=days),
        })


# ==========================================================================
# Orders
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminOrderViewSet(ReadOnlyEnvelopeViewSet):
    """
    Orders are never created or deleted from the dashboard - they are the
    financial record. Staff move them through the state machine instead.

    POST and PATCH are allowed only so the `status`, `cancel` and `notes`
    actions can be routed; the base class has no create/update/destroy, so
    the router never builds those routes.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
    serializer_class = AdminOrderDetailSerializer
    list_serializer_class = AdminOrderListSerializer
    lookup_field = "order_number"
    lookup_value_regex = "[^/]+"
    audit_model_name = "Order"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["order_number", "customer_name", "customer_email", "customer_phone"]
    ordering_fields = ["created_at", "grand_total", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = (
            Order.objects.select_related("user")
            .prefetch_related("items", "status_history__changed_by", "payments__refunds")
            .annotate(item_count=Count("items", distinct=True))
        )
        params = self.request.query_params

        statuses = [s for s in params.get("status", "").split(",") if s]
        if statuses:
            queryset = queryset.filter(status__in=statuses)

        payment_methods = [m for m in params.get("payment_method", "").split(",") if m]
        if payment_methods:
            queryset = queryset.filter(payment_method__in=payment_methods)

        date_from, date_to = _parse_date_range(self.request)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, order_number=None):
        order = self.get_object()
        serializer = OrderStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = transition_status(
            order,
            serializer.validated_data["status"],
            changed_by=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        order = self.get_queryset().get(pk=order.pk)
        return success_response(AdminOrderDetailSerializer(order).data, message="Order status updated.")

    @action(detail=True, methods=["post"])
    def cancel(self, request, order_number=None):
        from apps.orders.services import cancel_order

        order = self.get_object()
        serializer = OrderCancelAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = cancel_order(order, reason=serializer.validated_data["reason"], cancelled_by=request.user)
        order = self.get_queryset().get(pk=order.pk)
        return success_response(AdminOrderDetailSerializer(order).data, message="Order cancelled.")

    @action(detail=True, methods=["patch"], url_path="notes")
    def set_notes(self, request, order_number=None):
        order = self.get_object()
        serializer = OrderNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order.notes = serializer.validated_data["notes"]
        order.save(update_fields=["notes"])
        self._audit(AuditAction.UPDATE, order, {"notes": order.notes})
        return success_response(AdminOrderDetailSerializer(order).data, message="Note saved.")


@extend_schema(tags=["Admin"])
class AdminReturnViewSet(ReadOnlyEnvelopeViewSet):
    """Read plus the two decisions; a return is always opened by a customer."""

    http_method_names = ["get", "post", "head", "options"]
    serializer_class = AdminReturnRequestSerializer
    audit_model_name = "ReturnRequest"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["order__order_number", "order__customer_name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = ReturnRequest.objects.select_related("order", "user").prefetch_related("items")
        statuses = [s for s in self.request.query_params.get("status", "").split(",") if s]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        return queryset

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        return_request = self.get_object()
        serializer = ReturnDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return_request = decide_return_request(
            return_request=return_request,
            approve=serializer.validated_data["approve"],
            decided_by=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return success_response(AdminReturnRequestSerializer(return_request).data, message="Return updated.")

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        return_request = self.get_object()
        return_request = complete_return_and_refund(return_request=return_request, processed_by=request.user)
        return success_response(AdminReturnRequestSerializer(return_request).data, message="Return refunded.")


# ==========================================================================
# Catalog
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminProductViewSet(EnvelopeViewSet):
    serializer_class = AdminProductSerializer
    list_serializer_class = AdminProductListSerializer
    audit_model_name = "Product"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name_ar", "name_en", "sku", "barcode"]
    ordering_fields = ["created_at", "price", "name_ar"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("category", "brand")
            .prefetch_related(
                "images",
                "variants",
                # The list serializer sums `available_quantity` and reads
                # `is_low_stock` for every variant to build one stock badge.
                # Those are child-table sums, so prefetching the batches and
                # reservations (as this used to) bought nothing — `aggregate()`
                # runs its own query and never looks at prefetched rows. Pulling
                # the inventory items through `with_stock()` instead hands the
                # serializer totals that are already computed.
                Prefetch("variants__inventory_item", queryset=InventoryItem.objects.with_stock()),
            )
            .annotate(variant_count=Count("variants", distinct=True))
        )
        params = self.request.query_params

        if params.get("category"):
            queryset = queryset.filter(category_id=params["category"])
        if params.get("brand"):
            queryset = queryset.filter(brand_id=params["brand"])
        statuses = [s for s in params.get("status", "").split(",") if s]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        if params.get("published") in {"true", "false"}:
            queryset = queryset.filter(published=params["published"] == "true")
        if params.get("featured") in {"true", "false"}:
            queryset = queryset.filter(featured=params["featured"] == "true")
        return queryset

    @action(detail=True, methods=["post"], url_path="images")
    def upload_image(self, request, pk=None):
        product = self.get_object()
        serializer = AdminProductImageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image = serializer.save(product=product)
        if image.is_primary:
            product.images.exclude(pk=image.pk).update(is_primary=False)
        self._audit(AuditAction.UPDATE, product, {"image_added": image.pk})
        return success_response(AdminProductImageSerializer(image).data, message="Image uploaded.", status=201)

    @action(detail=True, methods=["post"], url_path="variants")
    def add_variant(self, request, pk=None):
        product = self.get_object()
        serializer = AdminProductVariantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        variant = serializer.save(product=product)
        get_or_create_item(variant)
        self._audit(AuditAction.CREATE, product, {"variant_added": variant.sku})
        return success_response(AdminProductVariantSerializer(variant).data, message="Variant created.", status=201)


@extend_schema(tags=["Admin"])
class AdminProductImageViewSet(EnvelopeViewSet):
    """Update alt text / primary flag / ordering, or delete an image.
    Images are uploaded through the product's own `images` action."""

    http_method_names = ["patch", "delete", "head", "options"]
    serializer_class = AdminProductImageSerializer
    audit_model_name = "ProductImage"
    queryset = ProductImage.objects.select_related("product")

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        image = self.get_object()
        if image.is_primary:
            ProductImage.objects.filter(product=image.product).exclude(pk=image.pk).update(is_primary=False)
        return response


@extend_schema(tags=["Admin"])
class AdminProductVariantViewSet(EnvelopeViewSet):
    http_method_names = ["get", "patch", "delete", "head", "options"]
    serializer_class = AdminProductVariantSerializer
    audit_model_name = "ProductVariant"
    queryset = ProductVariant.objects.select_related("product", "inventory_item")


@extend_schema(tags=["Admin"])
class AdminCategoryViewSet(EnvelopeViewSet):
    serializer_class = AdminCategorySerializer
    audit_model_name = "Category"
    pagination_class = None
    filter_backends = [filters.SearchFilter]
    search_fields = ["name_ar", "name_en", "slug"]

    def get_queryset(self):
        return Category.objects.annotate(product_count=Count("products", distinct=True)).order_by("tree_id", "lft")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        return success_response(self.get_serializer(queryset, many=True).data)

    def destroy(self, request, *args, **kwargs):
        category = self.get_object()
        if category.products.exists():
            raise ApplicationError(
                ErrorCodes.VALIDATION_ERROR,
                "Cannot delete a category that still has products. Move them first.",
            )
        return super().destroy(request, *args, **kwargs)


# ==========================================================================
# Customers
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminCustomerViewSet(ReadOnlyEnvelopeViewSet):
    """Customers register themselves; staff may only look and disable."""

    http_method_names = ["get", "post", "head", "options"]
    serializer_class = AdminCustomerDetailSerializer
    list_serializer_class = AdminCustomerListSerializer
    audit_model_name = "User"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["email", "first_name", "last_name", "phone"]
    ordering_fields = ["date_joined", "order_count", "total_spent"]
    ordering = ["-date_joined"]

    def get_queryset(self):
        queryset = User.objects.filter(is_staff=False).select_related("profile").annotate(
            order_count=Count("orders", distinct=True),
            total_spent=Coalesce(
                Sum("orders__grand_total", filter=~Q(orders__status=OrderStatus.CANCELLED)),
                Decimal("0"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )
        params = self.request.query_params
        if params.get("is_active") in {"true", "false"}:
            queryset = queryset.filter(is_active=params["is_active"] == "true")
        if params.get("has_orders") == "true":
            queryset = queryset.filter(order_count__gt=0)
        return queryset

    @action(detail=True, methods=["post"], url_path="toggle-active")
    def toggle_active(self, request, pk=None):
        customer = self.get_object()
        customer.is_active = not customer.is_active
        customer.save(update_fields=["is_active"])
        self._audit(AuditAction.UPDATE, customer, {"is_active": customer.is_active})
        return success_response(
            self.get_serializer(customer).data,
            message="Customer enabled." if customer.is_active else "Customer disabled.",
        )

    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        customer = self.get_object()
        queryset = customer.orders.annotate(item_count=Count("items", distinct=True))
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(AdminOrderListSerializer(page, many=True).data)


# ==========================================================================
# Inventory
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminInventoryViewSet(ReadOnlyEnvelopeViewSet):
    """An InventoryItem is created alongside its variant, never by hand.
    Stock only moves through `adjust`, so every change leaves a movement."""

    http_method_names = ["get", "post", "patch", "head", "options"]
    serializer_class = AdminInventoryItemSerializer
    audit_model_name = "InventoryItem"
    filter_backends = [filters.SearchFilter]
    search_fields = ["variant__sku", "variant__product__name_ar", "variant__product__name_en"]

    def get_queryset(self):
        # `with_stock()` annotates the totals this serializer renders. Without
        # it each row re-queries its batches and reservations several times over
        # — the list was ~150 queries for 20 rows. The batches/reservations
        # prefetch that used to be here never helped: `aggregate()` issues its
        # own query and ignores prefetched objects entirely.
        return (
            InventoryItem.objects.with_stock()
            .select_related("variant__product")
            .order_by("variant__product__name_ar")
        )

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # Filtered in SQL against the annotations. This used to load every
        # inventory row and test each one in Python, so the cost of viewing one
        # page grew with the size of the whole catalogue.
        return queryset.for_state(self.request.query_params.get("state"))

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        item = self.get_object()
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        movement = admin_services.adjust_stock(
            item=item,
            quantity_delta=data["quantity_delta"],
            reason=data["reason"],
            notes=data.get("notes", ""),
            batch_number=data.get("batch_number", ""),
            expiration_date=data.get("expiration_date"),
            actor=request.user,
        )
        self._audit(AuditAction.UPDATE, item, {"quantity_delta": data["quantity_delta"], "reason": data["reason"]})
        item.refresh_from_db()
        return success_response(
            {"movement": AdminInventoryMovementSerializer(movement).data,
             "item": AdminInventoryItemSerializer(item).data},
            message="Stock adjusted.",
        )

    @action(detail=True, methods=["patch"], url_path="settings")
    def update_settings(self, request, pk=None):
        item = self.get_object()
        for field in ("track_inventory", "low_stock_threshold", "allow_backorder"):
            if field in request.data:
                setattr(item, field, request.data[field])
        item.save(update_fields=["track_inventory", "low_stock_threshold", "allow_backorder"])
        self._audit(AuditAction.UPDATE, item, {"settings": request.data})
        return success_response(self.get_serializer(item).data, message="Saved.")

    @action(detail=False, methods=["get"], url_path="alerts")
    def alerts(self, request):
        """Everything that needs a human: low stock, out of stock, expiry."""
        items = list(self.get_queryset())
        today = timezone.now().date()
        horizon = today + timedelta(days=int(request.query_params.get("expiry_days", 30) or 30))
        expiring = (
            Batch.objects.filter(
                is_active=True, quantity__gt=0,
                expiration_date__gte=today, expiration_date__lte=horizon,
            )
            .select_related("item__variant")
            .order_by("expiration_date")[:50]
        )
        return success_response({
            "low_stock": AdminInventoryItemSerializer([i for i in items if i.is_low_stock], many=True).data,
            "out_of_stock": AdminInventoryItemSerializer([i for i in items if i.is_out_of_stock], many=True).data,
            "expiring_soon": AdminBatchSerializer(expiring, many=True).data,
            "expired": AdminBatchSerializer(
                get_expired_batches().select_related("item__variant")[:50], many=True
            ).data,
        })


@extend_schema(tags=["Admin"])
class AdminInventoryMovementViewSet(ReadOnlyEnvelopeViewSet):
    serializer_class = AdminInventoryMovementSerializer

    def get_queryset(self):
        queryset = InventoryMovement.objects.select_related("item__variant", "created_by")
        if self.request.query_params.get("item"):
            queryset = queryset.filter(item_id=self.request.query_params["item"])
        if self.request.query_params.get("movement_type"):
            queryset = queryset.filter(movement_type=self.request.query_params["movement_type"])
        return queryset


@extend_schema(tags=["Admin"])
class AdminBatchViewSet(EnvelopeViewSet):
    serializer_class = AdminBatchSerializer
    audit_model_name = "Batch"

    def get_queryset(self):
        queryset = Batch.objects.select_related("item__variant")
        if self.request.query_params.get("item"):
            queryset = queryset.filter(item_id=self.request.query_params["item"])
        return queryset


# ==========================================================================
# Marketing
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminCouponViewSet(EnvelopeViewSet):
    serializer_class = AdminCouponSerializer
    audit_model_name = "Coupon"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["code", "description"]
    ordering = ["-created_at"]

    def get_queryset(self):
        # NOT `times_used`: Coupon already defines that as a read-only
        # property, and Django assigns annotations with setattr, which
        # raises "property has no setter" the moment a row is returned.
        queryset = Coupon.objects.annotate(usage_count=Count("usages", distinct=True))
        if self.request.query_params.get("is_active") in {"true", "false"}:
            queryset = queryset.filter(is_active=self.request.query_params["is_active"] == "true")
        return queryset


@extend_schema(tags=["Admin"])
class AdminReviewViewSet(DeletableEnvelopeViewSet):
    """Moderation only: approve, hide or remove. Staff cannot write or edit
    a review, because it is a customer's words."""

    http_method_names = ["get", "post", "delete", "head", "options"]
    serializer_class = AdminReviewSerializer
    audit_model_name = "Review"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "comment", "product__name_ar", "user__email"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = Review.objects.select_related("product", "user")
        approved = self.request.query_params.get("approved")
        if approved in {"true", "false"}:
            queryset = queryset.filter(approved=approved == "true")
        if self.request.query_params.get("rating"):
            queryset = queryset.filter(rating=self.request.query_params["rating"])
        return queryset

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        review = self.get_object()
        review.approved = True
        review.save(update_fields=["approved"])
        self._audit(AuditAction.UPDATE, review, {"approved": True})
        return success_response(self.get_serializer(review).data, message="Review approved.")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        review = self.get_object()
        review.approved = False
        review.save(update_fields=["approved"])
        self._audit(AuditAction.UPDATE, review, {"approved": False})
        return success_response(self.get_serializer(review).data, message="Review hidden.")


# ==========================================================================
# Payments
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminPaymentViewSet(ReadOnlyEnvelopeViewSet):
    """Payments are written by the checkout and webhook flows. The dashboard
    reads them and may refund; it must never author one."""

    http_method_names = ["get", "post", "head", "options"]
    serializer_class = AdminPaymentSerializer
    audit_model_name = "Payment"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["order__order_number", "provider_reference"]
    ordering = ["-created_at"]

    search_fields_extra = ["merchant_reference"]

    def get_queryset(self):
        queryset = Payment.objects.select_related("order").prefetch_related(
            "refunds", "transactions"
        )
        params = self.request.query_params
        statuses = [s for s in params.get("status", "").split(",") if s]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        if params.get("provider"):
            queryset = queryset.filter(provider=params["provider"])
        if params.get("method"):
            queryset = queryset.filter(method=params["method"])
        # The two references staff actually have in hand: one from a provider's
        # dashboard, one from our own order.
        if params.get("reference"):
            reference = params["reference"].strip()
            queryset = queryset.filter(
                Q(provider_reference=reference) | Q(merchant_reference=reference)
            )
        return queryset

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """
        Full or partial refund. Delegates to apps.payments.services so the
        provider call, idempotency and ledger rows behave exactly as they do
        for the existing customer-facing refund route.
        """
        from apps.payments.services import create_refund

        payment = self.get_object()
        raw_amount = request.data.get("amount")
        try:
            amount = Money(Decimal(str(raw_amount)), payment.amount.currency) if raw_amount else payment.amount
        except (InvalidOperation, TypeError):
            raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "Invalid refund amount.")
        if amount.amount <= 0 or amount > payment.amount:
            raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "Refund must be between 0 and the payment amount.")

        refund = create_refund(
            payment=payment,
            amount=amount,
            reason=request.data.get("reason", "Admin refund"),
            destination=request.data.get("destination", "original_method"),
            # Without a key, a double-click is two refunds. The dashboard sends
            # one; this fallback keys on the payment and amount so even a client
            # that forgets cannot pay out twice for the same request.
            idempotency_key=request.headers.get("Idempotency-Key")
            or f"admin:{payment.pk}:{amount.amount}:{request.data.get('reason', '')}",
            actor=request.user,
        )
        self._audit(AuditAction.REFUND, payment, {"amount": str(amount.amount), "refund_id": refund.pk})
        return success_response(AdminRefundSerializer(refund).data, message="Refund recorded.", status=201)

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        """
        Ask the gateway what actually happened and apply it.

        The tool for the support case that otherwise needs a database console: a
        customer insisting they paid, a payment stuck waiting because a webhook
        never arrived.
        """
        from apps.payments.services import reconcile_payment

        payment = reconcile_payment(self.get_object(), actor=request.user)
        return success_response(
            AdminPaymentSerializer(payment).data, message="Reconciled with the provider."
        )

    @action(detail=True, methods=["post"], url_path="settle")
    def settle(self, request, pk=None):
        """
        Confirm a manually-settled payment arrived, or did not.

        Cash on delivery and InstaPay. For COD this is usually implicit (moving an
        order to DELIVERED captures it) and this covers the exceptions; for
        InstaPay it is the only thing that will ever confirm the payment, and the
        order is not fulfilled until it runs.
        """
        from apps.payments.services import record_manual_receipt, record_manual_refusal

        payment = self.get_object()
        outcome = str(request.data.get("outcome") or "").strip()
        note = str(request.data.get("note") or "")[:500]

        # "collected" is the natural word for cash and is accepted as a synonym.
        if outcome in ("received", "collected"):
            payment = record_manual_receipt(payment, actor=request.user, note=note)
            message = "Payment recorded as received."
        elif outcome == "refused":
            payment = record_manual_refusal(payment, actor=request.user, reason=note)
            message = "Recorded that no payment arrived."
        else:
            raise ApplicationError(
                ErrorCodes.VALIDATION_ERROR, "outcome must be 'received' or 'refused'."
            )

        return success_response(AdminPaymentSerializer(payment).data, message=message)


@extend_schema(tags=["Admin"])
class AdminPaymentMethodsView(APIView):
    """
    GET /api/v1/admin-api/payment-methods/

    Every payment method and its real state — including, for staff only, exactly
    which setting is empty on the ones that are unavailable. The public
    `/payments/methods/` endpoint deliberately never says that, because it names
    our configuration; here it is the whole point, so an operator can see why
    Paymob is not showing without reading the server's environment.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.payments import config as payment_config
        from apps.payments.webhooks import all_webhook_urls

        methods = []
        # One entry per method. InstaPay has two possible routes and the dashboard
        # should show which one is in play, not both.
        for method in payment_config.method_codes():
            state = payment_config.availability(method)
            spec = state.spec
            can_refund, refund_note = payment_config.refund_available(method)
            methods.append(
                {
                    "code": spec.method,
                    "label_ar": spec.label_ar,
                    "label_en": spec.label_en,
                    "gateway": spec.gateway,
                    "available": state.available,
                    "reason": state.reason,
                    "missing_settings": state.missing,
                    "route": spec.gateway,
                    "refundable_via_api": can_refund,
                    "refund_note": refund_note,
                    "redirects": spec.redirects,
                }
            )

        return success_response(
            {
                "methods": methods,
                # So an operator can copy them into a provider dashboard without
                # guessing at the host.
                "webhook_urls": all_webhook_urls(),
            }
        )


# ==========================================================================
# System
# ==========================================================================
@extend_schema(tags=["Admin"])
class AdminAuditLogViewSet(ReadOnlyEnvelopeViewSet):
    serializer_class = AdminAuditLogSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["model_name", "object_id", "actor__email", "path"]

    def get_queryset(self):
        queryset = AuditLog.objects.select_related("actor")
        params = self.request.query_params
        if params.get("action"):
            queryset = queryset.filter(action=params["action"])
        if params.get("model_name"):
            queryset = queryset.filter(model_name=params["model_name"])
        date_from, date_to = _parse_date_range(self.request)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        return queryset


@extend_schema(tags=["Admin"])
class AdminStaffViewSet(ReadOnlyEnvelopeViewSet):
    """Read-only on purpose: creating staff and granting privileges stays in
    Django admin, where password and permission handling already live."""

    serializer_class = AdminStaffSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["email", "first_name", "last_name"]

    def get_queryset(self):
        return User.objects.filter(is_staff=True).order_by("-date_joined")


@extend_schema(tags=["Admin"], responses={200: AdminStaffSerializer})
class AdminMeView(APIView):
    """Who am I, and may I be here? The dashboard calls this on boot."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        return success_response(AdminStaffSerializer(request.user).data)
