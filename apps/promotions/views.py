from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView

from apps.core.response import success_response
from apps.promotions.services import get_active_promotions
from apps.promotions.serializers import PromotionSerializer


@extend_schema(tags=["Promotions"])
class ActivePromotionListView(ListAPIView):
    serializer_class = PromotionSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        return get_active_promotions()

    def list(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_queryset(), many=True).data)
