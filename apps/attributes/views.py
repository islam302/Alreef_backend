from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView

from apps.attributes.models import Attribute
from apps.attributes.serializers import AttributeSerializer
from apps.core.response import success_response


@extend_schema(tags=["Products"])
class AttributeListView(ListAPIView):
    queryset = Attribute.objects.filter(is_filterable=True).prefetch_related("values")
    serializer_class = AttributeSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_queryset(), many=True).data)
