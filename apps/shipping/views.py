from djmoney.money import Money
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.views import APIView

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.shipping.models import City, Governorate, Shipment
from apps.shipping.serializers import (
    CitySerializer,
    GovernorateSerializer,
    ShipmentTrackingSerializer,
    ShippingQuoteSerializer,
)
from apps.shipping.services import get_available_quotes


@extend_schema(tags=["Shipping"])
class GovernorateListView(ListAPIView):
    queryset = Governorate.objects.filter(is_active=True)
    serializer_class = GovernorateSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


@extend_schema(tags=["Shipping"], parameters=[OpenApiParameter("governorate", str, OpenApiParameter.QUERY)])
class CityListView(ListAPIView):
    serializer_class = CitySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        qs = City.objects.filter(is_active=True).select_related("governorate")
        governorate_code = self.request.query_params.get("governorate")
        if governorate_code:
            qs = qs.filter(governorate__code=governorate_code)
        return qs


@extend_schema(
    tags=["Shipping"],
    parameters=[
        OpenApiParameter("governorate", str, OpenApiParameter.QUERY, required=True, description="Governorate code"),
        OpenApiParameter("subtotal", str, OpenApiParameter.QUERY, required=True, description="Cart subtotal amount"),
        OpenApiParameter("currency", str, OpenApiParameter.QUERY, required=False, default="EGP"),
        OpenApiParameter("weight_kg", str, OpenApiParameter.QUERY, required=False),
    ],
    responses=ShippingQuoteSerializer(many=True),
)
class ShippingQuoteView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        code = request.query_params.get("governorate")
        governorate = Governorate.objects.filter(code=code, is_active=True).first()
        if not governorate:
            raise ApplicationError(ErrorCodes.SHIPPING_UNAVAILABLE, "Unknown or inactive governorate.")

        subtotal_raw = request.query_params.get("subtotal", "0")
        currency = request.query_params.get("currency", "EGP")
        weight_kg = request.query_params.get("weight_kg")

        subtotal = Money(subtotal_raw, currency)
        quotes = get_available_quotes(governorate=governorate, subtotal=subtotal, weight_kg=weight_kg)
        return success_response(ShippingQuoteSerializer(quotes, many=True).data)


@extend_schema(tags=["Shipping"])
class ShipmentTrackingView(RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "tracking_number"

    def get_queryset(self):
        return Shipment.objects.filter(order__user=self.request.user).prefetch_related("events")

    def retrieve(self, request, *args, **kwargs):
        shipment = self.get_object()
        data = {
            "tracking_number": shipment.tracking_number,
            "carrier_code": shipment.carrier_code,
            "status": shipment.status,
            "estimated_delivery": shipment.estimated_delivery,
            "delivered_at": shipment.delivered_at,
            "events": shipment.events.all(),
        }
        return success_response(ShipmentTrackingSerializer(data).data)
