from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.views import APIView

from apps.catalog.models import ProductVariant
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.core.schema import envelope
from apps.subscriptions import services
from apps.subscriptions.models import Subscription, SubscriptionItem
from apps.subscriptions.serializers import SubscriptionCreateSerializer, SubscriptionSerializer
from apps.users.models import Address


@extend_schema(tags=["Subscriptions"])
class SubscriptionListView(ListAPIView):
    serializer_class = SubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user).prefetch_related("items", "schedule_entries")

    def list(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_queryset(), many=True).data)


@extend_schema(tags=["Subscriptions"], request=SubscriptionCreateSerializer, responses=envelope(SubscriptionSerializer, name="SubscriptionCreate"))
class SubscriptionCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SubscriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        address = Address.objects.filter(pk=data["address_id"], user=request.user).first()
        if not address:
            raise ApplicationError(ErrorCodes.ADDRESS_NOT_FOUND, "Address not found.")

        subscription = Subscription.objects.create(
            user=request.user, name=data["name"], address=address, shipping_method_id=data["shipping_method_id"],
            payment_method=data["payment_method"], frequency=data["frequency"],
            custom_interval_days=data.get("custom_interval_days"), next_order_date=data["start_date"],
        )
        for entry in data["items"]:
            variant = ProductVariant.objects.filter(pk=entry["variant_id"], is_active=True).first()
            if not variant:
                raise ApplicationError(ErrorCodes.VARIANT_NOT_FOUND, f"Variant {entry.get('variant_id')} not found.")
            SubscriptionItem.objects.create(subscription=subscription, variant=variant, quantity=entry.get("quantity", 1))

        return success_response(SubscriptionSerializer(subscription).data, message="Subscription created.", status=201)


@extend_schema(tags=["Subscriptions"])
class SubscriptionDetailView(RetrieveAPIView):
    serializer_class = SubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)


def _get_subscription(request, pk):
    subscription = Subscription.objects.filter(pk=pk, user=request.user).first()
    if not subscription:
        raise ApplicationError(ErrorCodes.NOT_FOUND, "Subscription not found.")
    return subscription


@extend_schema(
    # POST with no body: the action is the URL.
    request=None,
    tags=["Subscriptions"], responses=envelope(SubscriptionSerializer, name="SubscriptionPause"),
)
class SubscriptionPauseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        subscription = services.pause_subscription(_get_subscription(request, pk))
        return success_response(SubscriptionSerializer(subscription).data, message="Subscription paused.")


@extend_schema(
    # POST with no body: the action is the URL.
    request=None,
    tags=["Subscriptions"], responses=envelope(SubscriptionSerializer, name="SubscriptionResume"),
)
class SubscriptionResumeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        subscription = services.resume_subscription(_get_subscription(request, pk))
        return success_response(SubscriptionSerializer(subscription).data, message="Subscription resumed.")


@extend_schema(
    # POST with no body: the action is the URL.
    request=None,
    tags=["Subscriptions"], responses=envelope(SubscriptionSerializer, name="SubscriptionCancel"),
)
class SubscriptionCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        subscription = services.cancel_subscription(_get_subscription(request, pk))
        return success_response(SubscriptionSerializer(subscription).data, message="Subscription cancelled.")
