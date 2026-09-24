from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.analytics.serializers import TrackEventSerializer
from apps.analytics.services import get_admin_dashboard_summary, track
from apps.core.response import success_response


@extend_schema(tags=["Analytics"], request=TrackEventSerializer)
class TrackEventView(APIView):
    """Client-side event tracking (spec §54). Fire-and-forget — processed async."""

    permission_classes = [permissions.AllowAny]
    throttle_scope = "analytics"

    def post(self, request):
        serializer = TrackEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        track(
            event_type=data["event_type"], request=request, product_id=data.get("product_id"),
            order_id=data.get("order_id"), metadata=data.get("metadata", {}),
        )
        return success_response(message="Event recorded.", status=202)


@extend_schema(tags=["Admin"])
class AdminDashboardView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        return success_response(get_admin_dashboard_summary())
