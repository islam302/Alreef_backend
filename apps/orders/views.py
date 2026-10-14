from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.views import APIView

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.core.schema import envelope
from apps.orders import services
from apps.orders.models import Order, ReturnRequest
from apps.orders.serializers import (
    GuestOrderLookupSerializer,
    GuestOrderStatusSerializer,
    OrderCancelSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    ReturnRequestCreateSerializer,
    ReturnRequestSerializer,
)


@extend_schema(tags=["Orders"])
class OrderListView(ListAPIView):
    serializer_class = OrderListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related("items")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema(tags=["Orders"])
class OrderDetailView(RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related("items", "status_history")

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)


@extend_schema(tags=["Orders"], request=OrderCancelSerializer, responses=envelope(OrderDetailSerializer, name="OrderCancel"))
class OrderCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_number):
        order = Order.objects.filter(order_number=order_number, user=request.user).first()
        if not order:
            raise ApplicationError(ErrorCodes.ORDER_NOT_FOUND, "Order not found.")
        serializer = OrderCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.payments.services import assert_customer_may_cancel

        assert_customer_may_cancel(order)
        order = services.cancel_order(order, reason=serializer.validated_data["reason"], cancelled_by=request.user)
        return success_response(OrderDetailSerializer(order).data, message="Order cancelled.")


@extend_schema(tags=["Returns"], request=ReturnRequestCreateSerializer, responses=envelope(ReturnRequestSerializer, name="ReturnRequestCreate"))
class ReturnRequestCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_number):
        order = Order.objects.filter(order_number=order_number, user=request.user).first()
        if not order:
            raise ApplicationError(ErrorCodes.ORDER_NOT_FOUND, "Order not found.")
        serializer = ReturnRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        quantities = {entry["order_item_id"]: entry["quantity"] for entry in data["items"]}
        return_request = services.create_return_request(
            order=order, user=request.user, reason=data["reason"], description=data.get("description", ""),
            order_item_quantities=quantities,
        )
        return success_response(ReturnRequestSerializer(return_request).data, message="Return requested.", status=201)


@extend_schema(tags=["Returns"])
class ReturnRequestListView(ListAPIView):
    serializer_class = ReturnRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ReturnRequest.objects.filter(user=self.request.user).prefetch_related("items")

    def list(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_queryset(), many=True).data)


@extend_schema(
    tags=["Orders"], request=GuestOrderLookupSerializer,
    responses=envelope(GuestOrderStatusSerializer, name="GuestOrderLookup"),
)
class GuestOrderLookupView(APIView):
    """
    POST /api/v1/orders/lookup/ — a guest order, for somebody without an account.

    `{"token": "..."}` (what checkout returned) or
    `{"order_number": "...", "phone": "..."}`. A POST so neither key ends up in
    an access log or a Referer header.

    Every miss — unknown number, wrong phone, bad or expired token, an order that
    belongs to an account — is the same 404 with the same message, so the
    endpoint cannot be used to learn which order numbers exist. See
    apps/orders/guest.py for the rest of the reasoning.
    """

    permission_classes = [permissions.AllowAny]
    # Unauthenticated callers guessing at a secret (the phone): its own tight
    # budget, keyed per client IP, on top of the per-order-number cap.
    throttle_scope = "order_lookup"

    def post(self, request):
        from apps.orders import guest

        serializer = GuestOrderLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["token"]:
            order = guest.order_for_token(data["token"])
            by_token = True
        else:
            try:
                order = guest.order_for_number_and_phone(data["order_number"], data["phone"])
            except guest.LookupLocked:
                raise ApplicationError(
                    ErrorCodes.THROTTLED,
                    "Too many attempts for this order number. Please try again later.",
                )
            by_token = False

        if order is None:
            raise ApplicationError(
                ErrorCodes.ORDER_NOT_FOUND,
                "We could not find an order with these details.",
            )

        if by_token:
            # The token is what the payment-return trip carries for a guest, so
            # it is also what may ask the gateway for news — the same
            # reconciliation PaymentStatusView does for an account. Not on the
            # phone path: that would let anybody holding a number and a phone
            # make us call the gateway.
            _reconcile_open_payment(order)

        order = Order.objects.prefetch_related("items", "status_history", "payments").get(pk=order.pk)
        return success_response(GuestOrderStatusSerializer(order).data)


def _reconcile_open_payment(order):
    import logging

    from apps.payments.models import PaymentStatus
    from apps.payments.services import reconcile_payment

    payment = order.payments.order_by("-created_at").first()
    if payment is None or payment.provider == "cod":
        return
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION, PaymentStatus.AUTHORIZED):
        return
    try:
        reconcile_payment(payment)
    except ApplicationError as exc:
        # A gateway that is down must not break the page the customer is
        # waiting on; they see the status we already hold.
        logging.getLogger(__name__).warning("guest lookup: payment %s could not reconcile: %s", payment.id, exc)
