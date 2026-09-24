from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.analytics.serializers import TrackEventSerializer
from apps.analytics.services import get_admin_dashboard_summary, track
from apps.core.response import success_response
from apps.core.schema import envelope


@extend_schema(tags=["Analytics"], request=TrackEventSerializer, responses=envelope(name="TrackEvent"))
class TrackEventView(APIView):
    """Client-side event tracking (spec §54). Fire-and-forget — processed async."""

    permission_classes = [permissions.AllowAny]
    throttle_scope = "analytics"

    def post(self, request):
        serializer = TrackEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # `order_id` arrives from an unauthenticated caller, so it is a claim,
        # not a fact. It used to be stored as given, which let anyone attribute
        # activity to any real order and quietly corrupt the figures the
        # dashboard reports. Nothing is read back out of this endpoint, so it was
        # never a disclosure — but the numbers staff make decisions on are worth
        # the same care as the numbers customers see.
        order_id = self._own_order_id(request, data.get("order_id"))

        track(
            event_type=data["event_type"], request=request, product_id=data.get("product_id"),
            order_id=order_id, metadata=data.get("metadata", {}),
        )
        return success_response(message="Event recorded.", status=202)

    @staticmethod
    def _own_order_id(request, order_id):
        """The order id only if this caller owns it; otherwise nothing.

        Dropped silently rather than refused: this is fire-and-forget telemetry,
        and answering 403 here would turn the endpoint into an oracle for whether
        a given order exists.
        """
        if not order_id:
            return None
        if not request.user.is_authenticated:
            return None
        from apps.orders.models import Order

        if Order.objects.filter(pk=order_id, user=request.user).exists():
            return order_id
        return None


@extend_schema(tags=["Admin"], responses=envelope(name="AdminDashboard"))
class AdminDashboardView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        return success_response(get_admin_dashboard_summary())
