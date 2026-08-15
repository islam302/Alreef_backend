from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.views import APIView

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.orders import services
from apps.orders.models import Order, ReturnRequest
from apps.orders.serializers import (
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


@extend_schema(tags=["Orders"], request=OrderCancelSerializer)
class OrderCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_number):
        order = Order.objects.filter(order_number=order_number, user=request.user).first()
        if not order:
            raise ApplicationError(ErrorCodes.ORDER_NOT_FOUND, "Order not found.")
        serializer = OrderCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.cancel_order(order, reason=serializer.validated_data["reason"], cancelled_by=request.user)
        return success_response(OrderDetailSerializer(order).data, message="Order cancelled.")


@extend_schema(tags=["Returns"], request=ReturnRequestCreateSerializer)
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
